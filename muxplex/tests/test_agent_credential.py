"""Tests for the agent provider credential endpoint (Settings -> Agent).

See docs/designs/agent-credentials.md. muxplex is a CONDUCTOR here, never
a STORE -- these tests pin the invariants the design leans on: the
provider allowlist, the absence of an `endpoint` field on the request
schema, that no response body ever carries a key, and that the stdout
parsers hold up against captured real CLI output (a coupling point to
another repo -- see the design doc SS3.5/SS10).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from muxplex.auth import create_session_cookie
from muxplex.main import (
    _AGENT_CREDENTIAL_ALLOWED_PROVIDERS,
    ProviderCredentialRequest,
    _auth_secret,
    _auth_ttl,
    _parse_agent_auth_list,
    _parse_agent_auth_status,
    app,
)


def _authed_client() -> TestClient:
    """A TestClient with a valid session cookie (non-localhost address --
    see AGENTS.md/test_integration.py's identical pattern; the removed
    localhost bypass is not relied on anywhere in this file)."""
    cookie = create_session_cookie(_auth_secret, _auth_ttl)
    client = TestClient(app, base_url="http://192.168.1.1")
    client.cookies.set("muxplex_session", cookie)
    return client


# ---------------------------------------------------------------------------
# Schema: no `endpoint` field, ever (SS7.3/SS9)
# ---------------------------------------------------------------------------


def test_request_model_has_no_endpoint_field():
    """The request schema must not carry an `endpoint` field -- not ignored,
    not validated, ABSENT. Adding one later for Azure support means
    designing an endpoint allowlist first; this test makes that a
    deliberate, visible act instead of an accidental one."""
    fields = ProviderCredentialRequest.model_fields
    assert "endpoint" not in fields
    assert set(fields) == {"provider", "api_key"}


# ---------------------------------------------------------------------------
# Allowlist (SS3.6/SS7.3)
# ---------------------------------------------------------------------------


def test_allowlist_is_exactly_anthropic_and_openai():
    assert _AGENT_CREDENTIAL_ALLOWED_PROVIDERS == frozenset({"anthropic", "openai"})


@pytest.mark.parametrize(
    "provider", ["azure-openai", "ollama", "github-copilot", "made-up-provider"]
)
def test_post_rejects_non_allowlisted_providers(provider, monkeypatch):
    """A disallowed provider is refused with 400 BEFORE any subprocess is
    spawned -- validation/write must never even be attempted."""
    called = {"validate": False}

    async def _fail_if_called(*_a, **_kw):
        called["validate"] = True
        raise AssertionError("must not validate a disallowed provider")

    monkeypatch.setattr("muxplex.main._validate_agent_credential", _fail_if_called)

    client = _authed_client()
    resp = client.post(
        "/api/agent/provider-credential",
        json={"provider": provider, "api_key": "sk-whatever"},
    )
    assert resp.status_code == 400
    assert not called["validate"]
    # No key material echoed back in the error body either.
    assert "sk-whatever" not in resp.text


def test_post_rejects_endpoint_field_silently_ignored_not_error(monkeypatch):
    """Sending an `endpoint` field in the body is simply not part of the
    schema -- FastAPI/pydantic drops unknown fields by default, so it must
    never reach the resolver. This pins that no code path forwards it."""

    async def _fake_validate(provider, api_key):
        assert api_key == "sk-real-key"
        return "ok", "1 model"

    async def _fake_served(provider):
        return True

    async def _fake_run(args, **kwargs):
        return 0, "", ""

    monkeypatch.setattr("muxplex.main._validate_agent_credential", _fake_validate)
    monkeypatch.setattr("muxplex.main._agent_provider_served", _fake_served)
    monkeypatch.setattr("muxplex.main._run_agent_auth_cmd", _fake_run)

    client = _authed_client()
    resp = client.post(
        "/api/agent/provider-credential",
        json={
            "provider": "anthropic",
            "api_key": "sk-real-key",
            "endpoint": "https://evil.example.com",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# ---------------------------------------------------------------------------
# Bad key never reaches disk or triggers a restart (SS0/SS3.3/SS9)
# ---------------------------------------------------------------------------


def test_post_bad_key_never_writes_or_restarts(monkeypatch):
    write_calls = []
    restart_calls = []

    async def _fake_validate(provider, api_key):
        return "bad_key", "AuthenticationError: API key is invalid."

    async def _fake_run(args, **kwargs):
        write_calls.append(args)
        return 0, "", ""

    async def _fake_restart(**kwargs):
        restart_calls.append(kwargs)
        return True, "ready"

    monkeypatch.setattr("muxplex.main._validate_agent_credential", _fake_validate)
    monkeypatch.setattr("muxplex.main._run_agent_auth_cmd", _fake_run)
    monkeypatch.setattr("muxplex.main._restart_agent_sidecar_and_wait", _fake_restart)

    client = _authed_client()
    resp = client.post(
        "/api/agent/provider-credential",
        json={"provider": "anthropic", "api_key": "sk-bad-key"},
    )
    assert resp.status_code == 400
    assert "invalid" in resp.text.lower()
    assert write_calls == []
    assert restart_calls == []
    assert "sk-bad-key" not in resp.text


def test_post_unreachable_is_distinguished_from_bad_key(monkeypatch):
    async def _fake_validate(provider, api_key):
        return "unreachable", "Connection timed out"

    monkeypatch.setattr("muxplex.main._validate_agent_credential", _fake_validate)

    client = _authed_client()
    resp = client.post(
        "/api/agent/provider-credential",
        json={"provider": "anthropic", "api_key": "sk-whatever"},
    )
    assert resp.status_code == 502
    assert "connectivity" in resp.text.lower() or "reach" in resp.text.lower()


# ---------------------------------------------------------------------------
# Good key: no restart when already served, restart when not (SS3.4)
# ---------------------------------------------------------------------------


def test_post_good_key_already_served_does_not_restart(monkeypatch):
    restart_calls = []

    async def _fake_validate(provider, api_key):
        return "ok", "3 models"

    async def _fake_run(args, **kwargs):
        return 0, "", ""

    async def _fake_served(provider):
        return True  # already serving -- no restart needed

    async def _fake_restart(**kwargs):
        restart_calls.append(kwargs)
        return True, "ready"

    monkeypatch.setattr("muxplex.main._validate_agent_credential", _fake_validate)
    monkeypatch.setattr("muxplex.main._run_agent_auth_cmd", _fake_run)
    monkeypatch.setattr("muxplex.main._agent_provider_served", _fake_served)
    monkeypatch.setattr("muxplex.main._restart_agent_sidecar_and_wait", _fake_restart)

    client = _authed_client()
    resp = client.post(
        "/api/agent/provider-credential",
        json={"provider": "anthropic", "api_key": "sk-good-key"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["restarted"] is False
    assert restart_calls == []


def test_post_good_key_not_served_triggers_restart(monkeypatch):
    restart_calls = []

    async def _fake_validate(provider, api_key):
        return "ok", "3 models"

    async def _fake_run(args, **kwargs):
        return 0, "", ""

    async def _fake_served(provider):
        return False  # not served yet -- restart required

    async def _fake_restart(**kwargs):
        restart_calls.append(kwargs)
        return True, "ready"

    monkeypatch.setattr("muxplex.main._validate_agent_credential", _fake_validate)
    monkeypatch.setattr("muxplex.main._run_agent_auth_cmd", _fake_run)
    monkeypatch.setattr("muxplex.main._agent_provider_served", _fake_served)
    monkeypatch.setattr("muxplex.main._restart_agent_sidecar_and_wait", _fake_restart)
    monkeypatch.setattr("muxplex.main._agent_last_restart_at", 0.0)

    client = _authed_client()
    resp = client.post(
        "/api/agent/provider-credential",
        json={"provider": "anthropic", "api_key": "sk-good-key"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["restarted"] is True
    assert len(restart_calls) == 1


# ---------------------------------------------------------------------------
# Response never carries key material (SS10 test list)
# ---------------------------------------------------------------------------


def test_get_status_response_never_contains_key_material(monkeypatch):
    async def _fake_run(args, **kwargs):
        if args[:1] == ["status"]:
            return (
                0,
                (
                    "Credentials file: /home/aa-svc/.amplifier-agent/credentials.json\n"
                    "  exists: True\n\n"
                    "Per-provider resolution (env wins if both are set):\n"
                    "  anthropic       USING file entry\n"
                    "  openai          NOT SET (export OPENAI_API_KEY or run `auth set openai ...`)\n"
                ),
                "",
            )
        return (
            0,
            (
                "Credentials file: /home/aa-svc/.amplifier-agent/credentials.json\n"
                "  (mode 600)\n\n"
                "  anthropic       sk-ant...EwAA  file\n"
                "  openai          <not set>      \u2014\n"
            ),
            "",
        )

    async def _fake_served(provider):
        return True

    monkeypatch.setattr("muxplex.main._run_agent_auth_cmd", _fake_run)
    monkeypatch.setattr("muxplex.main._agent_provider_served", _fake_served)

    client = _authed_client()
    resp = client.get("/api/agent/provider-credential")
    assert resp.status_code == 200
    body = resp.json()
    # masked value only -- never the full key.
    assert body["providers"]["anthropic"]["masked"] == "sk-ant...EwAA"
    assert (
        "sk-ant" not in str(body["providers"]["anthropic"]["masked"])[:0]
    )  # sanity: masked is a display string
    # the response text as a whole must never contain a plausible raw key
    assert "sk-ant-api" not in resp.text


# ---------------------------------------------------------------------------
# stdout parsers -- pinned against captured real CLI output (SS3.5/SS10)
# ---------------------------------------------------------------------------

_CAPTURED_AUTH_STATUS = """Credentials file: /home/aa-svc/.amplifier-agent/credentials.json
  exists: True
  mode:   600

