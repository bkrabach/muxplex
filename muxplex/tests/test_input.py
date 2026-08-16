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
    input_allowed_for_session,
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
    """TestClient with a valid session cookie (bypasses AuthMiddleware).

    NOTE: a valid cookie makes this an OPERATOR caller, never ``bearer_only``
    (see main._bearer_only_caller). Since input_enabled/input_allowed_sessions
    became settings.OPERATOR_SETTABLE_LOCAL_KEYS, this client CAN set them via
    PATCH /api/settings. To exercise the fence that still blocks a federation
    Bearer holder, use the ``bearer_client`` fixture below instead.
    """
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    with TestClient(app) as c:
        from muxplex.auth import create_session_cookie
        from muxplex.main import _auth_secret, _auth_ttl

        cookie = create_session_cookie(_auth_secret, _auth_ttl)
        c.cookies.set("muxplex_session", cookie)
        yield c


@pytest.fixture
def bearer_client(tmp_path, monkeypatch):
    """TestClient whose ONLY credential is a real federation Bearer key.

    Yields ``(client, headers)``. Stubs nothing on the auth path -- neither
    AuthMiddleware nor ``main._bearer_only_caller`` -- so a request carrying
    *headers* is authorized by the real middleware and classified
    ``bearer_only`` by the real classifier. Two real readers, two real
    sources:

      1. AuthMiddleware's Bearer branch calls ``settings.load_federation_key()``
         FRESH FROM DISK per request (auth.py), honoring the
         ``MUXPLEX_FEDERATION_KEY_FILE`` env override -- that is what makes
         the real middleware ACCEPT the header (without it: 401).
      2. ``main._bearer_only_caller()`` compares the header against the
         module-global ``main._federation_key`` -- the same mechanism
         ``test_ws_proxy.py::test_ws_bearer_auth_accepted`` uses.

    No cookie is ever set, so this is the real self-authorization attacker
    the input fence exists to contain.
    """
    import muxplex.main as main_mod

    fed_key = "test-federation-key-input-fence"
    key_file = tmp_path / "federation_key"
    key_file.write_text(fed_key)
    monkeypatch.setenv("MUXPLEX_FEDERATION_KEY_FILE", str(key_file))
    monkeypatch.setattr(main_mod, "_federation_key", fed_key)
    monkeypatch.setenv("MUXPLEX_PASSWORD", "test-password")
    with TestClient(app) as c:
        yield c, {"Authorization": f"Bearer {fed_key}"}


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
    """The capability is OFF by default -- `input_enabled` is the gate.

    The allowlist default was deliberately WIDENED to "*" (v0.48.0, work
    item muxplex-ph0): it used to be `[]`, so flipping `input_enabled: true`
    hit a second, silent 403 wall and the operator had to enumerate session
    names by hand. `input_enabled` staying False is what keeps the
    capability closed out of the box -- and that is the assertion that
    must never be relaxed.
    """
    assert DEFAULT_SETTINGS["input_enabled"] is False
    # The LIST form on purpose: the fence requires a list, so written this
    # way the default needs no coercion to work. A bare "*" in a
    # hand-written settings.json is equally accepted (normalized on load) --
    # see the bare-string tests at the bottom of this file.
    assert DEFAULT_SETTINGS["input_allowed_sessions"] == ["*"]


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
    assert tmux_calls == [
        (
            "copy-mode",
            "-q",
            "-t",
            "amplifier-foo",
            ";",
            "send-keys",
            "-l",
            "-t",
            "amplifier-foo",
            "--",
            "hi",
        ),
    ]


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
    """text goes as ONE argv element after `send-keys -l -t name --`,
    chained behind tmux-kit's copy-mode-exit prefix in a single argv/call."""
    _enable(monkeypatch, allowed=["alpha"], known=["alpha"])
    hostile = "; rm -rf / && $(reboot) `id` | tee /etc/passwd"
    resp = client.post("/api/sessions/alpha/input", json={"text": hostile})
    assert resp.status_code == 200
    assert tmux_calls == [
        (
            "copy-mode",
            "-q",
            "-t",
            "alpha",
            ";",
            "send-keys",
            "-l",
            "-t",
            "alpha",
            "--",
            hostile,
        ),
    ]


