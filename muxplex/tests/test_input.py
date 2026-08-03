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
    redact_preview,
    session_matches_allowlist,
    session_target,
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

    tmp_socket_dir = tmp_path / "ttyd"
    monkeypatch.setattr("muxplex.ttyd.TTYD_SOCKET_DIR", tmp_socket_dir)

    async def _mock_reap_orphan():
        return 0

    async def _mock_reap_legacy():
        return False

    monkeypatch.setattr("muxplex.main.reap_orphan_ttyds", _mock_reap_orphan)
    monkeypatch.setattr("muxplex.main.reap_legacy_ttyd", _mock_reap_legacy)
    monkeypatch.setattr("muxplex.main.ttyd_mod.validate_socket_dir", lambda d: None)

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


def _enable(monkeypatch, allowed: list, known: list[str]) -> None:
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


def test_allowlist_exact_entry_matches_only_itself(client, monkeypatch, tmux_calls):
    """A literal (non-glob) entry is backward compatible: matches only that name."""
    _enable(monkeypatch, allowed=["alpha"], known=["alpha", "alphabet"])
    ok = client.post("/api/sessions/alpha/input", json={"text": "hi"})
    assert ok.status_code == 200
    other = client.post("/api/sessions/alphabet/input", json={"text": "hi"})
    assert other.status_code == 403


def test_allowlist_star_allows_any_valid_session(client, monkeypatch, tmux_calls):
    """ "*" is a valid pattern that allows every known session."""
    _enable(monkeypatch, allowed=["*"], known=["alpha", "amplifier-foo", "z"])
    for name in ("alpha", "amplifier-foo", "z"):
        resp = client.post(f"/api/sessions/{name}/input", json={"text": "hi"})
        assert resp.status_code == 200


def test_allowlist_prefix_glob_matches_family_only(client, monkeypatch, tmux_calls):
    """ "amplifier-*" matches the prefix family, not lookalikes or unrelated names."""
    _enable(
        monkeypatch,
        allowed=["amplifier-*"],
        known=["amplifier-foo", "amplifier-test-input", "other-foo", "xamplifier-foo"],
    )
    for name in ("amplifier-foo", "amplifier-test-input"):
        resp = client.post(f"/api/sessions/{name}/input", json={"text": "hi"})
        assert resp.status_code == 200, name
    for name in ("other-foo", "xamplifier-foo"):
        resp = client.post(f"/api/sessions/{name}/input", json={"text": "hi"})
        assert resp.status_code == 403, name


def test_allowlist_matching_is_case_insensitive(client, monkeypatch, tmux_calls):
    """ "Amplifier-*" (capital A) DOES match "amplifier-foo" -- casefold + fnmatchcase."""
    _enable(monkeypatch, allowed=["Amplifier-*"], known=["amplifier-foo"])
    resp = client.post("/api/sessions/amplifier-foo/input", json={"text": "hi"})
    assert resp.status_code == 200
    assert tmux_calls == [("send-keys", "-l", "-t", "amplifier-foo", "--", "hi")]


def test_allowlist_junk_entries_skipped_valid_pattern_still_works(
    client, monkeypatch, tmux_calls
):
    """Non-string junk in the allowlist is skipped, not fatal; valid patterns still match."""
    _enable(
        monkeypatch,
        allowed=[123, None, "amplifier-*"],
        known=["amplifier-foo"],
    )
    resp = client.post("/api/sessions/amplifier-foo/input", json={"text": "hi"})
    assert resp.status_code == 200


def test_allowlist_multiple_patterns_any_match_allows(client, monkeypatch, tmux_calls):
    """Any pattern in the list matching is sufficient -- not just the first."""
    _enable(monkeypatch, allowed=["zzz-*", "amplifier-*"], known=["amplifier-foo"])
    resp = client.post("/api/sessions/amplifier-foo/input", json={"text": "hi"})
    assert resp.status_code == 200


def test_nonallowlisted_and_nonexistent_session_returns_403_not_404(
    client, monkeypatch, tmux_calls
):
    """A name that is BOTH unmatched by any pattern AND unknown -> 403, never 404.

    Proves the allowlist check still runs before the existence check, so the
    endpoint never leaks whether a non-allowlisted session exists.
    """
    _enable(monkeypatch, allowed=["amplifier-*"], known=[])
    resp = client.post("/api/sessions/ghost/input", json={"text": "hi"})
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
# `lines` -- caller-controlled read-back depth (no-scrollback fix)
# ---------------------------------------------------------------------------


def test_lines_omitted_uses_default_capture_lines(client, monkeypatch):
    """Omitting `lines` must preserve the original read-back depth exactly."""
    from muxplex.sessions import DEFAULT_CAPTURE_LINES

    _enable(monkeypatch, allowed=["alpha"], known=["alpha"])

    captured_args = []

    async def fake_run_tmux(*args: str) -> str:
        return ""

    async def fake_capture(name: str, lines: int = 30) -> str:
        captured_args.append((name, lines))
        return f"pane-of-{name}"

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("muxplex.main.run_tmux", fake_run_tmux)
    monkeypatch.setattr("muxplex.main.capture_pane", fake_capture)
    monkeypatch.setattr("muxplex.main.asyncio.sleep", fake_sleep)

    resp = client.post("/api/sessions/alpha/input", json={"text": "hi"})
    assert resp.status_code == 200
    assert captured_args == [("alpha", DEFAULT_CAPTURE_LINES)]