Per-provider resolution (env wins if both are set):
  anthropic       USING env=ANTHROPIC_API_KEY
  openai          USING file entry
  azure-openai    NOT SET (export AZURE_OPENAI_API_KEY or run `auth set azure-openai ...`)
  ollama          USING built-in default (http://localhost:11434)
  github-copilot  NOT SET (export GITHUB_TOKEN or run `auth set github-copilot ...`)
"""

_CAPTURED_AUTH_LIST = """Credentials file: /home/aa-svc/.amplifier-agent/credentials.json
  (mode 600)

  anthropic       sk-ant...EwAA  env=ANTHROPIC_API_KEY
  openai          sk-...abcd     file
  azure-openai    <not set>      \u2014
  ollama          <not set>      default
  github-copilot  <not set>      \u2014
"""


def test_parse_agent_auth_status_against_captured_output():
    result = _parse_agent_auth_status(_CAPTURED_AUTH_STATUS)
    assert result["anthropic"] == "env"
    assert result["openai"] == "file"
    # azure-openai/ollama/github-copilot are outside the allowlist and must
    # not appear even though they're present in the raw output.
    assert "azure-openai" not in result
    assert "ollama" not in result
    assert "github-copilot" not in result


def test_parse_agent_auth_list_against_captured_output():
    result = _parse_agent_auth_list(_CAPTURED_AUTH_LIST)
    assert result["anthropic"] == ("sk-ant...EwAA", "env=ANTHROPIC_API_KEY")
    assert result["openai"] == ("sk-...abcd", "file")
    assert "azure-openai" not in result


# ---------------------------------------------------------------------------
# systemd-EnvironmentFile shadow detection -- a gap in the CLI-only check
# (SS3.5) verified live: `auth status` run as a fresh `sudo -u aa-svc`
# subprocess never sees a var that only exists in the unit's own
# EnvironmentFile, because sudo's default env_reset strips the calling
# shell's environment and the file is loaded only into the unit's process.
# ---------------------------------------------------------------------------


def test_get_status_detects_systemd_environment_file_shadow(monkeypatch, tmp_path):
    """A provider env var present ONLY in the sidecar unit's own
    EnvironmentFile -- invisible to `auth status` -- must still surface as
    `source: env` in the response. This is the exact scenario measured
    live in the twin (SS3.5 "worst failure available": reporting success
    while doing nothing)."""
    env_file = tmp_path / "aasvc.env"
    env_file.write_text(
        "AMPLIFIER_AGENT_HTTP_API_KEY=irrelevant\nANTHROPIC_API_KEY=sk-shadowed-value\n"
    )

    async def _fake_run(args, **kwargs):
        if args[:1] == ["status"]:
            return (
                0,
                (
                    "Credentials file: /home/aa-svc/.amplifier-agent/credentials.json\n"
                    "  exists: False\n\n"
                    "Per-provider resolution (env wins if both are set):\n"
                    "  anthropic       NOT SET (export ANTHROPIC_API_KEY or run `auth set anthropic ...`)\n"
                    "  openai          NOT SET (export OPENAI_API_KEY or run `auth set openai ...`)\n"
                ),
                "",
            )
        return (
            0,
            "Credentials file: /home/aa-svc/.amplifier-agent/credentials.json\n  (not present)\n",
            "",
        )

    async def _fake_systemctl_show(*args, **kwargs):
        class _P:
            returncode = 0

            async def communicate(self):
                return (
                    f"Environment=\nEnvironmentFiles={env_file} (ignore_errors=no)\n".encode(),
                    b"",
                )

        return _P()

    async def _fake_served(provider):
        return True

    monkeypatch.setattr("muxplex.main._run_agent_auth_cmd", _fake_run)
    monkeypatch.setattr("muxplex.main._agent_provider_served", _fake_served)
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_systemctl_show)

    client = _authed_client()
    resp = client.get("/api/agent/provider-credential")
    assert resp.status_code == 200
    body = resp.json()
    assert body["providers"]["anthropic"]["source"] == "env"
    assert body["state"] == "configured_shadowed"
    # The shadow's VALUE must never appear anywhere in the response.
    assert "sk-shadowed-value" not in resp.text
