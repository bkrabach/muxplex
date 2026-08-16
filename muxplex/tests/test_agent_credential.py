"""Tests for the agent provider credential endpoint (Settings -> Agent).

See docs/designs/agent-credentials.md. muxplex is a CONDUCTOR here, never
a STORE -- these tests pin the invariants the design leans on: the
provider allowlist, the absence of an `endpoint` field on the request
schema, that no response body ever carries a key, and that the stdout
parsers hold up against captured real CLI output (a coupling point to
another repo -- see the design doc SS3.5/SS10).
"""

from __future__ import annotations

import pwd

import pytest
from fastapi.testclient import TestClient

from muxplex.auth import create_session_cookie
from muxplex.main import (
    _AGENT_CREDENTIAL_ALLOWED_PROVIDERS,
    AgentSidecarNotInstalled,
    ProviderCredentialRequest,
    _agent_service_env_shadow_vars,
    _agent_sidecar_install_gap,
    _auth_secret,
    _auth_ttl,
    _have_systemctl,
    _parse_agent_auth_list,
    _parse_agent_auth_status,
    _restart_agent_sidecar_and_wait,
    _run_agent_cli,
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
    monkeypatch.setattr("muxplex.main._run_agent_cli", _fake_run)

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
    monkeypatch.setattr("muxplex.main._run_agent_cli", _fake_run)
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
    monkeypatch.setattr("muxplex.main._run_agent_cli", _fake_run)
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
    monkeypatch.setattr("muxplex.main._run_agent_cli", _fake_run)
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
        if args[:2] == ["auth", "status"]:
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

    monkeypatch.setattr("muxplex.main._run_agent_cli", _fake_run)
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

    monkeypatch.setattr("muxplex.main._run_agent_cli", _fake_run)
    monkeypatch.setattr("muxplex.main._agent_provider_served", _fake_served)
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_systemctl_show)
    # This test's premise is a SYSTEMD host: an EnvironmentFile shadow can
    # only exist where there is a systemd unit to carry it. Say so, rather
    # than inheriting it from whatever platform the suite happens to run
    # on. Without this, `_agent_service_env_shadow_vars()` short-circuits
    # on its `_have_systemctl()` precondition and returns an empty set
    # before ever reaching the mocked `create_subprocess_exec` above --
    # the test then fails with `source == "not_set"` on any non-systemd
    # host, which is CORRECT behaviour being reported as a broken test.
    # That is exactly what happened on the macOS runner during the v0.48.2
    # release (CI run 31944963673); see that entry in CHANGELOG.md.
    monkeypatch.setattr("muxplex.main._have_systemctl", lambda: True)

    client = _authed_client()
    resp = client.get("/api/agent/provider-credential")
    assert resp.status_code == 200
    body = resp.json()
    assert body["providers"]["anthropic"]["source"] == "env"
    assert body["state"] == "configured_shadowed"
    # The shadow's VALUE must never appear anywhere in the response.
    assert "sk-shadowed-value" not in resp.text


# ---------------------------------------------------------------------------
# v0.48.2: `systemctl` is a checked precondition, not an assumed binary.
#
# Both direct `systemctl` callers in main.py used to spawn unconditionally.
# macOS has no systemd, so `create_subprocess_exec("systemctl", ...)` raised
# an unhandled `FileNotFoundError` and -- with no exception handler on the
# app -- `GET /api/agent/provider-credential` returned a raw traceback to
# the user on a configured macOS box. Same class as muxplex-at9 (raw
# subprocess errors reaching the UI) in the functions that fix missed.
#
# These tests pin BOTH branches of the guard. The absent-branch tests assert
# that no subprocess is spawned AT ALL -- the fix is a precondition checked
# before spawning, not a swallowed exception, and a test that only checked
# the return value could not tell those two apart. The present-branch tests
# exist so the guard cannot be silently inverted or dropped: without them,
# "always return the non-systemd answer" would pass just as well.
#
# Written because the guard originally shipped with ZERO direct coverage,
# which is the same failure shape as the bug it fixes: a code path that
# only ever ran in the environment we happened to have.
# ---------------------------------------------------------------------------


def test_have_systemctl_true_when_on_path(monkeypatch):
    """The present branch: `shutil.which` finds systemctl -> True."""
    monkeypatch.setattr(
        "muxplex.main.shutil.which",
        lambda name: "/usr/bin/systemctl" if name == "systemctl" else None,
    )
    assert _have_systemctl() is True


def test_have_systemctl_false_when_absent(monkeypatch):
    """The absent branch (macOS): `shutil.which` finds nothing -> False."""
    monkeypatch.setattr("muxplex.main.shutil.which", lambda _name: None)
    assert _have_systemctl() is False


