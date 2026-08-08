"""Tests for muxplex/cgroup_escape.py -- the fix for "Two ways to destroy
every live tmux session on this host", mechanism #1 (AGENTS.md).

These tests never invoke a real ``systemd-run`` -- every subprocess call is
mocked. The real-mechanism proof (a genuine tmux server landing outside
muxplex's cgroup, and surviving a `KillMode=mixed` restart) lives in the DTU
run described in this fix's report, not in this unit suite.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# The module moved to tmux_kit.cgroup at extraction stage S1 (plan §7.1);
# these tests are the 44-session incident's guards and move WITH their code
# (plan §8.4) -- they patch module INTERNALS (sys, shutil, environment_mode),
# which only work against the defining module, not the re-export shim.
from tmux_kit import cgroup as cgroup_escape


@pytest.fixture(autouse=True)
def _reset_probe_cache():
    """Every test starts with a clean (unprobed) cache and leaves one behind."""
    cgroup_escape.reset_probe_cache_for_tests()
    yield
    cgroup_escape.reset_probe_cache_for_tests()


def _mock_proc(returncode: int = 0, stderr: bytes = b""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(b"", stderr))
    return proc


# ---------------------------------------------------------------------------
# environment_mode() -- the three branches
# ---------------------------------------------------------------------------


def test_environment_mode_not_applicable_on_macos(monkeypatch):
    """macOS/launchd: no cgroups exist on this platform at all."""
    monkeypatch.setattr(cgroup_escape.sys, "platform", "darwin")
    assert cgroup_escape.environment_mode() == "not-applicable"


def test_environment_mode_not_applicable_without_xdg_runtime_dir(monkeypatch):
    """Linux without a usable systemd --user session (e.g. `tower`: root via
    a plain boot script, no systemd unit at all) -- nothing to escape from."""
    monkeypatch.setattr(cgroup_escape.sys, "platform", "linux")
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    assert cgroup_escape.environment_mode() == "not-applicable"


def test_environment_mode_not_applicable_without_systemd_run_binary(monkeypatch):
    """XDG_RUNTIME_DIR present but `systemd-run` not on PATH -- still N/A."""
    monkeypatch.setattr(cgroup_escape.sys, "platform", "linux")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    monkeypatch.setattr(cgroup_escape.shutil, "which", lambda _: None)
    assert cgroup_escape.environment_mode() == "not-applicable"


def test_environment_mode_scope_candidate_when_both_present(monkeypatch):
    """Linux + XDG_RUNTIME_DIR + systemd-run on PATH -- the fix applies."""
    monkeypatch.setattr(cgroup_escape.sys, "platform", "linux")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    monkeypatch.setattr(cgroup_escape.shutil, "which", lambda _: "/usr/bin/systemd-run")
    assert cgroup_escape.environment_mode() == "scope-candidate"


# ---------------------------------------------------------------------------
# should_escape() -- combines the static check with the cached real probe
# ---------------------------------------------------------------------------


async def test_should_escape_false_when_not_applicable_skips_probe(monkeypatch):
    """When not-applicable, should_escape() must be False and must NOT spawn
    a probe subprocess at all -- there is nothing to test."""
    monkeypatch.setattr(cgroup_escape, "environment_mode", lambda: "not-applicable")
    with patch("asyncio.create_subprocess_exec", new=AsyncMock()) as mock_create:
        result = await cgroup_escape.should_escape()
    assert result is False
    mock_create.assert_not_called()


async def test_should_escape_true_when_probe_succeeds(monkeypatch):
    monkeypatch.setattr(cgroup_escape, "environment_mode", lambda: "scope-candidate")
    proc = _mock_proc(returncode=0)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        result = await cgroup_escape.should_escape()
    assert result is True


async def test_should_escape_false_when_probe_returncode_nonzero(monkeypatch, caplog):
    """systemd --user session looked available but the self-test failed --
    must be False AND logged at CRITICAL, never silent."""
    monkeypatch.setattr(cgroup_escape, "environment_mode", lambda: "scope-candidate")
    proc = _mock_proc(returncode=1, stderr=b"Failed to create bus connection")
    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
        caplog.at_level("CRITICAL"),
    ):
        result = await cgroup_escape.should_escape()
    assert result is False
    assert any(rec.levelname == "CRITICAL" for rec in caplog.records), (
        "a failed self-test must log at CRITICAL, not be silently swallowed"
    )


async def test_should_escape_false_when_probe_raises_oserror(monkeypatch, caplog):
    """systemd-run binary vanished between the static check and the actual
    call (race) -- must be False AND logged at CRITICAL, never silent."""
    monkeypatch.setattr(cgroup_escape, "environment_mode", lambda: "scope-candidate")
    with (
        patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=FileNotFoundError("no such file")),
        ),
        caplog.at_level("CRITICAL"),
    ):
        result = await cgroup_escape.should_escape()
    assert result is False
    assert any(rec.levelname == "CRITICAL" for rec in caplog.records)


async def test_should_escape_caches_probe_result_across_calls(monkeypatch):
    """The real probe must run at most ONCE per process lifetime -- session
    creation and ttyd spawn happen far too often to re-probe every time."""
    monkeypatch.setattr(cgroup_escape, "environment_mode", lambda: "scope-candidate")
    proc = _mock_proc(returncode=0)
    with patch(
        "asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)
    ) as mock_create:
        first = await cgroup_escape.should_escape()
        second = await cgroup_escape.should_escape()
    assert first is True
    assert second is True
    assert mock_create.call_count == 1, (
        "should_escape() must cache the probe result, not re-probe every call"
    )


# ---------------------------------------------------------------------------
# argv wrapping
# ---------------------------------------------------------------------------


def test_wrap_exec_argv_prepends_scope_prefix():
    wrapped = cgroup_escape.wrap_exec_argv(["ttyd", "-W", "tmux", "attach", "-t", "x"])
    assert wrapped == [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "--collect",
        "--same-dir",
        "--",
        "ttyd",
        "-W",
        "tmux",
        "attach",
        "-t",
        "x",
    ]


def test_wrap_shell_argv_wraps_command_in_sh_c():
    wrapped = cgroup_escape.wrap_shell_argv("tmux new-session -d -s foo")
    assert wrapped == [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "--collect",
        "--same-dir",
        "--",
        "sh",
        "-c",
        "tmux new-session -d -s foo",
    ]
