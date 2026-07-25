"""
Tests for POST /api/sessions/{name}/input — the fenced terminal-input endpoint.

The endpoint is remote-code-execution by design, so these tests are primarily
about the FENCES: global opt-in (input_enabled), per-session allowlist
(input_allowed_sessions), fail-closed target gate, name validation, and the
named-key allowlist. Plus: argv construction (literal send-keys, never shell),
the read-back snapshot, and audit-log hygiene.
"""

import logging

import pytest
from fastapi.testclient import TestClient

from muxplex.main import app
from muxplex.settings import DEFAULT_SETTINGS, LOCAL_ONLY_KEYS, SYNCABLE_KEYS
from muxplex.terminal_input import (
    ALLOWED_KEYS,
    MAX_KEYS,
    MAX_TEXT_BYTES,
    build_send_key_argv,
    build_send_text_argv,
    session_target,
    redact_preview,
)


# ---------------------------------------------------------------------------
# Fixtures — mirror test_api.py's isolation pattern
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_startup_and_state(tmp_path, monkeypatch):
    """Redirect state/PID files, mock startup side-effects (same as test_api)."""
    tmp_state_dir = tmp_path / "state"
    monkeypatch.setattr("muxplex.state.STATE_DIR", tmp_state_dir)
    monkeypatch.setattr("muxplex.state.STATE_PATH", tmp_state_dir / "state.json")

    tmp_pid_dir = tmp_path / "ttyd"
    monkeypatch.setattr("muxplex.ttyd.TTYD_PID_DIR", tmp_pid_dir)
    monkeypatch.setattr("muxplex.ttyd.TTYD_PID_PATH", tmp_pid_dir / "ttyd.pid")

    async def _mock_kill_orphan():
        return False

    monkeypatch.setattr("muxplex.main.kill_orphan_ttyd", _mock_kill_orphan)

    async def noop_poll_loop() -> None:
        pass

    monkeypatch.setattr("muxplex.main._poll_loop", noop_poll_loop)


@pytest.fixture
def client(monkeypatch):
    """TestClient with a valid session cookie (bypasses AuthMiddleware)."""
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    with TestClient(app) as c:
        from muxplex.auth import create_session_cookie
        from muxplex.main import _auth_secret, _auth_ttl

        cookie = create_session_cookie(_auth_secret, _auth_ttl)
        c.cookies.set("muxplex_session", cookie)
        yield c


def _settings(**overrides) -> dict:
    """Return a copy of DEFAULT_SETTINGS with overrides applied."""
    import copy

    s = copy.deepcopy(DEFAULT_SETTINGS)
    s.update(overrides)
    return s


@pytest.fixture
def tmux_calls(monkeypatch):
    """Record run_tmux argv calls instead of touching a real tmux.

    Also stubs capture_pane (read-back) and removes the 0.4s settle sleep
    so the test suite stays fast.
    """
    calls: list[tuple[str, ...]] = []

    async def fake_run_tmux(*args: str) -> str:
        calls.append(args)
        return ""

    async def fake_capture(name: str, lines: int = 30) -> str:
        return f"pane-of-{name}"

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("muxplex.main.run_tmux", fake_run_tmux)
    monkeypatch.setattr("muxplex.main.capture_pane", fake_capture)
    monkeypatch.setattr("muxplex.main.asyncio.sleep", fake_sleep)
    return calls


def _enable(monkeypatch, allowed: list[str], known: list[str]) -> None:
    """Enable the feature with *allowed* sessions and a known-session set."""
    monkeypatch.setattr(
        "muxplex.main.load_settings",
        lambda: _settings(input_enabled=True, input_allowed_sessions=allowed),
    )
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: known)


# ---------------------------------------------------------------------------
# Fence 1: global opt-in (input_enabled, default OFF)
# ---------------------------------------------------------------------------


def test_input_disabled_by_default_in_settings():
    """The config defaults must be CLOSED: disabled, empty allowlist."""
    assert DEFAULT_SETTINGS["input_enabled"] is False
    assert DEFAULT_SETTINGS["input_allowed_sessions"] == []


