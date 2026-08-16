# pyright: reportMissingImports=false
# amplifier-agent (amplifier_agent_cli / amplifier_agent_lib) is an OPTIONAL
# dependency (see pyproject.toml's `agent` extra) -- this suite's baseline
# CI/DTU environment installs it (embedded mode is the default), but a
# host running `python_check` without the extra cannot resolve these
# imports. Same suppression convention as runner.py/credentials.py.
"""Tests for the agent provider credential path (Settings -> Agent).

The embedded path is THE path -- the sidecar (a separate
`amplifier-agent serve chat-completions` OS process) was removed once the
embedded path was proven durable across a restart with a real browser.
Every test here exercises `muxplex.agent_embedded.credentials` and the
`GET`/`POST /api/agent/provider-credential` endpoints directly, in-process.

Three resolution branches, mirroring the owner's explicit acceptance
criteria ("I'd prefer to read env first, do it right"):

  1. Env var set -> resolved from "env"; a POST is a no-op-with-explanation.
  2. No env var, a valid key saved via POST -> persisted to the credentials
     file; resolved from "file" on the next read (this IS the durability
     proof at the unit level -- the DTU proof additionally restarts the
     whole muxplex process and re-checks).
  3. No env var, a bad key submitted -> rejected, nothing persisted.

Also pins the invariants that hold regardless of transport (SS7.3/SS9 of
docs/designs/agent-credentials.md, the design doc written for the
sidecar's credential flow but whose invariants -- no `endpoint` field, a
closed provider allowlist, validate-before-persist -- apply unchanged
here): the request schema shape and the provider allowlist.

Every test isolates `AMPLIFIER_AGENT_HOME` to a per-test tmp_path and
clears the provider env vars by default, so no test can ever read or
write a real user's `~/.amplifier-agent/credentials.json`.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from muxplex.agent_embedded import credentials as agent_embedded_credentials
from muxplex.agent_embedded import runner as agent_embedded_runner
from muxplex.auth import create_session_cookie
from muxplex.main import (
    _AGENT_CREDENTIAL_ALLOWED_PROVIDERS,
    ProviderCredentialRequest,
    _auth_secret,
    _auth_ttl,
    app,
)


def _authed_client() -> TestClient:
    """A TestClient with a valid session cookie (non-localhost address --
    matches test_agent_credential.py's identical helper)."""
    cookie = create_session_cookie(_auth_secret, _auth_ttl)
    client = TestClient(app, base_url="http://192.168.1.1")
    client.cookies.set("muxplex_session", cookie)
    return client


@pytest.fixture(autouse=True)
def _isolate_agent_credentials(tmp_path, monkeypatch):
    """Every test in this file gets its own throwaway
    `$AMPLIFIER_AGENT_HOME` (so `credentials.persist_key`/`resolve_status`
    can never touch a real user's credentials file) and starts with every
    allowed provider's env var cleared, so ambient host state can never
    leak into a resolution-order assertion.
    """
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    yield


@pytest.fixture(autouse=True)
def _force_embedded_mode(monkeypatch):
    """Pin embedded mode explicitly for every test in this file, rather
    than relying on it merely being the current default -- a future
    default flip must not silently change which branch these tests
    exercise (mirrors test_agent_credential.py's `_force_sidecar_mode`
    fixture, inverted)."""
    monkeypatch.setattr("muxplex.agent_embedded.is_embedded_mode", lambda: True)


@pytest.fixture(autouse=True)
def _assume_library_available(monkeypatch):
    """Pin `library_unavailable_reason()` to "available" for every test
    here -- this suite is about credential resolution/persistence, not
    about the (separately testable) "amplifier-agent isn't installed at
    all" gap. Tests that specifically need the library-missing branch
    override this locally.
    """

    async def _available():
        return None

    monkeypatch.setattr(agent_embedded_runner, "library_unavailable_reason", _available)


# ---------------------------------------------------------------------------
# Schema / allowlist invariants (docs/designs/agent-credentials.md
# SS7.3/SS9) -- these hold regardless of transport, and held for the
# sidecar path too before it was removed.
# ---------------------------------------------------------------------------


def test_request_model_has_no_endpoint_field():
    """The request schema must not carry an `endpoint` field -- not
    ignored, not validated, ABSENT. Adding one later for Azure support
    means designing an endpoint allowlist first; this test makes that a
    deliberate, visible act instead of an accidental one."""
    fields = ProviderCredentialRequest.model_fields
    assert "endpoint" not in fields
    assert set(fields) == {"provider", "api_key"}


def test_allowlist_is_exactly_anthropic_and_openai():
    """azure-openai and ollama carry a caller-controlled endpoint/host the
    resolver would feed straight to the provider; github-copilot is
    environment-only. Also pins that `agent_embedded.credentials`'s own
    copy of this set (duplicated so that module has no import dependency
    on main.py -- see its docstring) never drifts from this one."""
    assert _AGENT_CREDENTIAL_ALLOWED_PROVIDERS == frozenset({"anthropic", "openai"})
    assert (
        agent_embedded_credentials.ALLOWED_PROVIDERS
        == _AGENT_CREDENTIAL_ALLOWED_PROVIDERS
    )


@pytest.mark.parametrize(
    "provider", ["azure-openai", "ollama", "github-copilot", "made-up-provider"]
)
def test_post_rejects_non_allowlisted_providers(provider, monkeypatch):
    """A disallowed provider is refused with 400 BEFORE any validation is
    attempted -- validation/persistence must never even be attempted."""
    called = {"validate": False}

    async def _fail_if_called(*_a, **_kw):
        called["validate"] = True
        raise AssertionError("must not validate a disallowed provider")

    monkeypatch.setattr(agent_embedded_credentials, "validate_key", _fail_if_called)

    client = _authed_client()
    resp = client.post(
        "/api/agent/provider-credential",
        json={"provider": provider, "api_key": "sk-whatever"},
    )
    assert resp.status_code == 400
    assert not called["validate"]
    # No key material echoed back in the error body either.
    assert "sk-whatever" not in resp.text


def test_post_endpoint_field_is_silently_dropped_not_an_error(monkeypatch):
    """Sending an `endpoint` field in the body is simply not part of the
    schema -- FastAPI/pydantic drops unknown fields by default, so it must
    never reach the resolver. This pins that no code path forwards it."""
    _patch_provider_loading(
        monkeypatch,
        provider_instance=_FakeProvider(models=[_FakeModel("claude-sonnet-5")]),
    )
    client = _authed_client()
    resp = client.post(
        "/api/agent/provider-credential",
        json={
            "provider": "anthropic",
            "api_key": "sk-real-key-for-endpoint-test",
            "endpoint": "https://evil.example.com",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# ---------------------------------------------------------------------------
# credentials.resolve_status -- the read side (env-first, already correct
# via resolve_credential_detailed; these tests pin that it stays that way).
# ---------------------------------------------------------------------------


def test_resolve_status_reports_not_set_when_nothing_configured():
    status = agent_embedded_credentials.resolve_status("anthropic")
    assert status["source"] == "not_set"
    assert status["masked"] is None
    assert status["env_var"] == "ANTHROPIC_API_KEY"


def test_resolve_status_reports_env_and_masks_the_value(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-abcdefghijklmnop")
    status = agent_embedded_credentials.resolve_status("anthropic")
    assert status["source"] == "env"
    assert status["masked"] == "sk-ant...mnop"
    assert "sk-ant-abcdefghijklmnop" not in status["masked"]


def test_resolve_status_reports_file_when_only_stored(tmp_path):
    creds_path = tmp_path / "credentials.json"
    creds_path.write_text(
        json.dumps(
            {
                "version": 1,
                "providers": {"anthropic": {"api_key": "sk-ant-storedkey123"}},
            }
        )
    )
    status = agent_embedded_credentials.resolve_status("anthropic")
    assert status["source"] == "file"
    assert status["masked"] == "sk-ant...y123"


def test_resolve_status_env_wins_over_file(tmp_path, monkeypatch):
    """The owner's explicit instruction: env first, always -- even when a
    (different) value is also on disk."""
    creds_path = tmp_path / "credentials.json"
    creds_path.write_text(
        json.dumps(
            {
                "version": 1,
                "providers": {"anthropic": {"api_key": "sk-ant-storedkey123"}},
            }
        )
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-envkey456789")
    status = agent_embedded_credentials.resolve_status("anthropic")
    assert status["source"] == "env"
    assert status["masked"] == "sk-ant...6789"


# ---------------------------------------------------------------------------
# credentials.persist_key -- the durable-store write side.
# ---------------------------------------------------------------------------


def test_persist_key_writes_the_same_file_the_library_reads(tmp_path):
    path = agent_embedded_credentials.persist_key("anthropic", "sk-ant-realvalue1234")
    assert path == tmp_path / "credentials.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["providers"]["anthropic"]["api_key"] == "sk-ant-realvalue1234"
    # 0600, matching the library's own convention (docs/designs/agent-credentials.md SS3.2).
    assert (path.stat().st_mode & 0o777) == 0o600
    # Immediately resolvable by the SAME chain a real turn uses -- this is
    # the "one mechanism, not two" property: no separate muxplex-owned store.
    status = agent_embedded_credentials.resolve_status("anthropic")
    assert status["source"] == "file"


def test_persist_key_preserves_other_providers(tmp_path):
    agent_embedded_credentials.persist_key("anthropic", "sk-ant-first-value12")
    agent_embedded_credentials.persist_key("openai", "sk-openai-secondvalue")
    data = json.loads((tmp_path / "credentials.json").read_text())
    assert data["providers"]["anthropic"]["api_key"] == "sk-ant-first-value12"
    assert data["providers"]["openai"]["api_key"] == "sk-openai-secondvalue"


# ---------------------------------------------------------------------------
# credentials.validate_key -- real (mocked-at-the-provider-boundary) calls.
# ---------------------------------------------------------------------------


class _FakeModel:
    def __init__(self, model_id: str) -> None:
        self.id = model_id


class _FakeProvider:
    def __init__(self, *, models=None, error: Exception | None = None) -> None:
        self._models = models or []
        self._error = error
        self.closed = False

    async def list_models(self):
        if self._error is not None:
            raise self._error
        return self._models

    async def close(self):
        self.closed = True


def _patch_provider_loading(
    monkeypatch, *, provider_instance=None, module_import_error=False
):
    """Patch the three amplifier_agent_cli.admin.models internals
    validate_key reaches into, so no real network call or real provider
    package is required for these tests."""
    import amplifier_agent_cli.admin.models as models_mod

    if module_import_error:

        def _raise(_provider):
            raise ImportError("provider module not installed")

        monkeypatch.setattr(models_mod, "_load_provider_module", _raise)
        return

    monkeypatch.setattr(models_mod, "_load_provider_module", lambda _p: object())
    monkeypatch.setattr(models_mod, "load_provider_class", lambda _p: object)
    monkeypatch.setattr(
        models_mod,
        "_try_instantiate_provider",
        lambda _cls, credentials=None, extra_config=None: provider_instance,
    )


async def test_validate_key_ok_on_real_models_returned(monkeypatch):
    _patch_provider_loading(
        monkeypatch,
        provider_instance=_FakeProvider(models=[_FakeModel("claude-sonnet-5")]),
    )
    verdict, detail = await agent_embedded_credentials.validate_key(
        "anthropic", "sk-good"
    )
    assert verdict == "ok"
    assert "1 model" in detail


async def test_validate_key_bad_key_on_authentication_error(monkeypatch):
    _patch_provider_loading(
        monkeypatch,
        provider_instance=_FakeProvider(
            error=RuntimeError(
                "AuthenticationError: Error code: 401 - API key is invalid."
            )
        ),
    )
    verdict, detail = await agent_embedded_credentials.validate_key(
        "anthropic", "sk-bad"
    )
    assert verdict == "bad_key"
    assert "401" in detail or "invalid" in detail.lower()


async def test_validate_key_unreachable_on_connection_error(monkeypatch):
    _patch_provider_loading(
        monkeypatch,
        provider_instance=_FakeProvider(error=ConnectionError("Connection timed out")),
    )
    verdict, detail = await agent_embedded_credentials.validate_key(
        "anthropic", "sk-whatever"
    )
    assert verdict == "unreachable"
    assert "timed out" in detail.lower()


async def test_validate_key_module_missing(monkeypatch):
    _patch_provider_loading(monkeypatch, module_import_error=True)
    verdict, _detail = await agent_embedded_credentials.validate_key(
        "anthropic", "sk-whatever"
    )
    assert verdict == "module_missing"


async def test_validate_key_never_persists_anything(monkeypatch, tmp_path):
    """validate_key must be side-effect-free on the credentials file --
    persistence is a SEPARATE, explicit step (persist_key), only reached
    after an "ok" verdict."""
    _patch_provider_loading(
        monkeypatch,
        provider_instance=_FakeProvider(
            error=RuntimeError("AuthenticationError: 401 invalid")
        ),
    )
    await agent_embedded_credentials.validate_key("anthropic", "sk-bad")
    assert not (tmp_path / "credentials.json").exists()


# ---------------------------------------------------------------------------
# credentials.full_status -- gating truth, anchored on the runner's own
# active provider (anthropic), not "any allowlisted provider".
# ---------------------------------------------------------------------------


async def test_full_status_not_installed_when_library_unavailable(monkeypatch):
    async def _unavailable():
        return "amplifier-agent is not installed in this Python environment"

    monkeypatch.setattr(
        agent_embedded_runner, "library_unavailable_reason", _unavailable
    )
    status = await agent_embedded_credentials.full_status()
    assert status["state"] == "not_installed"
    assert status["providers"] == {}


async def test_full_status_not_configured_when_nothing_resolves():
    status = await agent_embedded_credentials.full_status()
    assert status["state"] == "not_configured"
    assert status["providers"]["anthropic"]["source"] == "not_set"
    assert status["providers"]["openai"]["source"] == "not_set"
    assert status["sidecar"] == "running"
    assert status["mode"] == "embedded"


async def test_full_status_configured_from_file():
    agent_embedded_credentials.persist_key("anthropic", "sk-ant-storedvalue123")
    status = await agent_embedded_credentials.full_status()
    assert status["state"] == "configured"
    assert status["providers"]["anthropic"]["source"] == "file"


async def test_full_status_configured_shadowed_when_env_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fromenv1234567")
    status = await agent_embedded_credentials.full_status()
    assert status["state"] == "configured_shadowed"
    assert status["providers"]["anthropic"]["source"] == "env"


async def test_full_status_gating_ignores_a_non_active_provider(monkeypatch):
    """A stored/env openai key must NOT flip the gate open -- the embedded
    runner only ever mounts `active_provider()` (anthropic) for a turn.
    This is the regression this design explicitly guards against (see
    `credentials.full_status`'s docstring)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-onlythisisset")
    status = await agent_embedded_credentials.full_status()
    assert status["state"] == "not_configured"
    assert status["providers"]["openai"]["source"] == "env"
    assert status["providers"]["anthropic"]["source"] == "not_set"


# ---------------------------------------------------------------------------
# HTTP layer: GET /api/agent/provider-credential (embedded branch).
# ---------------------------------------------------------------------------


def test_get_embedded_not_configured():
    client = _authed_client()
    resp = client.get("/api/agent/provider-credential")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "not_configured"
    assert body["mode"] == "embedded"


def test_get_embedded_configured_shadowed_when_env_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fromenv1234567")
    client = _authed_client()
    resp = client.get("/api/agent/provider-credential")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "configured_shadowed"
    assert body["providers"]["anthropic"]["source"] == "env"
    # The real value must never appear anywhere in the response.
    assert "sk-ant-fromenv1234567" not in resp.text


# ---------------------------------------------------------------------------
# HTTP layer: POST /api/agent/provider-credential (embedded branch) -- the
# three resolution branches, end to end through the real FastAPI route.
# ---------------------------------------------------------------------------


def test_post_embedded_env_set_is_a_no_op(monkeypatch, tmp_path):
    """Branch 1: env var set -> a UI save is a no-op-with-explanation.
    Nothing is written to disk."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fromenv1234567")
    client = _authed_client()
    resp = client.post(
        "/api/agent/provider-credential",
        json={"provider": "anthropic", "api_key": "sk-ant-submittedbyuser"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["no_op"] is True
    assert "ANTHROPIC_API_KEY" in body["detail"]
    assert not (tmp_path / "credentials.json").exists()
    # Neither the env value nor the submitted (rejected) value ever appear.
    assert "sk-ant-fromenv1234567" not in resp.text
    assert "sk-ant-submittedbyuser" not in resp.text


def test_post_embedded_valid_key_persists_and_is_then_resolvable(monkeypatch, tmp_path):
    """Branch 2: no env var, a VALID key is validated then persisted to
    the durable store, and is immediately resolvable by the exact chain a
    real turn uses -- the unit-level half of the restart-durability proof
    (the DTU proof additionally restarts the whole process)."""
    _patch_provider_loading(
        monkeypatch,
        provider_instance=_FakeProvider(models=[_FakeModel("claude-sonnet-5")]),
    )
    client = _authed_client()
    resp = client.post(
        "/api/agent/provider-credential",
        json={"provider": "anthropic", "api_key": "sk-ant-goodvalue12345"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body.get("no_op") is False
    assert body["restarted"] is False
    assert "no restart needed" in body["detail"]
    assert "sk-ant-goodvalue12345" not in resp.text

    data = json.loads((tmp_path / "credentials.json").read_text())
    assert data["providers"]["anthropic"]["api_key"] == "sk-ant-goodvalue12345"

    status = agent_embedded_credentials.resolve_status("anthropic")
    assert status["source"] == "file"


def test_post_embedded_bad_key_rejected_and_never_persisted(monkeypatch, tmp_path):
    """Branch 3: no env var, a BAD key is rejected with the real provider
    error, and nothing is written -- validate-before-persist, unchanged
    from the sidecar's own invariant (SS0/SS3.3)."""
    _patch_provider_loading(
        monkeypatch,
        provider_instance=_FakeProvider(
            error=RuntimeError(
                'AuthenticationError: Error code: 401 - "API key is invalid."'
            )
        ),
    )
    client = _authed_client()
    resp = client.post(
        "/api/agent/provider-credential",
        json={"provider": "anthropic", "api_key": "sk-ant-badvalue123456"},
    )
    assert resp.status_code == 400
    assert "invalid" in resp.text.lower()
    assert not (tmp_path / "credentials.json").exists()
    assert "sk-ant-badvalue123456" not in resp.text


def test_post_embedded_unreachable_is_502_and_never_persisted(monkeypatch, tmp_path):
    _patch_provider_loading(
        monkeypatch,
        provider_instance=_FakeProvider(error=ConnectionError("Connection timed out")),
    )
    client = _authed_client()
    resp = client.post(
        "/api/agent/provider-credential",
        json={"provider": "anthropic", "api_key": "sk-ant-whatever12345"},
    )
    assert resp.status_code == 502
    assert not (tmp_path / "credentials.json").exists()


def test_post_embedded_rejects_non_allowlisted_provider_before_any_validation(
    monkeypatch,
):
    called = {"validate": False}

    async def _fail_if_called(*_a, **_kw):
        called["validate"] = True
        raise AssertionError("must not validate a disallowed provider")

    monkeypatch.setattr(agent_embedded_credentials, "validate_key", _fail_if_called)

    client = _authed_client()
    resp = client.post(
        "/api/agent/provider-credential",
        json={"provider": "ollama", "api_key": "sk-whatever"},
    )
    assert resp.status_code == 400
    assert not called["validate"]


def test_post_embedded_never_shells_out(monkeypatch):
    """No subprocess of any kind may be spawned by the embedded POST path
    -- the whole point of embedded mode is that sudo/aa-svc/systemctl are
    gone."""

    async def _fail_if_spawned(*_a, **_kw):
        raise AssertionError("embedded POST must never spawn a subprocess")

    monkeypatch.setattr("asyncio.create_subprocess_exec", _fail_if_spawned)
    _patch_provider_loading(
        monkeypatch,
        provider_instance=_FakeProvider(models=[_FakeModel("claude-sonnet-5")]),
    )
    client = _authed_client()
    resp = client.post(
        "/api/agent/provider-credential",
        json={"provider": "anthropic", "api_key": "sk-ant-goodvalue12345"},
    )
    assert resp.status_code == 200
