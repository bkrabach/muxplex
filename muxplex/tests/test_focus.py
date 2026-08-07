"""
Tests for POST /api/focus -- server-side foreground-focus for the muxplex PWA.

See docs/plans/2026-08-05-focus-grab-plan.md for the full design. The most
important test in this file is `test_focus_endpoint_accepts_no_target`
(section 6.1/9.2): the endpoint's entire security posture rests on the
caller never being able to name the target, so that property is asserted
directly rather than left to convention.
"""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from muxplex.focus import FocusCapability, FocusFailedError
from muxplex.main import app
from muxplex.settings import DEFAULT_SETTINGS, LOCAL_ONLY_KEYS, SYNCABLE_KEYS

# ---------------------------------------------------------------------------
# Fixtures -- mirror test_input.py's isolation pattern
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
    s = copy.deepcopy(DEFAULT_SETTINGS)
    s.update(overrides)
    return s


def _darwin_supported(monkeypatch) -> None:
    """Force main.focus.resolve_focus_capability() to report macOS/supported."""
    monkeypatch.setattr(
        "muxplex.main.focus.resolve_focus_capability",
        lambda: FocusCapability(
            supported=True, platform="darwin", mechanism="open -a", reason=""
        ),
    )


def _linux_unsupported(monkeypatch, reason: str = "no can do") -> None:
    monkeypatch.setattr(
        "muxplex.main.focus.resolve_focus_capability",
        lambda: FocusCapability(
            supported=False, platform="linux", mechanism="", reason=reason
        ),
    )


# ---------------------------------------------------------------------------
# settings.py -- default + fence membership
# ---------------------------------------------------------------------------


def test_focus_app_default_is_empty_string():
    assert DEFAULT_SETTINGS["focus_app"] == ""


def test_focus_app_is_local_only():
    """LOCAL_ONLY_KEYS membership is the whole security design -- see §6.2."""
    assert "focus_app" in LOCAL_ONLY_KEYS


def test_focus_app_is_not_syncable():
    """A federation peer's settings sync must never carry focus_app across hosts."""
    assert "focus_app" not in SYNCABLE_KEYS


def test_patch_settings_ignores_focus_app(monkeypatch, tmp_path):
    """PATCH /api/settings must silently ignore focus_app (with a warning log),
    exactly like new_session_template/session_commands."""
    from muxplex.settings import SETTINGS_PATH, load_settings, patch_settings

    monkeypatch.setattr("muxplex.settings.SETTINGS_PATH", tmp_path / "settings.json")
    assert SETTINGS_PATH == tmp_path / "settings.json"  # sanity: patched module attr
    patch_settings({"focus_app": "Evil App"})
    assert load_settings()["focus_app"] == ""


def test_apply_synced_settings_never_applies_focus_app(tmp_path, monkeypatch):
    """A federation peer's synced payload must never write focus_app locally."""
    from muxplex.settings import apply_synced_settings, load_settings

    monkeypatch.setattr("muxplex.settings.SETTINGS_PATH", tmp_path / "settings.json")
    apply_synced_settings({"focus_app": "Attacker App"}, 999999999.0, 0.0)
    assert load_settings()["focus_app"] == ""


# ---------------------------------------------------------------------------
# §6.1 -- the single most important test: no target of any kind
# ---------------------------------------------------------------------------


def test_focus_endpoint_accepts_no_target(client, monkeypatch):
    """Posting a body that NAMES an app must not change which app is invoked.

    This is the entire security design (§6.1): the endpoint reads its target
    ONLY from settings["focus_app"], never from the request. A caller
    supplying {"app": "Calculator"} must still only be able to trigger
    whatever the operator configured.
    """
    _darwin_supported(monkeypatch)
    monkeypatch.setattr(
        "muxplex.main.load_settings", lambda: _settings(focus_app="Muxplex")
    )

    calls: list[str] = []

    async def fake_raise_window(app_name: str) -> None:
        calls.append(app_name)

    monkeypatch.setattr("muxplex.main.focus.raise_window", fake_raise_window)

    resp = client.post("/api/focus", json={"app": "Calculator"})
    assert resp.status_code == 200
    assert resp.json()["app"] == "Muxplex"
    assert calls == ["Muxplex"]  # NEVER "Calculator"