def test_enter_sends_enter_after_text(client, monkeypatch, tmux_calls):
    """enter=true appends a named Enter key after the literal text, each as
    its own chained (copy-mode-exit + send-keys) call."""
    _enable(monkeypatch, allowed=["alpha"], known=["alpha"])
    resp = client.post("/api/sessions/alpha/input", json={"text": "ls", "enter": True})
    assert resp.status_code == 200
    assert tmux_calls == [
        (
            "copy-mode",
            "-q",
            "-t",
            "alpha",
            ";",
            "send-keys",
            "-l",
            "-t",
            "alpha",
            "--",
            "ls",
        ),
        ("copy-mode", "-q", "-t", "alpha", ";", "send-keys", "-t", "alpha", "Enter"),
    ]


def test_named_keys_sent_in_order(client, monkeypatch, tmux_calls):
    """keys are sent individually, in order, non-literal (named-key mode),
    each chained behind its own copy-mode-exit prefix."""
    _enable(monkeypatch, allowed=["alpha"], known=["alpha"])
    resp = client.post("/api/sessions/alpha/input", json={"keys": ["C-c", "Up"]})
    assert resp.status_code == 200
    assert tmux_calls == [
        ("copy-mode", "-q", "-t", "alpha", ";", "send-keys", "-t", "alpha", "C-c"),
        ("copy-mode", "-q", "-t", "alpha", ";", "send-keys", "-t", "alpha", "Up"),
    ]


# ---------------------------------------------------------------------------
# Copy-mode exit -- now the library's guarantee, verified at this boundary
# ---------------------------------------------------------------------------