def test_input_fences_are_not_syncable():
    """A federation peer's settings sync must never widen the input fences."""
    assert "input_enabled" not in SYNCABLE_KEYS
    assert "input_allowed_sessions" not in SYNCABLE_KEYS


def test_input_disabled_returns_403(client, monkeypatch, tmux_calls):
    """input_enabled=False -> 403, even for an allowlisted, known session."""
    monkeypatch.setattr(
        "muxplex.main.load_settings",
        lambda: _settings(input_enabled=False, input_allowed_sessions=["alpha"]),
    )
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])
    resp = client.post("/api/sessions/alpha/input", json={"text": "hi"})
    assert resp.status_code == 403
    assert "disabled" in resp.json()["detail"].lower()
    assert tmux_calls == []  # nothing reached tmux


def test_default_settings_leave_endpoint_disabled(client, monkeypatch, tmux_calls):
    """With stock DEFAULT_SETTINGS (no overrides at all) the endpoint is a 403."""
    monkeypatch.setattr("muxplex.main.load_settings", lambda: _settings())
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])
    resp = client.post("/api/sessions/alpha/input", json={"text": "hi"})
    assert resp.status_code == 403
    assert tmux_calls == []


# ---------------------------------------------------------------------------
# Fence 2: per-session allowlist
# ---------------------------------------------------------------------------


def test_session_not_on_allowlist_returns_403(client, monkeypatch, tmux_calls):
    """Enabled, session exists, but not allowlisted -> 403."""
    _enable(monkeypatch, allowed=["other"], known=["alpha", "other"])
    resp = client.post("/api/sessions/alpha/input", json={"text": "hi"})
    assert resp.status_code == 403
    assert "input_allowed_sessions" in resp.json()["detail"]
    assert tmux_calls == []


def test_empty_allowlist_rejects_everything(client, monkeypatch, tmux_calls):
    """Enabled but empty allowlist -> every session is a 403."""
    _enable(monkeypatch, allowed=[], known=["alpha"])
    resp = client.post("/api/sessions/alpha/input", json={"text": "hi"})
    assert resp.status_code == 403
    assert tmux_calls == []


def test_allowlist_is_exact_names_no_prefix_or_glob(client, monkeypatch, tmux_calls):
    """Allowlist matching is exact: neither prefix nor glob may match."""
    _enable(monkeypatch, allowed=["alpha*", "alph"], known=["alpha"])
    resp = client.post("/api/sessions/alpha/input", json={"text": "hi"})
    assert resp.status_code == 403
    assert tmux_calls == []


# ---------------------------------------------------------------------------
# Fence 4: fail-closed target gate (known-session set)
# ---------------------------------------------------------------------------


def test_unknown_session_returns_404(client, monkeypatch, tmux_calls):
    """Allowlisted but not in the known-session set -> 404."""
    _enable(monkeypatch, allowed=["ghost"], known=["alpha"])
    resp = client.post("/api/sessions/ghost/input", json={"text": "hi"})
    assert resp.status_code == 404
    assert tmux_calls == []


def test_empty_session_cache_fails_closed(client, monkeypatch, tmux_calls):
    """Empty/unavailable known-session cache rejects every target (404)."""
    _enable(monkeypatch, allowed=["alpha"], known=[])
    resp = client.post("/api/sessions/alpha/input", json={"text": "hi"})
    assert resp.status_code == 404
    assert tmux_calls == []


# ---------------------------------------------------------------------------
# Fence 5: session-name validation at the boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    ["a;b", "a b", "-flag", "a:b", ".hidden", "a$(x)"],
)
def test_invalid_session_name_returns_400(client, monkeypatch, tmux_calls, bad_name):
    """Names failing is_valid_session_name -> 400 before any other check."""
    _enable(monkeypatch, allowed=[bad_name], known=[bad_name])
    resp = client.post(f"/api/sessions/{bad_name}/input", json={"text": "hi"})
    assert resp.status_code == 400
    assert tmux_calls == []