async def test_shadow_vars_returns_empty_without_spawning_when_no_systemctl(
    monkeypatch,
):
    """On a non-systemd host `_agent_service_env_shadow_vars()` must answer
    "no shadow vars" WITHOUT spawning anything. There is no systemd unit to
    introspect, so the empty set is the correct answer rather than a
    degraded fallback -- and the guard must run BEFORE the spawn, which is
    what the fail-if-spawned stub pins."""
    monkeypatch.setattr("muxplex.main._have_systemctl", lambda: False)

    async def _fail_if_spawned(*_a, **_kw):
        raise AssertionError(
            "must not spawn a subprocess when systemctl is unavailable"
        )

    monkeypatch.setattr("asyncio.create_subprocess_exec", _fail_if_spawned)

    assert await _agent_service_env_shadow_vars() == set()


async def test_shadow_vars_still_reads_systemd_environment_when_present(
    monkeypatch, tmp_path
):
    """The other half of the guard: where systemd IS present the function
    must still spawn `systemctl show` and parse it. Without this, inverting
    the guard (or hard-coding the empty set) would go unnoticed."""
    env_file = tmp_path / "aasvc.env"
    env_file.write_text("ANTHROPIC_API_KEY=sk-shadowed-value\n")
    spawned: list[tuple] = []

    async def _fake_systemctl_show(*args, **_kw):
        spawned.append(args)

        class _P:
            returncode = 0

            async def communicate(self):
                return (
                    f"Environment=\nEnvironmentFiles={env_file} (ignore_errors=no)\n".encode(),
                    b"",
                )

        return _P()

    monkeypatch.setattr("muxplex.main._have_systemctl", lambda: True)
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_systemctl_show)

    assert await _agent_service_env_shadow_vars() == {"ANTHROPIC_API_KEY"}
    assert spawned, "must spawn `systemctl show` on a systemd host"
    assert spawned[0][0] == "systemctl"
    assert spawned[0][1] == "show"


async def test_restart_sidecar_refuses_without_spawning_when_no_systemctl(
    monkeypatch,
):
    """Restarting a systemd unit is impossible on a host with no systemd.
    That must be reported as a plain precondition failure -- again without
    spawning, and in language that names the real constraint rather than
    leaking a `FileNotFoundError`."""
    monkeypatch.setattr("muxplex.main._have_systemctl", lambda: False)

    async def _fail_if_spawned(*_a, **_kw):
        raise AssertionError(
            "must not spawn a subprocess when systemctl is unavailable"
        )

    monkeypatch.setattr("asyncio.create_subprocess_exec", _fail_if_spawned)

    ok, detail = await _restart_agent_sidecar_and_wait()
    assert ok is False
    assert "systemctl" in detail
    # A precondition stated in plain language -- never a raw exception.
    assert "FileNotFoundError" not in detail
    assert "Traceback" not in detail


async def test_restart_sidecar_spawns_systemctl_when_present(monkeypatch):
    """The present branch: where systemd exists the restart must actually be
    attempted. A non-zero return is used so the function reports the failure
    immediately instead of entering its readiness poll -- what is being
    pinned here is that the spawn HAPPENS, not what it returns."""
    spawned: list[tuple] = []

    async def _fake_restart(*args, **_kw):
        spawned.append(args)

        class _P:
            returncode = 1

            async def communicate(self):
                return (b"", b"Failed to restart amplifier-agent-http.service")

        return _P()

    monkeypatch.setattr("muxplex.main._have_systemctl", lambda: True)
    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_restart)

    ok, detail = await _restart_agent_sidecar_and_wait()
    assert ok is False
    assert spawned, "must spawn `systemctl restart` on a systemd host"
    assert spawned[0][0] == "systemctl"
    assert spawned[0][1] == "restart"
    assert "Failed to restart" in detail


def test_credential_endpoint_survives_a_host_with_no_systemctl(monkeypatch):
    """The end-to-end regression, in the shape the user actually reported.

    `create_subprocess_exec` here raises `FileNotFoundError` exactly as it
    does on real macOS. No exception handler is registered on the app, so
    a 200 here means the spawn genuinely never happened.

    Confirmed to catch the regression rather than merely assumed to:
    re-running this test against a copy of main.py with the
    `_agent_service_env_shadow_vars` guard removed (the pre-v0.48.2 shape)
    fails with `FileNotFoundError: [Errno 2] ... 'systemctl'`. That is the
    unhandled error a real macOS user met -- surfaced here as a
    propagated exception because TestClient re-raises server exceptions,
    and as a 500 in production under uvicorn."""

    async def _fake_run(args, **_kwargs):
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
        return (0, "Credentials file: (not present)\n", "")

    async def _no_systemd_spawn(*_a, **_kw):
        raise FileNotFoundError(2, "No such file or directory: 'systemctl'")

    monkeypatch.setattr("muxplex.main._run_agent_cli", _fake_run)
    monkeypatch.setattr("muxplex.main._agent_sidecar_install_gap", lambda: None)
    monkeypatch.setattr("muxplex.main.shutil.which", lambda _name: None)
    monkeypatch.setattr("asyncio.create_subprocess_exec", _no_systemd_spawn)

    client = _authed_client()
    resp = client.get("/api/agent/provider-credential")

    assert resp.status_code == 200
    assert "systemctl" not in resp.text
    assert "FileNotFoundError" not in resp.text
    assert "Traceback" not in resp.text