def test_focus_endpoint_accepts_no_query_param_target(client, monkeypatch):
    """A ?app=... query parameter must be equally inert -- no target of ANY kind."""
    _darwin_supported(monkeypatch)
    monkeypatch.setattr(
        "muxplex.main.load_settings", lambda: _settings(focus_app="Muxplex")
    )

    calls: list[str] = []

    async def fake_raise_window(app_name: str) -> None:
        calls.append(app_name)

    monkeypatch.setattr("muxplex.main.focus.raise_window", fake_raise_window)

    resp = client.post("/api/focus?app=Calculator")
    assert resp.status_code == 200
    assert calls == ["Muxplex"]


# ---------------------------------------------------------------------------
# Response ordering (§6.4/§3.2): platform BEFORE configuration
# ---------------------------------------------------------------------------


def test_unsupported_platform_never_returns_200(client, monkeypatch):
    _linux_unsupported(monkeypatch, reason="Linux is not supported.")
    monkeypatch.setattr(
        "muxplex.main.load_settings", lambda: _settings(focus_app="Muxplex")
    )
    resp = client.post("/api/focus")
    assert resp.status_code == 501
    body = resp.json()
    assert body["focus_unsupported_platform"] is True
    assert body["platform"] == "linux"
    assert "Linux is not supported." in body["detail"]


def test_platform_checked_before_configuration(client, monkeypatch):
    """Unsupported platform + unconfigured focus_app -> 501, never 409 (§6.4).

    Deliberate: the platform answer is public/non-sensitive; the
    configuration answer is a fact about this host's local settings. A
    caller on an unsupported host must learn nothing about whether an
    operator configured anything.
    """
    _linux_unsupported(monkeypatch)
    monkeypatch.setattr("muxplex.main.load_settings", lambda: _settings(focus_app=""))
    resp = client.post("/api/focus")
    assert resp.status_code == 501
    assert resp.json()["focus_unsupported_platform"] is True


@pytest.mark.parametrize("bad_value", ["", None, 123, [], {}])
def test_unconfigured_returns_409_not_200(client, monkeypatch, bad_value):
    """Empty, missing, or non-string focus_app -> 409, fail-closed.

    Whitespace-only ("   ") is deliberately NOT in this list -- it's a
    non-empty str, so it counts as "configured" (see
    test_whitespace_only_focus_app_is_treated_as_configured_if_non_empty
    below): only the literal empty string or a non-string value are
    treated as unconfigured.
    """
    _darwin_supported(monkeypatch)
    settings = _settings()
    if bad_value is None:
        del settings["focus_app"]
    else:
        settings["focus_app"] = bad_value
    monkeypatch.setattr("muxplex.main.load_settings", lambda: settings)

    resp = client.post("/api/focus")
    assert resp.status_code == 409
    body = resp.json()
    assert body["focus_not_configured"] is True
    assert "focus_app" in body["detail"]
    assert "settings.json" in body["detail"]


def test_whitespace_only_focus_app_is_treated_as_configured_if_non_empty(
    client, monkeypatch
):
    """A whitespace string IS a non-empty str -- the endpoint does not strip it.

    Documents the exact boundary: only the empty string (or a non-string)
    counts as unconfigured. `open -a "   "` failing is then a 502
    focus_failed, not a 409 -- surfaced honestly rather than silently
    "fixed" by trimming a value the operator actually wrote.
    """
    _darwin_supported(monkeypatch)
    monkeypatch.setattr(
        "muxplex.main.load_settings", lambda: _settings(focus_app="   ")
    )

    async def fake_raise_window(app_name: str) -> None:
        raise FocusFailedError('Unable to find application named "   "')

    monkeypatch.setattr("muxplex.main.focus.raise_window", fake_raise_window)

    resp = client.post("/api/focus")
    assert resp.status_code == 502
    assert resp.json()["focus_failed"] is True


