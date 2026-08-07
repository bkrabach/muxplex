"""muxplex/focus.py -- server-side foreground-focus for the muxplex PWA window.

One responsibility: resolve whether THIS host can raise its own muxplex PWA
window, and do it. Owns platform dispatch and nothing else -- no HTTP, no
settings loading, no auth. See ``main.py``'s ``POST /api/focus`` for the
endpoint that calls this, and ``docs/plans/2026-08-05-focus-grab-plan.md``
for the full design.

Platform feasibility (the design's honest table, condensed):

- **macOS**: SHIP. muxplex's launchd agent is bootstrapped into the
  ``gui/$UID`` Aqua domain (``service.py``'s launchd install), which has
  access to the window server. ``open -a <app>`` activates the app if
  running, launches it if not -- no AppleScript/Automation permission
  prompt, unlike ``osascript``.
- **Linux/X11**: 501. muxplex's systemd user service does not reliably carry
  ``DISPLAY``/``XAUTHORITY`` (desktop-dependent), and there is no
  muxplex-owned mechanism to drive ``wmctrl``/``xdotool`` even when it does.
- **Linux/Wayland**: 501, structurally. ``xdg-activation-v1`` requires the
  REQUESTING process to be a Wayland client holding a surface and an input
  serial -- muxplex is a headless HTTP server, so it cannot mint a token to
  hand to anything, on any compositor.
- **WSL**: 501. The window being raised is a Windows browser window; there
  is no Linux window for this process to activate.
- **Windows native**: n/a -- muxplex has no Windows port at all.

Only macOS has an implementation. Every other platform is an honest,
explicit, non-silent 501 -- never a success-shaped no-op.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import sys
from dataclasses import dataclass

_log = logging.getLogger(__name__)

# `open -a` normally returns in well under a second; if it hangs, give up
# rather than tying up the request. Mirrors muxplex-deck's own
# FOCUS_TIMEOUT_SECONDS (focus.py), the implementation this subsumes.
FOCUS_TIMEOUT_SECONDS = 2.0


class FocusUnsupportedError(Exception):
    """This platform has no focus-raise implementation. Carries the honest reason."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class FocusFailedError(Exception):
    """The platform's mechanism ran but failed. Carries the real stderr/exception text."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class FocusCapability:
    """This host's ability to raise its own muxplex PWA window.

    ``supported=False`` always carries a non-empty, honest, user-facing
    ``reason`` -- see ``resolve_focus_capability()``.
    """

    supported: bool
    platform: str
    mechanism: str
    reason: str


def _is_wsl() -> bool:
    """True when running under WSL (duplicated from ``ttyd.is_wsl()`` deliberately.

    Same technique (``"microsoft" in platform.uname().release.lower()``),
    NOT imported across modules: ttyd's copy guards an ``AF_UNIX`` bind
    failure, this one picks a diagnostic platform string for a 501 reason --
    two callers with different purposes, so a future tightening of either
    must not silently move the other (see ``AGENTS.md``'s duplication
    rationale for ``views.matches_name_pattern`` vs
    ``terminal_input.session_matches_allowlist``).
    """
    return "microsoft" in platform.uname().release.lower()


def resolve_focus_capability() -> FocusCapability:
    """Resolve this host's ability to raise its own PWA window.

    Pure -- reads only ``sys.platform``/``platform.uname()``. No I/O, no
    settings. Safe to call on every request.
    """
    if sys.platform == "darwin":
        return FocusCapability(
            supported=True, platform="darwin", mechanism="open -a", reason=""
        )
    if _is_wsl():
        return FocusCapability(
            supported=False,
            platform="wsl",
            mechanism="",
            reason=(
                "muxplex is running under WSL. The muxplex PWA window is a "
                "Windows browser window, not a Linux one -- there is nothing "
                "on this side of the WSL boundary for muxplex to raise."
            ),
        )
    if sys.platform.startswith("linux"):
        return FocusCapability(
            supported=False,
            platform="linux",
            mechanism="",
            reason=(
                "Foreground focus is not supported on Linux. X11 is "
                "unreliable from a systemd user service (DISPLAY/XAUTHORITY "
                "are not guaranteed to be present), and Wayland has no "
                "portable activation path a headless server process can use "
                "(xdg-activation-v1 requires the requesting process to "
                "already be a Wayland client holding a surface and input "
                "serial)."
            ),
        )
    return FocusCapability(
        supported=False,
        platform=sys.platform,
        mechanism="",
        reason=f"Foreground focus is not supported on this platform ({sys.platform!r}).",
    )


def macos_focus_command(app_name: str) -> list[str]:
    """The exact argv the macOS path runs to activate *app_name*.

    ``open -a`` (never ``osascript``) -- see the module docstring. List-args
    for ``create_subprocess_exec``, never a shell string.
    """
    return ["open", "-a", app_name]


async def raise_window(app_name: str) -> None:
    """Raise (or launch) the configured PWA window on this host.

    Args:
        app_name: non-empty, sourced from ``settings["focus_app"]`` --
            NEVER from a request body. The caller (``main.py``'s endpoint)
            never accepts a target of any kind; this function has no
            opinion on where its argument came from and does not validate
            that it's non-empty (the endpoint's ``409 focus_not_configured``
            check happens before this is ever called).

    Raises:
        FocusUnsupportedError: this platform has no implementation.
        FocusFailedError: the mechanism ran and failed (non-zero exit,
            timeout, or the exec itself failed) -- carries the real stderr
            or exception text.

    On macOS this LAUNCHES the app if it is not already running -- ``open
    -a`` does not distinguish "raise" from "launch", and the spec adopts
    that behavior deliberately (see the plan's \u00a73.4): a caller asking to
    bring the PWA to the foreground means it either way.
    """
    capability = resolve_focus_capability()
    if not capability.supported:
        raise FocusUnsupportedError(capability.reason)

    command = macos_focus_command(app_name)
    _log.debug("focus: running %s", command)
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=FOCUS_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise FocusFailedError(
                f"{' '.join(command)} did not complete within "
                f"{FOCUS_TIMEOUT_SECONDS}s (timed out)"
            )
    except OSError as exc:
        # The exec itself failed (e.g. ENOENT -- `open` missing, which
        # should not happen on a real macOS host, but never silently
        # swallow it).
        raise FocusFailedError(f"failed to run {' '.join(command)}: {exc}") from exc

    if proc.returncode != 0:
        # Most common cause: `app_name` doesn't match any installed app
        # ("Unable to find application named ..."). Surface the real
        # stderr so the operator can fix their settings.json -- this is
        # the text a human needs verbatim, not a generic failure message.
        detail = (stderr or b"").decode("utf-8", errors="replace").strip() or (
            f"open -a exited {proc.returncode} with no stderr"
        )
        raise FocusFailedError(detail)