def test_copy_mode_exit_is_chained_into_every_send_path(
    client, monkeypatch, tmux_calls
):
    """Every path that reaches tmux -- text+enter, enter-only, keys-only --
    must carry tmux-kit's copy-mode-exit prefix chained into the SAME
    argv/subprocess call as the send-keys itself.

    This endpoint no longer issues ``build_exit_copy_mode_argv()`` as its
    own separate leading call (see ``send_session_input``'s docstring):
    ``tmux_kit.keys.build_send_text_argv`` / ``build_send_key_argv`` chain
    it in as of tmux-kit 0.4.0, so every accepted call this endpoint makes
    to ``run_tmux`` already starts with ``copy-mode -q -t <name> ;``. This
    test verifies that guarantee holds at muxplex's own boundary, for each
    of the three send paths -- it is what stops the app-level code from
    silently regressing to a bare ``send-keys`` with no copy-mode prefix,
    which would resume the exact "scroll then send does nothing" bug this
    was written to fix (scrolling back silently puts the pane in tmux
    copy-mode via ``mouse on``, base.conf; a pane in that state consumes
    ``send-keys`` through the copy-mode key table instead of the shell).
    """
    _enable(monkeypatch, allowed=["alpha"], known=["alpha"])

    resp = client.post("/api/sessions/alpha/input", json={"text": "ls", "enter": True})
    assert resp.status_code == 200
    assert tmux_calls == [
        (
            "copy-mode",
            "-q",
            "-t",
            "alpha",
            ";",
            "send-keys",
            "-l",
            "-t",
            "alpha",
            "--",
            "ls",
        ),
        ("copy-mode", "-q", "-t", "alpha", ";", "send-keys", "-t", "alpha", "Enter"),
    ]
    for call in tmux_calls:
        assert call[:4] == ("copy-mode", "-q", "-t", "alpha")
    tmux_calls.clear()

    resp = client.post("/api/sessions/alpha/input", json={"enter": True})
    assert resp.status_code == 200
    assert tmux_calls == [
        ("copy-mode", "-q", "-t", "alpha", ";", "send-keys", "-t", "alpha", "Enter"),
    ]
    tmux_calls.clear()

    resp = client.post("/api/sessions/alpha/input", json={"keys": ["C-c", "Up"]})
    assert resp.status_code == 200
    assert tmux_calls == [
        ("copy-mode", "-q", "-t", "alpha", ";", "send-keys", "-t", "alpha", "C-c"),
        ("copy-mode", "-q", "-t", "alpha", ";", "send-keys", "-t", "alpha", "Up"),
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
    """As of tmux-kit 0.4.0 this is CHAINED: copy-mode-exit then send-keys,
    in one argv via a literal ``;`` element -- see this module's
    ``test_copy_mode_exit_is_chained_into_every_send_path`` for the
    boundary-level proof this chain is what muxplex actually sends."""
    argv = build_send_text_argv("s1", "-rf --danger")
    assert argv == [
        "copy-mode",
        "-q",
        "-t",
        "s1",
        ";",
        "send-keys",
        "-l",
        "-t",
        "s1",
        "--",
        "-rf --danger",
    ]


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
    (docs/plans/2026-08-02-named-session-command-pairs-plan.md).

    focus_app is included for the same reason: it's the argument to a
    command (`open -a <focus_app>`) the server executes on POST /api/focus
    (docs/plans/2026-08-05-focus-grab-plan.md)."""
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
            "focus_app",
        }
    )
    assert LOCAL_ONLY_KEYS.isdisjoint(SYNCABLE_KEYS)


def test_patch_settings_operator_enables_input_cosubmitted_key_applies(
    client, settings_file
):
    """An OPERATOR (cookie caller) PATCHing input_enabled=true DOES enable it;
    a co-submitted ordinary key applies in the same PATCH.

    Contract change: input_enabled joined settings.OPERATOR_SETTABLE_LOCAL_KEYS,
    so an operator-credentialed PATCH may set it (this is the whole point --
    flipping the gate from the browser instead of hand-editing settings.json
    over SSH on every host). It remains in LOCAL_ONLY_KEYS and is still
    dropped for a bearer_only caller -- see
    test_bearer_only_patch_cannot_widen_input_fence_end_to_end below.
    """
    resp = client.patch("/api/settings", json={"input_enabled": True, "fontSize": 18})
    assert resp.status_code == 200
    body = resp.json()
    assert body["input_enabled"] is True  # operator CAN flip the gate
    assert body["fontSize"] == 18  # unrelated key in same PATCH still applied
    # On disk too, not just the response.
    from muxplex.settings import load_settings

    on_disk = load_settings()
    assert on_disk["input_enabled"] is True
    assert on_disk["fontSize"] == 18


def test_patch_settings_operator_can_steer_input_allowed_sessions(
    client, settings_file
):
    """An OPERATOR may steer the allowlist in BOTH directions -- widen it to a
    specific family, and narrow it (including all the way to deny-all).

    Contract change: input_allowed_sessions joined
    settings.OPERATOR_SETTABLE_LOCAL_KEYS. Narrowing matters as much as
    widening: an operator scoping typing down to `agent-*` is the documented
    way to keep their own working panes un-typeable, and that has to be
    reachable from the UI too. A bearer_only caller still cannot steer it in
    either direction -- see the bearer test below.
    """
    from muxplex.settings import load_settings

    resp = client.patch("/api/settings", json={"input_allowed_sessions": ["agent-*"]})
    assert resp.status_code == 200
    assert resp.json()["input_allowed_sessions"] == ["agent-*"]
    assert load_settings()["input_allowed_sessions"] == ["agent-*"]

    # Narrowing all the way to deny-all is equally an operator's call.
    resp = client.patch("/api/settings", json={"input_allowed_sessions": []})
    assert resp.status_code == 200
    assert load_settings()["input_allowed_sessions"] == []


def test_operator_patch_widens_input_fence_end_to_end(
    client, settings_file, monkeypatch, tmux_calls
):
    """The FEATURE, end to end: an operator PATCHes both fence keys, then
    /input works -- no settings mocking, real load_settings via the
    redirected file.

    This is the counterpart to the bearer test below: same two PATCHes, same
    /input call, opposite outcome, and the ONLY difference is the credential.
    """
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])
    patch_resp = client.patch(
        "/api/settings",
        json={"input_enabled": True, "input_allowed_sessions": ["alpha"]},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["input_enabled"] is True

    resp = client.post("/api/sessions/alpha/input", json={"text": "hi"})
    assert resp.status_code == 200
    # The keystrokes really did reach (mocked) tmux.
    assert any("send-keys" in call for call in tmux_calls), (
        f"expected a send-keys argv after an operator widened the fence, got: {tmux_calls}"
    )


def test_bearer_only_patch_cannot_widen_input_fence_end_to_end(
    bearer_client, settings_file, monkeypatch, tmux_calls
):
    """The self-authorization attack, with the REAL attacker credential:
    a federation-Bearer-only caller PATCHes both fence keys, then calls /input.

    This is the security property the original cookie-based version of this
    test was reaching for but could not actually express -- a cookie caller
    is an operator, never the Bearer-key holder the fence exists to contain.
    Nothing on the auth path is stubbed (see the bearer_client fixture): the
    real AuthMiddleware authorizes the request and the real
    main._bearer_only_caller classifies it.

    The PATCH must be silently ignored for BOTH fence keys, so /input still
    403s and nothing reaches tmux.
    """
    from muxplex.settings import load_settings

    c, headers = bearer_client
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["alpha"])

    patch_resp = c.patch(
        "/api/settings",
        json={"input_enabled": True, "input_allowed_sessions": ["alpha"]},
        headers=headers,
    )
    # Authorized (not 401) -- the Bearer key satisfies the shared auth...
    assert patch_resp.status_code == 200
    # ...but neither fence key moved, on the wire or on disk.
    assert patch_resp.json()["input_enabled"] is False
    on_disk = load_settings()
    assert on_disk["input_enabled"] is False
    assert on_disk["input_allowed_sessions"] == ["*"]  # untouched default

    resp = c.post("/api/sessions/alpha/input", json={"text": "hi"}, headers=headers)
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


# ---------------------------------------------------------------------------
# input_allowed_for_session -- the single fence /input AND the terminal WS
# input gate (main.py's terminal_ws_proxy/client_to_ttyd) both evaluate.
# See terminal_input.py's docstring and docs/API_SEMANTICS.md's "terminal
# WS input fence" entry.
# ---------------------------------------------------------------------------


def test_input_allowed_for_session_false_when_disabled():
    settings = _settings(input_enabled=False, input_allowed_sessions=["alpha"])
    assert input_allowed_for_session("alpha", settings) is False


def test_input_allowed_for_session_false_when_not_allowlisted():
    settings = _settings(input_enabled=True, input_allowed_sessions=["beta"])
    assert input_allowed_for_session("alpha", settings) is False


def test_input_allowed_for_session_true_when_enabled_and_allowlisted():
    settings = _settings(input_enabled=True, input_allowed_sessions=["alpha"])
    assert input_allowed_for_session("alpha", settings) is True


def test_input_allowed_for_session_fails_closed_on_string_allowlist():
    """A non-list input_allowed_sessions (e.g. a stray string) must not
    silently widen to substring matching -- treated as empty (deny-all).

    This is the LIBRARY-level contract (tmux_kit.keys), evaluated on a raw
    settings dict, and it is unchanged. muxplex normalizes a bare string
    into a one-element list UPSTREAM of this call, in load_settings() --
    see settings.normalize_input_allowed_sessions() and the
    "bare-string form" tests at the bottom of this file. The two are not
    in conflict: the fence never receives a raw string on any real code
    path, and if one ever did reach it, it still denies rather than
    substring-matching.
    """
    settings = _settings(input_enabled=True, input_allowed_sessions="alpha")
    assert input_allowed_for_session("al", settings) is False


def test_input_allowed_for_session_fails_closed_on_truthy_string_enabled():
    """input_enabled: "false" (a truthy STRING) must disable, not enable --
    only the literal boolean True enables the fence.
    """
    settings = _settings(input_enabled="true", input_allowed_sessions=["alpha"])
    assert input_allowed_for_session("alpha", settings) is False


# ---------------------------------------------------------------------------
# The widened default (work item muxplex-ph0)
#
# `input_allowed_sessions` used to default to `[]` -- deny everything -- so
# flipping `input_enabled: true` hit a SECOND silent 403 wall and the
# operator had to enumerate session names by hand. The default is now "*".
#
# These tests are the acceptance criteria for that change, in order:
#   1. fresh install -> input_enabled False, allowlist "*"
#   2. flip input_enabled ONLY -> input into any session is accepted
#   3. narrow the list -> a session outside it is refused, allowlist named
#   4. the API (Bearer/agent) still cannot change either key
# ---------------------------------------------------------------------------


def test_fresh_install_defaults_gate_closed_but_allowlist_open(settings_file):
    """AC1: no settings file at all -> input_enabled False, allowlist "*".

    Reads through the REAL load_settings against a redirected (absent)
    file, so this covers the actual fresh-install path, not just the
    DEFAULT_SETTINGS literal.
    """
    from muxplex.settings import load_settings

    assert not settings_file.exists()
    loaded = load_settings()
    assert loaded["input_enabled"] is False
    # Normalized to the list form the fence requires -- the scalar default
    # never reaches the fence as a raw string.
    assert loaded["input_allowed_sessions"] == ["*"]
    assert input_allowed_for_session("anything", loaded) is False


def test_enabling_the_switch_alone_opens_every_session(settings_file):
    """AC2: operator sets input_enabled: true and changes NOTHING else.

    This is the whole point of the item. Writes a settings.json containing
    ONLY that one key -- exactly what a human editing the file by hand
    would do -- and asserts the fence opens for arbitrary session names.
    Before this change the same file produced a 403 for every session.
    """
    import json

    from muxplex.settings import load_settings

    settings_file.write_text(json.dumps({"input_enabled": True}))
    loaded = load_settings()
    for name in ("counter", "logtail", "sysmon", "some-other-session"):
        assert input_allowed_for_session(name, loaded) is True


def test_enabling_the_switch_alone_accepts_input_end_to_end(
    client, settings_file, monkeypatch, tmux_calls
):
    """AC2, at the endpoint, through the real settings file and real fence.

    No monkeypatched load_settings: the only thing standing between a
    stock install and a typed keystroke is the one line the operator wrote.
    """
    import json

    settings_file.write_text(json.dumps({"input_enabled": True}))
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["counter"])
    resp = client.post("/api/sessions/counter/input", json={"text": "hi"})
    assert resp.status_code == 200
    assert len(tmux_calls) == 1


def test_narrowing_the_allowlist_still_refuses_outside_sessions(
    client, settings_file, monkeypatch, tmux_calls
):
    """AC3: an operator who narrows the list gets the old behavior back.

    Widening the DEFAULT must not remove the ability to narrow. The 403
    must still name the allowlist as the cause, so the operator is pointed
    at the setting they actually changed.
    """
    import json

    settings_file.write_text(
        json.dumps({"input_enabled": True, "input_allowed_sessions": ["agent-*"]})
    )
    monkeypatch.setattr(
        "muxplex.main.get_session_list", lambda: ["agent-build", "my-own-shell"]
    )

    ok = client.post("/api/sessions/agent-build/input", json={"text": "hi"})
    assert ok.status_code == 200

    refused = client.post("/api/sessions/my-own-shell/input", json={"text": "hi"})
    assert refused.status_code == 403
    assert "input_allowed_sessions" in refused.json()["detail"]
    assert len(tmux_calls) == 1  # only the allowed one reached tmux


def test_narrowing_to_empty_list_still_denies_everything(settings_file):
    """AC3, the strictest narrowing: [] must still mean deny-all.

    The widened DEFAULT must not be confused with "empty means allow-all".
    An operator who deliberately writes [] is locking the door, and that
    reading is unchanged.
    """
    import json

    from muxplex.settings import load_settings

    settings_file.write_text(
        json.dumps({"input_enabled": True, "input_allowed_sessions": []})
    )
    loaded = load_settings()
    assert loaded["input_allowed_sessions"] == []
    assert input_allowed_for_session("counter", loaded) is False


def test_widened_default_did_not_move_either_key_out_of_local_only(settings_file):
    """AC4: the LOCAL_ONLY_KEYS partition still holds for BOTH keys.

    Guards the one thing that would turn this convenience change into a
    real vulnerability: the federation Bearer key IS the agent credential,
    so if either key became settable by THAT credential the agent could
    self-authorize typing into the human's own panes -- and with the
    allowlist now defaulting open, `input_enabled` is the ONLY thing left
    standing between a Bearer-key holder and RCE on every session.

    Scope note: both keys later joined settings.OPERATOR_SETTABLE_LOCAL_KEYS,
    so an OPERATOR-credentialed `PATCH /api/settings` may now set them (see
    test_patch_settings_operator_enables_input_cosubmitted_key_applies).
    That carve-out is opt-in per call and lives entirely in the HTTP layer:
    `patch_settings()` called bare -- as below, and as the CLI calls it --
    still drops both keys, and federation sync still never carries them.
    Those two properties are exactly what this test pins.
    """
    import json

    from muxplex.settings import load_settings

    assert "input_enabled" in LOCAL_ONLY_KEYS
    assert "input_allowed_sessions" in LOCAL_ONLY_KEYS
    assert LOCAL_ONLY_KEYS.isdisjoint(SYNCABLE_KEYS)

    # And prove it behaviorally, not just by set membership.
    from muxplex.settings import apply_synced_settings, patch_settings

    settings_file.write_text(json.dumps({"input_enabled": False}))

    patched = patch_settings(
        {"input_enabled": True, "input_allowed_sessions": ["victim-shell"]}
    )
    assert patched["input_enabled"] is False
    assert patched["input_allowed_sessions"] == ["*"]

    # Federation sync is the other remote door -- also refused.
    synced = apply_synced_settings(
        {"input_enabled": True, "input_allowed_sessions": ["victim-shell"]},
        incoming_timestamp=9_999_999_999.0,
    )
    assert synced["input_enabled"] is False
    assert synced["input_allowed_sessions"] == ["*"]

    assert load_settings()["input_enabled"] is False


# ---------------------------------------------------------------------------
# The bare-string form (settings.normalize_input_allowed_sessions)
#
# The fence requires a list and treats any non-list as empty (deny-all).
# The default is the bare string "*", and that is also what a human
# hand-writes after reading the docs -- so a raw string must never reach
# the fence. Normalization happens in load_settings(), upstream of it.
# ---------------------------------------------------------------------------


def test_normalize_wraps_bare_string_into_one_element_list():
    from muxplex.settings import normalize_input_allowed_sessions as norm

    assert norm("*") == ["*"]
    assert norm("agent-shell") == ["agent-shell"]
    assert norm("agent-*") == ["agent-*"]


def test_normalize_strips_surrounding_whitespace():
    """A session name can never carry leading/trailing whitespace
    (is_valid_session_name restricts the charset), so stripping can only
    recover intent -- ' * ' is unmistakably "*", and leaving it unstripped
    would deny everything with no diagnostic."""
    from muxplex.settings import normalize_input_allowed_sessions as norm

    assert norm(" * ") == ["*"]
    assert norm("\tagent-*\n") == ["agent-*"]


def test_normalize_empty_string_denies_everything():
    """ "" is not "allow all" -- it collapses to [], the same deny-all an
    empty list already means."""
    from muxplex.settings import normalize_input_allowed_sessions as norm

    assert norm("") == []
    assert norm("   ") == []


def test_normalize_does_not_invent_a_comma_syntax():
    """ "a,b" becomes ONE pattern, which matches nothing (a comma is not a
    legal session-name character). Inventing an undocumented mini-language
    is worse than a value that plainly fails closed."""
    from muxplex.settings import normalize_input_allowed_sessions as norm

    patterns = norm("counter,logtail")
    assert patterns == ["counter,logtail"]
    assert isinstance(patterns, list)
    assert session_matches_allowlist("counter", patterns) is False
    assert session_matches_allowlist("logtail", patterns) is False


def test_normalize_passes_lists_and_junk_through_untouched():
    """Lists are already the canonical form. Non-str, non-list values have
    no recoverable operator intent and are left alone -- the fence then
    treats them as empty (fail closed)."""
    from muxplex.settings import normalize_input_allowed_sessions as norm

    assert norm(["agent-*", "counter"]) == ["agent-*", "counter"]
    assert norm([]) == []
    assert norm(123) == 123
    assert norm(None) is None
    assert norm({"a": 1}) == {"a": 1}


def test_hand_written_star_string_in_settings_file_actually_works(
    client, settings_file, monkeypatch, tmux_calls
):
    """The regression this normalization exists to prevent.

    An operator reads the docs ("the allowlist defaults to *"), writes the
    literal string into settings.json, and it must WORK. Without
    normalization the list-only fence reads a raw string as empty and
    returns 403 -- silently doing the exact opposite of what was written,
    in the one file an operator is supposed to edit to widen this fence.
    """
    import json

    settings_file.write_text(
        json.dumps({"input_enabled": True, "input_allowed_sessions": "*"})
    )
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["counter"])
    resp = client.post("/api/sessions/counter/input", json={"text": "hi"})
    assert resp.status_code == 200
    assert len(tmux_calls) == 1


def test_hand_written_single_name_string_is_not_substring_matched(
    client, settings_file, monkeypatch, tmux_calls
):
    """Normalization must not reintroduce substring matching.

    A hand-written "alpha" now allows exactly the session `alpha` -- it
    must NOT allow `al`, which is what a naive `name in allowed` on a raw
    string would have done. That widening is the reason the fence rejects
    raw strings in the first place, and it stays rejected here.
    """
    import json

    settings_file.write_text(
        json.dumps({"input_enabled": True, "input_allowed_sessions": "alpha"})
    )
    monkeypatch.setattr("muxplex.main.get_session_list", lambda: ["al", "alpha"])

    refused = client.post("/api/sessions/al/input", json={"text": "hi"})
    assert refused.status_code == 403
    assert tmux_calls == []

    ok = client.post("/api/sessions/alpha/input", json={"text": "hi"})
    assert ok.status_code == 200