# ---------------------------------------------------------------------------
# Mechanism failure -> 502, real stderr surfaced
# ---------------------------------------------------------------------------


def test_mechanism_failure_surfaces_stderr(client, monkeypatch):
    _darwin_supported(monkeypatch)
    monkeypatch.setattr(
        "muxplex.main.load_settings", lambda: _settings(focus_app="NoSuchApp")
    )

    async def fake_raise_window(app_name: str) -> None:
        raise FocusFailedError('Unable to find application named "NoSuchApp"')

    monkeypatch.setattr("muxplex.main.focus.raise_window", fake_raise_window)

    resp = client.post("/api/focus")
    assert resp.status_code == 502
    body = resp.json()
    assert body["focus_failed"] is True
    assert "NoSuchApp" in body["detail"]


# ---------------------------------------------------------------------------
# Success
# ---------------------------------------------------------------------------


def test_success_returns_ok_platform_and_app(client, monkeypatch):
    _darwin_supported(monkeypatch)
    monkeypatch.setattr(
        "muxplex.main.load_settings", lambda: _settings(focus_app="Muxplex")
    )

    async def fake_raise_window(app_name: str) -> None:
        return None

    monkeypatch.setattr("muxplex.main.focus.raise_window", fake_raise_window)

    resp = client.post("/api/focus")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "platform": "darwin", "app": "Muxplex"}


def test_focus_endpoint_ignores_empty_json_body(client, monkeypatch):
    """No body at all is the documented contract -- an empty JSON body must work too."""
    _darwin_supported(monkeypatch)
    monkeypatch.setattr(
        "muxplex.main.load_settings", lambda: _settings(focus_app="Muxplex")
    )

    async def fake_raise_window(app_name: str) -> None:
        return None

    monkeypatch.setattr("muxplex.main.focus.raise_window", fake_raise_window)

    resp = client.post("/api/focus", json={})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/instance-info -- capability advertisement (§3.3)
# ---------------------------------------------------------------------------


def test_instance_info_focus_block_supported_and_configured(client, monkeypatch):
    _darwin_supported(monkeypatch)
    monkeypatch.setattr(
        "muxplex.main.load_settings", lambda: _settings(focus_app="Muxplex")
    )
    resp = client.get("/api/instance-info")
    assert resp.status_code == 200
    focus_block = resp.json()["focus"]
    assert focus_block == {
        "supported": True,
        "configured": True,
        "platform": "darwin",
        "mechanism": "open -a",
    }
    # The app name VALUE must never appear on this unauthenticated endpoint.
    assert "Muxplex" not in resp.text


def test_instance_info_focus_block_unsupported_and_unconfigured(client, monkeypatch):
    _linux_unsupported(monkeypatch, reason="nope")
    monkeypatch.setattr("muxplex.main.load_settings", lambda: _settings(focus_app=""))
    resp = client.get("/api/instance-info")
    assert resp.status_code == 200
    focus_block = resp.json()["focus"]
    assert focus_block == {
        "supported": False,
        "configured": False,
        "platform": "linux",
        "mechanism": "",
    }


def test_instance_info_is_unauthenticated_for_focus_block(monkeypatch):
    """Public endpoint -- no cookie/Bearer needed to see the focus capability block."""
    _darwin_supported(monkeypatch)
    monkeypatch.setattr(
        "muxplex.main.load_settings", lambda: _settings(focus_app="Muxplex")
    )
    with TestClient(app) as c:
        resp = c.get("/api/instance-info")
    assert resp.status_code == 200
    assert "focus" in resp.json()