def test_lines_override_forwarded_to_readback_capture(client, monkeypatch):
    """An explicit `lines` value must be forwarded to the read-back capture_pane() call."""
    _enable(monkeypatch, allowed=["alpha"], known=["alpha"])

    captured_args = []

    async def fake_run_tmux(*args: str) -> str:
        return ""

    async def fake_capture(name: str, lines: int = 30) -> str:
        captured_args.append((name, lines))
        return f"pane-of-{name}"

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("muxplex.main.run_tmux", fake_run_tmux)
    monkeypatch.setattr("muxplex.main.capture_pane", fake_capture)
    monkeypatch.setattr("muxplex.main.asyncio.sleep", fake_sleep)

    resp = client.post("/api/sessions/alpha/input", json={"text": "hi", "lines": 500})
    assert resp.status_code == 200
    assert captured_args == [("alpha", 500)]


def test_lines_above_max_returns_400(client, monkeypatch, tmux_calls):
    """`lines` above MAX_CAPTURE_LINES must be a 400 -- never silently clamped."""
    from muxplex.sessions import MAX_CAPTURE_LINES

    _enable(monkeypatch, allowed=["alpha"], known=["alpha"])
    resp = client.post(
        "/api/sessions/alpha/input",
        json={"text": "hi", "lines": MAX_CAPTURE_LINES + 1},
    )
    assert resp.status_code == 400
    assert "lines" in resp.json()["detail"]
    assert tmux_calls == [], "nothing must reach tmux when validation fails"


def test_lines_zero_or_negative_returns_400(client, monkeypatch, tmux_calls):
    """`lines` <= 0 must be a 400."""
    _enable(monkeypatch, allowed=["alpha"], known=["alpha"])
    resp = client.post("/api/sessions/alpha/input", json={"text": "hi", "lines": 0})
    assert resp.status_code == 400
    assert tmux_calls == []


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
# session_matches_allowlist -- the pure glob-matching helper, unit-tested
# directly (no HTTP/TestClient overhead needed for these).
# ---------------------------------------------------------------------------


def test_matches_allowlist_exact_pattern_matches_only_itself():
    assert session_matches_allowlist("alpha", ["alpha"]) is True
    assert session_matches_allowlist("alphabet", ["alpha"]) is False


def test_matches_allowlist_star_matches_any_name():
    assert session_matches_allowlist("anything-goes", ["*"]) is True
    assert session_matches_allowlist("x", ["*"]) is True


def test_matches_allowlist_prefix_glob():
    assert session_matches_allowlist("amplifier-foo", ["amplifier-*"]) is True
    assert session_matches_allowlist("amplifier-test-input", ["amplifier-*"]) is True
    assert session_matches_allowlist("other-foo", ["amplifier-*"]) is False
    assert session_matches_allowlist("xamplifier-foo", ["amplifier-*"]) is False


def test_matches_allowlist_is_case_insensitive():
    """casefold() + fnmatchcase -- matching folds case deterministically on every platform."""
    # pattern upper, name lower
    assert session_matches_allowlist("amplifier-foo", ["Amplifier-*"]) is True
    # pattern lower, name upper
    assert session_matches_allowlist("AMPLIFIER-Foo", ["amplifier-*"]) is True
    # exact-name entries are also case-insensitive now
    assert session_matches_allowlist("mysession", ["MySession"]) is True
    assert session_matches_allowlist("MYSESSION", ["MySession"]) is True
    # mixed case on both sides
    assert session_matches_allowlist("aMpLiFiEr-test", ["AmPlIfIeR-*"]) is True


def test_matches_allowlist_empty_list_denies_everything():
    assert session_matches_allowlist("alpha", []) is False


def test_matches_allowlist_skips_non_string_entries():
    """Junk entries (int/None/dict) are skipped, not fatal; valid entries still match."""
    assert (
        session_matches_allowlist("amplifier-foo", [123, None, "amplifier-*"]) is True
    )
    assert session_matches_allowlist("alpha", [123, None, {}]) is False


def test_matches_allowlist_multiple_patterns_any_match_wins():
    assert session_matches_allowlist("amplifier-foo", ["zzz-*", "amplifier-*"]) is True
    assert session_matches_allowlist("amplifier-foo", ["amplifier-*", "zzz-*"]) is True
    assert session_matches_allowlist("neither", ["zzz-*", "amplifier-*"]) is False


def test_matches_allowlist_question_and_bracket_glob_forms():
    """`?` and `[abc]` glob forms come free with fnmatch -- document, don't assume."""
    assert session_matches_allowlist("job1", ["job?"]) is True
    assert session_matches_allowlist("job12", ["job?"]) is False
    assert session_matches_allowlist("joba", ["job[abc]"]) is True
    assert session_matches_allowlist("jobd", ["job[abc]"]) is False


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
    """The local-only set covers both terminal-input fence keys plus the
    command/path settings keys fenced for the same reason (see
    settings.LOCAL_ONLY_KEYS's module comment: a client holding only the
    federation Bearer key could otherwise rewrite new_session_template and
    get RCE without ever touching the /input endpoint this file tests).

    session_commands is included: it's a LIST of additional named
    create/kill pairs, each holding the same two arbitrary shell commands as
    the singular keys -- the API may list/select a pair, never define one
    (COMMAND_PAIRS_SPEC.md)."""
    assert LOCAL_ONLY_KEYS == frozenset(
        {
            "input_enabled",
            "input_allowed_sessions",
            "new_session_template",
            "delete_session_template",
            "session_commands",
            "tmux_socket_dir",
            "tls_cert",
            "tls_key",
        }
    )
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