# ---------------------------------------------------------------------------
# Named-key allowlist
# ---------------------------------------------------------------------------


def test_unsupported_key_returns_400(client, monkeypatch, tmux_calls):
    """A key outside ALLOWED_KEYS is rejected with 400; nothing is sent."""
    _enable(monkeypatch, allowed=["alpha"], known=["alpha"])
    resp = client.post(
        "/api/sessions/alpha/input",
        json={"keys": ["Enter", "C-b"]},  # C-b (tmux prefix) is not allowlisted
    )
    assert resp.status_code == 400
    assert "C-b" in resp.json()["detail"]
    assert tmux_calls == []


def test_empty_payload_returns_400(client, monkeypatch, tmux_calls):
    """No text, no keys, enter=false -> 400 (nothing to send)."""
    _enable(monkeypatch, allowed=["alpha"], known=["alpha"])
    resp = client.post("/api/sessions/alpha/input", json={})
    assert resp.status_code == 400
    assert tmux_calls == []


# ---------------------------------------------------------------------------
# Accepted input: argv construction, send order, read-back
# ---------------------------------------------------------------------------


def test_text_sent_literally_via_argv(client, monkeypatch, tmux_calls):
    """text goes as ONE argv element after `send-keys -l -t name --`."""
    _enable(monkeypatch, allowed=["alpha"], known=["alpha"])
    hostile = "; rm -rf / && $(reboot) `id` | tee /etc/passwd"
    resp = client.post("/api/sessions/alpha/input", json={"text": hostile})
    assert resp.status_code == 200
    assert tmux_calls == [("send-keys", "-l", "-t", "alpha", "--", hostile)]


def test_enter_sends_enter_after_text(client, monkeypatch, tmux_calls):
    """enter=true appends a named Enter key after the literal text."""
    _enable(monkeypatch, allowed=["alpha"], known=["alpha"])
    resp = client.post("/api/sessions/alpha/input", json={"text": "ls", "enter": True})
    assert resp.status_code == 200
    assert tmux_calls == [
        ("send-keys", "-l", "-t", "alpha", "--", "ls"),
        ("send-keys", "-t", "alpha", "Enter"),
    ]


def test_named_keys_sent_in_order(client, monkeypatch, tmux_calls):
    """keys are sent individually, in order, non-literal (named-key mode)."""
    _enable(monkeypatch, allowed=["alpha"], known=["alpha"])
    resp = client.post("/api/sessions/alpha/input", json={"keys": ["C-c", "Up"]})
    assert resp.status_code == 200
    assert tmux_calls == [
        ("send-keys", "-t", "alpha", "C-c"),
        ("send-keys", "-t", "alpha", "Up"),
    ]