# ---------------------------------------------------------------------------
# focus.py -- pure unit tests (no HTTP)
# ---------------------------------------------------------------------------


def test_resolve_focus_capability_darwin(monkeypatch):
    from muxplex import focus as focus_mod

    monkeypatch.setattr(focus_mod.sys, "platform", "darwin")
    cap = focus_mod.resolve_focus_capability()
    assert cap.supported is True
    assert cap.platform == "darwin"
    assert cap.mechanism == "open -a"
    assert cap.reason == ""


def test_resolve_focus_capability_linux(monkeypatch):
    from muxplex import focus as focus_mod

    monkeypatch.setattr(focus_mod.sys, "platform", "linux")
    monkeypatch.setattr(focus_mod, "_is_wsl", lambda: False)
    cap = focus_mod.resolve_focus_capability()
    assert cap.supported is False
    assert cap.platform == "linux"
    assert cap.reason  # non-empty, honest reason


def test_resolve_focus_capability_wsl_reports_its_own_platform_string(monkeypatch):
    from muxplex import focus as focus_mod

    monkeypatch.setattr(focus_mod.sys, "platform", "linux")
    monkeypatch.setattr(focus_mod, "_is_wsl", lambda: True)
    cap = focus_mod.resolve_focus_capability()
    assert cap.supported is False
    assert cap.platform == "wsl"
    assert "WSL" in cap.reason


def test_resolve_focus_capability_unknown_platform(monkeypatch):
    from muxplex import focus as focus_mod

    monkeypatch.setattr(focus_mod.sys, "platform", "freebsd13")
    cap = focus_mod.resolve_focus_capability()
    assert cap.supported is False
    assert cap.platform == "freebsd13"
    assert "freebsd13" in cap.reason


def test_macos_focus_command_is_argv_never_shell():
    """Shaped after test_input.py:316 -- the mechanism must be argv, not a shell
    string, even for a hostile app_name."""
    from muxplex.focus import macos_focus_command

    hostile = "; rm -rf / && $(reboot) `id` | tee /etc/passwd"
    assert macos_focus_command(hostile) == ["open", "-a", hostile]


@pytest.mark.asyncio
async def test_raise_window_uses_create_subprocess_exec_never_shell(monkeypatch):
    """The macOS path must call create_subprocess_exec with exactly
    ("open", "-a", app_name) -- argv, never create_subprocess_shell."""
    from muxplex import focus as focus_mod

    monkeypatch.setattr(focus_mod.sys, "platform", "darwin")

    captured: dict = {}

    class _FakeProc:
        returncode = 0

        async def communicate(self):
            return b"", b""

        def kill(self):
            pass

        async def wait(self):
            return None

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        return _FakeProc()

    monkeypatch.setattr(
        focus_mod.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    hostile = "; rm -rf / && $(reboot) `id` | tee /etc/passwd"
    await focus_mod.raise_window(hostile)
    assert captured["args"] == ("open", "-a", hostile)


@pytest.mark.asyncio
async def test_raise_window_unsupported_platform_raises(monkeypatch):
    from muxplex import focus as focus_mod

    monkeypatch.setattr(focus_mod.sys, "platform", "linux")
    monkeypatch.setattr(focus_mod, "_is_wsl", lambda: False)
    with pytest.raises(focus_mod.FocusUnsupportedError):
        await focus_mod.raise_window("Muxplex")


@pytest.mark.asyncio
async def test_raise_window_nonzero_exit_raises_focus_failed_with_stderr(monkeypatch):
    from muxplex import focus as focus_mod

    monkeypatch.setattr(focus_mod.sys, "platform", "darwin")

    class _FakeProc:
        returncode = 1

        async def communicate(self):
            return b"", b'Unable to find application named "NoSuchApp"'

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(
        focus_mod.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    with pytest.raises(focus_mod.FocusFailedError) as exc_info:
        await focus_mod.raise_window("NoSuchApp")
    assert "NoSuchApp" in exc_info.value.detail