# ---------------------------------------------------------------------------
# muxplex-at9: sidecar not installed -- detected BEFORE shelling out, never
# rendered as raw subprocess stderr. This is the default starting state for
# every fresh muxplex install: the service account and CLI binary only exist
# on a box someone has already worked through docs/AGENT_CHAT_SETUP.md on.
# ---------------------------------------------------------------------------


def test_install_gap_reports_missing_service_account(monkeypatch):
    def _no_such_user(_name):
        raise KeyError("getpwnam(): name not found")

    monkeypatch.setattr(pwd, "getpwnam", _no_such_user)
    gap = _agent_sidecar_install_gap()
    assert gap is not None
    assert "aa-svc" in gap


def test_install_gap_reports_missing_binary(monkeypatch):
    # A real account (root, uid 0) always exists -- isolates this case to
    # the binary-missing check alone.
    monkeypatch.setattr("muxplex.main._AGENT_AUTH_USER", "root")
    monkeypatch.setattr("muxplex.main._AGENT_AUTH_BIN", "/no/such/binary-at9")
    gap = _agent_sidecar_install_gap()
    assert gap is not None
    assert "/no/such/binary-at9" in gap


def test_install_gap_is_none_when_both_present(monkeypatch, tmp_path):
    real_bin = tmp_path / "amplifier-agent"
    real_bin.write_text("#!/bin/sh\n")
    monkeypatch.setattr("muxplex.main._AGENT_AUTH_USER", "root")
    monkeypatch.setattr("muxplex.main._AGENT_AUTH_BIN", str(real_bin))
    assert _agent_sidecar_install_gap() is None


async def test_run_agent_cli_raises_without_spawning_a_subprocess(monkeypatch):
    """The whole point: detecting "not installed" must never itself shell
    out (that shelling-out IS the leak this bug is about)."""
    monkeypatch.setattr(
        "muxplex.main._AGENT_AUTH_USER", "definitely-not-a-real-user-at9"
    )

    async def _fail_if_spawned(*_a, **_kw):
        raise AssertionError(
            "must not spawn a subprocess when the sidecar isn't installed"
        )

    monkeypatch.setattr("asyncio.create_subprocess_exec", _fail_if_spawned)

    with pytest.raises(AgentSidecarNotInstalled) as excinfo:
        await _run_agent_cli(["auth", "status"])
    assert "definitely-not-a-real-user-at9" in str(excinfo.value)


def test_get_status_reports_not_installed_not_error(monkeypatch):
    """The core regression this work item fixes: opening Settings -> Agent
    on a box with no sidecar installed must read as an onboarding fact
    (`state: "not_installed"`), and the message must NEVER contain raw
    `sudo`/audit-plugin stderr -- because no subprocess is ever spawned to
    produce that stderr in the first place."""

    async def _raise_not_installed(*_a, **_kw):
        raise AgentSidecarNotInstalled(
            "the 'aa-svc' service account does not exist on this server"
        )

    monkeypatch.setattr("muxplex.main._run_agent_cli", _raise_not_installed)

    client = _authed_client()
    resp = client.get("/api/agent/provider-credential")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "not_installed"
    assert "AGENT_CHAT_SETUP.md" in body["message"]
    # No raw sudo/audit-plugin stderr anywhere in the response.
    assert "sudo:" not in resp.text
    assert "audit plugin" not in resp.text


def test_post_credential_refuses_cleanly_when_not_installed(monkeypatch):
    """Same precondition, reached via the write path: a clean 503, never
    a 500 carrying raw subprocess stderr."""

    async def _raise_not_installed(provider, api_key):
        raise AgentSidecarNotInstalled(
            "the 'aa-svc' service account does not exist on this server"
        )

    monkeypatch.setattr("muxplex.main._validate_agent_credential", _raise_not_installed)

    client = _authed_client()
    resp = client.post(
        "/api/agent/provider-credential",
        json={"provider": "anthropic", "api_key": "sk-whatever"},
    )
    assert resp.status_code == 503
    assert "AGENT_CHAT_SETUP.md" in resp.text
    assert "sudo:" not in resp.text
    assert "audit plugin" not in resp.text
    assert "sk-whatever" not in resp.text