def test_response_includes_readback_snapshot(client, monkeypatch, tmux_calls):
    """The response carries the post-input pane capture."""
    _enable(monkeypatch, allowed=["alpha"], known=["alpha"])
    resp = client.post("/api/sessions/alpha/input", json={"text": "hi"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["session"] == "alpha"
    assert body["snapshot"] == "pane-of-alpha"


def test_tmux_failure_returns_500(client, monkeypatch, tmux_calls):
    """A RuntimeError from tmux (session vanished mid-flight) -> 500."""
    _enable(monkeypatch, allowed=["alpha"], known=["alpha"])

    async def failing_run_tmux(*args: str) -> str:
        raise RuntimeError("can't find pane")

    monkeypatch.setattr("muxplex.main.run_tmux", failing_run_tmux)
    resp = client.post("/api/sessions/alpha/input", json={"text": "hi"})
    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


def test_audit_log_line_present_and_redacted(client, monkeypatch, tmux_calls, caplog):
    """One info line per accepted action; full text NOT at info level."""
    _enable(monkeypatch, allowed=["alpha"], known=["alpha"])
    secret = "export TOKEN=super-secret-value-9000"
    with caplog.at_level(logging.INFO, logger="muxplex.main"):
        resp = client.post(
            "/api/sessions/alpha/input", json={"text": secret, "enter": True}
        )
    assert resp.status_code == 200
    audit = [r for r in caplog.records if r.getMessage().startswith("input: session=")]
    assert len(audit) == 1
    msg = audit[0].getMessage()
    assert "'alpha'" in msg
    assert f"chars={len(secret)}" in msg
    assert "enter=True" in msg
    # Redaction: the 16-char preview appears, the full secret does not.
    assert "export TOKEN=sup" in msg
    assert "super-secret-value-9000" not in msg


def test_rejection_logged_at_warning(client, monkeypatch, tmux_calls, caplog):
    """Fence rejections emit a warning log line."""
    monkeypatch.setattr("muxplex.main.load_settings", lambda: _settings())
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])
    with caplog.at_level(logging.WARNING, logger="muxplex.main"):
        client.post("/api/sessions/alpha/input", json={"text": "hi"})
    assert any(
        r.levelno == logging.WARNING and "input_enabled" in r.getMessage()
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# terminal_input helpers (pure functions)
# ---------------------------------------------------------------------------


def test_session_target_is_plain_name():
    assert session_target("alpha") == "alpha"


def test_build_send_text_argv_shape():
    argv = build_send_text_argv("s1", "-rf --danger")
    assert argv == ["send-keys", "-l", "-t", "s1", "--", "-rf --danger"]


def test_build_send_key_argv_rejects_non_allowlisted():
    with pytest.raises(ValueError):
        build_send_key_argv("s1", "C-b")


def test_allowed_keys_is_the_documented_closed_set():
    assert ALLOWED_KEYS == frozenset(
        {
            "Enter",
            "Escape",
            "Tab",
            "C-c",
            "C-d",
            "Up",
            "Down",
            "Left",
            "Right",
            "PageUp",
            "PageDown",
        }
    )


def test_redact_preview_truncates_and_flattens_newlines():
    assert redact_preview("abc") == "abc"
    long = "x" * 40
    out = redact_preview(long)
    assert out.startswith("x" * 16) and out.endswith("…")
    assert "\n" not in redact_preview("a\nb\r\nc")


# ---------------------------------------------------------------------------
# F1: fence keys are LOCAL-FILE-ONLY — never settable via PATCH /api/settings
#
# The federation Bearer key satisfies the shared auth on PATCH /api/settings,
# and it is the SAME credential handed to remote agents that call /input.
# If the fence keys were PATCHable, an agent could self-authorize typing into
# the human's own panes. These tests exercise the REAL patch_settings against
# a redirected settings file (no monkeypatched load_settings).
# ---------------------------------------------------------------------------


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    """Redirect the on-disk settings file so real load/patch/save run isolated."""
    import muxplex.settings as settings_mod

    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", path)
    return path


def test_local_only_keys_are_exactly_the_input_fences():
    """The local-only set covers both fence keys and stays out of sync."""
    assert LOCAL_ONLY_KEYS == frozenset({"input_enabled", "input_allowed_sessions"})
    assert LOCAL_ONLY_KEYS.isdisjoint(SYNCABLE_KEYS)


def test_patch_settings_ignores_input_enabled(client, settings_file, caplog):
    """PATCH input_enabled=true is ignored (and warned); co-submitted key applies."""
    with caplog.at_level(logging.WARNING, logger="muxplex.settings"):
        resp = client.patch(
            "/api/settings", json={"input_enabled": True, "fontSize": 18}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["input_enabled"] is False  # fence unchanged
    assert body["fontSize"] == 18  # unrelated key in same PATCH still applied
    # On disk too, not just the response.
    from muxplex.settings import load_settings

    on_disk = load_settings()
    assert on_disk["input_enabled"] is False
    assert on_disk["fontSize"] == 18
    assert any(
        "local-only" in r.getMessage() and "input_enabled" in r.getMessage()
        for r in caplog.records
    )


def test_patch_settings_ignores_input_allowed_sessions(client, settings_file):
    """PATCH input_allowed_sessions=[...] is ignored — allowlist stays empty."""
    resp = client.patch(
        "/api/settings", json={"input_allowed_sessions": ["victim-shell"]}
    )
    assert resp.status_code == 200
    assert resp.json()["input_allowed_sessions"] == []
    from muxplex.settings import load_settings

    assert load_settings()["input_allowed_sessions"] == []


def test_patch_cannot_widen_input_fence_end_to_end(
    client, settings_file, monkeypatch, tmux_calls
):
    """The self-authorization attack: PATCH both fence keys, then call /input.

    The PATCH must be ignored, so /input still 403s and nothing reaches tmux.
    Uses the REAL load_settings (redirected file) — no settings mocking.
    """
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])
    patch_resp = client.patch(
        "/api/settings",
        json={"input_enabled": True, "input_allowed_sessions": ["alpha"]},
    )
    assert patch_resp.status_code == 200
    resp = client.post("/api/sessions/alpha/input", json={"text": "hi"})
    assert resp.status_code == 403
    assert tmux_calls == []


# ---------------------------------------------------------------------------
# F2: strict-typed fence reads (fail CLOSED on malformed settings values)
# ---------------------------------------------------------------------------


def test_string_false_input_enabled_fails_closed(client, monkeypatch, tmux_calls):
    """input_enabled: "false" (truthy STRING) must disable, not enable."""
    monkeypatch.setattr(
        "muxplex.main.load_settings",
        lambda: _settings(input_enabled="false", input_allowed_sessions=["alpha"]),
    )
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])
    resp = client.post("/api/sessions/alpha/input", json={"text": "hi"})
    assert resp.status_code == 403
    assert tmux_calls == []


def test_string_allowlist_is_not_substring_matched(client, monkeypatch, tmux_calls):
    """input_allowed_sessions: "alpha" (STRING) must not substring-match "al"."""
    monkeypatch.setattr(
        "muxplex.main.load_settings",
        lambda: _settings(input_enabled=True, input_allowed_sessions="alpha"),
    )
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["al"])
    resp = client.post("/api/sessions/al/input", json={"text": "hi"})
    assert resp.status_code == 403
    assert tmux_calls == []


# ---------------------------------------------------------------------------
# F3: size/quantity caps + exec-failure handling
# ---------------------------------------------------------------------------


def test_oversized_text_rejected_413(client, monkeypatch, tmux_calls):
    """text over MAX_TEXT_BYTES -> 413; nothing reaches tmux."""
    _enable(monkeypatch, allowed=["alpha"], known=["alpha"])
    resp = client.post(
        "/api/sessions/alpha/input", json={"text": "x" * (MAX_TEXT_BYTES + 1)}
    )
    assert resp.status_code == 413
    assert tmux_calls == []


def test_text_at_cap_is_accepted(client, monkeypatch, tmux_calls):
    """Exactly MAX_TEXT_BYTES is fine — the cap is a limit, not off-by-one."""
    _enable(monkeypatch, allowed=["alpha"], known=["alpha"])
    resp = client.post("/api/sessions/alpha/input", json={"text": "x" * MAX_TEXT_BYTES})
    assert resp.status_code == 200
    assert len(tmux_calls) == 1


def test_too_many_keys_rejected_400(client, monkeypatch, tmux_calls):
    """keys count over MAX_KEYS -> 400; no subprocess storm."""
    _enable(monkeypatch, allowed=["alpha"], known=["alpha"])
    resp = client.post(
        "/api/sessions/alpha/input", json={"keys": ["Enter"] * (MAX_KEYS + 1)}
    )
    assert resp.status_code == 400
    assert tmux_calls == []


def test_oserror_from_exec_returns_clean_500(client, monkeypatch, tmux_calls):
    """An OSError from exec (e.g. E2BIG) -> handled 500, not a traceback."""
    _enable(monkeypatch, allowed=["alpha"], known=["alpha"])

    async def failing_run_tmux(*args: str) -> str:
        raise OSError(7, "Argument list too long")

    monkeypatch.setattr("muxplex.main.run_tmux", failing_run_tmux)
    resp = client.post("/api/sessions/alpha/input", json={"text": "hi"})
    assert resp.status_code == 500
    assert "Failed to send input" in resp.json()["detail"]
