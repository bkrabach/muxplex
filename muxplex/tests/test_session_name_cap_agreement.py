"""The session-name cap must be ONE number, not several that happen to match.

WHY THIS FILE EXISTS. muxplex's session-name length cap is written down in
places that cannot see each other:

  1. amplifier-workspace  (muxplex-27o) -- derives it from the filesystem's
     NAME_MAX at call time; not importable here, so it is out of this file's
     reach and is named only for context.
  2. tmux-kit             (muxplex-i1r) -- an EXTERNAL pinned dependency;
     ``tmux_kit.names`` is the cap muxplex's server actually enforces.
  3. muxplex's frontend   (muxplex-1vz) -- ``SESSION_NAME_MAX_BYTES`` in
     ``frontend/app.js``, a JavaScript literal no Python test read until now.

Those three carried 32, 64 and 255 respectively, and that disagreement WAS the
bug: amplifier-workspace silently truncated at 32, tmux-kit rejected at 65, and
the input field accepted 255. They were reconciled onto 255 -- the filesystem's
real NAME_MAX in bytes -- but nothing turned red when they disagreed, so
nothing stops the next person from moving one and not the others.

This file is that missing red. It reads the JavaScript constant out of app.js
as source text (the same technique test_frontend_js.py uses) and compares it
against the cap the INSTALLED tmux-kit actually enforces, so a drift in either
direction fails here rather than in a user's browser.

THE ONE RECORDED DISAGREEMENT. As of this writing they legitimately do NOT
agree, and that is deliberate rather than accidental:

    app.js   255   (the real filesystem limit -- muxplex-1vz)
    tmux-kit  64   (pinned at tmux-kit==0.4.0, which predates the raise)

tmux-kit's raise to 255 landed on a branch but was never published to PyPI
(publishing is an irreversible external action and remains the owner's call --
see muxplex-i1r's resolution), so muxplex's pin cannot move yet. That exact
pair is recorded below as a pinned-open exception; ANY other disagreement
fails. When the pin is finally bumped and the two agree,
``test_recorded_disagreement_window_is_still_open`` fails on purpose, so the
exception cannot outlive its reason and quietly mask a real regression back to
this state.

Read-only source and metadata inspection; no tmux, no network, no subprocess.
"""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

import tmux_kit.names as _kit_names

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_JS_PATH = _REPO_ROOT / "muxplex" / "frontend" / "app.js"
_MAIN_PY_PATH = _REPO_ROOT / "muxplex" / "main.py"

_APP_JS: str = _APP_JS_PATH.read_text(encoding="utf-8")
_MAIN_PY: str = _MAIN_PY_PATH.read_text(encoding="utf-8")

# Paths as a human should see them in a failure message -- repo-relative, not
# the absolute path of whatever machine happened to run the suite.
_APP_JS_REL = "muxplex/frontend/app.js"
_MAIN_PY_REL = "muxplex/main.py"

# ---------------------------------------------------------------------------
# The pinned-open exception. Delete BOTH of these (and the two tests that
# reference them) in the same change that bumps the tmux-kit pin.
# ---------------------------------------------------------------------------
_RECORDED_APP_JS_CAP = 255
_RECORDED_KIT_CAP = 64
_RECORDED_REASON = (
    "tmux-kit's raise from 64 to 255 (muxplex-i1r) landed on a branch but was "
    "never published to PyPI, so muxplex still pins tmux-kit==0.4.0. Names of "
    "65..255 characters are accepted by the input and refused by the server "
    "with a visible 400 until that pin moves."
)


def _installed_tmux_kit_version() -> str:
    try:
        return _pkg_version("tmux-kit")
    except PackageNotFoundError:  # pragma: no cover - tmux-kit is a hard dep
        return "unknown"


def _app_js_cap() -> tuple[int, int]:
    """Return (cap, 1-based line number) for app.js's SESSION_NAME_MAX_BYTES."""
    match = re.search(
        r"^const\s+SESSION_NAME_MAX_BYTES\s*=\s*(\d+)\s*;", _APP_JS, re.MULTILINE
    )
    assert match is not None, (
        f"could not find `const SESSION_NAME_MAX_BYTES = <number>;` in "
        f"{_APP_JS_REL}. It is the frontend's copy of the session-name cap and "
        f"the whole point of this file is that it must agree with tmux-kit's. "
        f"If it was renamed or made non-literal, update this test to read the "
        f"new form -- do NOT delete the check, or the two caps go back to "
        f"drifting silently (muxplex-i8w)."
    )
    line = _APP_JS[: match.start()].count("\n") + 1
    return int(match.group(1)), line


def _installed_kit_cap() -> tuple[int, str]:
    """Return (cap, where-it-came-from) for the INSTALLED tmux-kit.

    Prefers the exported ``SESSION_NAME_MAX_LEN`` constant (tmux-kit >= 0.5.0,
    where ``SESSION_NAME_RE`` is built from it). Falls back to reading the
    length bound straight out of ``SESSION_NAME_RE`` for older pins, which is
    the only cap those versions have. Either way the number is cross-checked
    against real behaviour by ``test_installed_tmux_kit_cap_is_real``.
    """
    exported = getattr(_kit_names, "SESSION_NAME_MAX_LEN", None)
    if isinstance(exported, int):
        return exported, "tmux_kit.names.SESSION_NAME_MAX_LEN"

    pattern = _kit_names.SESSION_NAME_RE.pattern
    match = re.search(r"\{0,(\d+)\}", pattern)
    assert match is not None, (
        f"installed tmux-kit exports no SESSION_NAME_MAX_LEN constant AND its "
        f"SESSION_NAME_RE has no readable `{{0,N}}` length bound "
        f"(pattern: {pattern!r}), so muxplex cannot tell what session-name "
        f"length the server it depends on will accept. Teach this helper the "
        f"new shape rather than dropping the check (muxplex-i8w)."
    )
    # The bound applies to the characters AFTER the mandatory leading one.
    return int(match.group(1)) + 1, f"SESSION_NAME_RE bound in {pattern!r}"


def _kit_location() -> str:
    return (
        f"installed tmux-kit {_installed_tmux_kit_version()}, pinned by "
        f"pyproject.toml's `tmux-kit==` entry"
    )


# ---------------------------------------------------------------------------
# The empirical anchor: whatever number we just read, prove tmux-kit really
# behaves that way. Without this, a wrong reading would compare two numbers
# neither of which is the cap anyone actually hits.
# ---------------------------------------------------------------------------


def test_installed_tmux_kit_cap_is_real():
    """The cap we read out of tmux-kit is the length it actually enforces."""
    cap, source = _installed_kit_cap()

    at_cap = "a" * cap
    over_cap = "a" * (cap + 1)

    assert _kit_names.is_valid_session_name(at_cap), (
        f"read a session-name cap of {cap} from {source}, but the installed "
        f"tmux-kit rejects a {cap}-character name. The number this file "
        f"compares against app.js is therefore not the cap tmux-kit enforces "
        f"-- fix the reader before trusting any other failure here."
    )
    assert not _kit_names.is_valid_session_name(over_cap), (
        f"read a session-name cap of {cap} from {source}, but the installed "
        f"tmux-kit ACCEPTS a {cap + 1}-character name -- so the real cap is "
        f"higher than {cap}. The number this file compares against app.js is "
        f"not the cap tmux-kit enforces; fix the reader."
    )


# ---------------------------------------------------------------------------
# The point of the file.
# ---------------------------------------------------------------------------


def test_app_js_cap_agrees_with_installed_tmux_kit():
    """app.js's cap and the installed tmux-kit's cap must be the same number.

    The one exception is the disagreement recorded at the top of this file,
    which is deliberate, understood, and self-retiring.
    """
    app_cap, app_line = _app_js_cap()
    kit_cap, kit_source = _installed_kit_cap()

    if app_cap == kit_cap:
        return

    both = (
        f"  app.js    SESSION_NAME_MAX_BYTES = {app_cap}\n"
        f"            {_APP_JS_REL}:{app_line}\n"
        f"  tmux-kit  session-name cap       = {kit_cap}\n"
        f"            {kit_source}\n"
        f"            ({_kit_location()})"
    )

    if (app_cap, kit_cap) == (_RECORDED_APP_JS_CAP, _RECORDED_KIT_CAP):
        return  # the recorded, pinned-open window -- see the module docstring

    raise AssertionError(
        "SESSION-NAME CAP DRIFT -- the frontend and the server no longer agree "
        "on how long a session name may be:\n\n"
        f"{both}\n\n"
        "A name between these two numbers is accepted by the new-session input "
        "and refused by the server, which is the exact bug muxplex-1vz / "
        "muxplex-i1r / muxplex-27o were opened to remove (they were 255, 64 "
        "and 32).\n\n"
        "Move BOTH numbers together, or -- if the disagreement is deliberate "
        "and temporary, as a tmux-kit pin bump waiting on a release is -- "
        "record the new pair in this file's _RECORDED_APP_JS_CAP / "
        "_RECORDED_KIT_CAP with the reason, so it is a decision someone made "
        "rather than a drift nobody noticed."
    )


def test_recorded_disagreement_window_is_still_open():
    """Fails once the recorded exception is obsolete, so it cannot linger.

    An exception that outlives its reason is worse than no check at all: it
    would let a later regression back into exactly this state pass silently.
    """
    app_cap, _ = _app_js_cap()
    kit_cap, kit_source = _installed_kit_cap()

    assert (app_cap, kit_cap) == (_RECORDED_APP_JS_CAP, _RECORDED_KIT_CAP), (
        f"the recorded session-name cap disagreement is no longer the current "
        f"state:\n\n"
        f"  recorded:  app.js {_RECORDED_APP_JS_CAP} vs tmux-kit "
        f"{_RECORDED_KIT_CAP}\n"
        f"  actual:    app.js {app_cap} ({_APP_JS_REL}) vs tmux-kit "
        f"{kit_cap} ({kit_source})\n\n"
        f"Recorded reason was: {_RECORDED_REASON}\n\n"
        f"If the two caps now AGREE, this is good news and the fix is to "
        f"delete _RECORDED_APP_JS_CAP, _RECORDED_KIT_CAP, _RECORDED_REASON, "
        f"the pinned-open branch in "
        f"test_app_js_cap_agrees_with_installed_tmux_kit, this test, and the "
        f"module docstring's 'ONE RECORDED DISAGREEMENT' section. "
        f"test_app_js_cap_agrees_with_installed_tmux_kit then enforces plain "
        f"equality with no exceptions, which is the end state this file wants."
    )


def test_main_py_400_detail_quotes_the_enforced_cap():
    """The 400 muxplex returns must name the length the server really enforces.

    ``_require_valid_session_name`` spells the cap out in prose ("1-64
    characters"), which is a third copy of the number with nothing holding it
    to the other two. When the tmux-kit pin moves, that sentence becomes a
    confident lie told to the user at the exact moment they need it to be
    true -- so it fails here instead (muxplex-i1r's named follow-up).
    """
    body_match = re.search(
        r"def _require_valid_session_name\(.*?\n(?=\n\n(?:def |# |@))",
        _MAIN_PY,
        re.DOTALL,
    )
    assert body_match is not None, (
        f"could not locate _require_valid_session_name in {_MAIN_PY_REL}; it "
        f"is the 400 that quotes the session-name cap back to the user."
    )
    body = body_match.group(0)

    stated = re.search(r"\(1-(\d+)\s+characters\)", body)
    if stated is None:
        # No hardcoded number left (e.g. it now interpolates the constant) --
        # exactly the end state this test is pushing toward.
        return

    kit_cap, kit_source = _installed_kit_cap()
    stated_cap = int(stated.group(1))
    line = _MAIN_PY[: body_match.start() + stated.start()].count("\n") + 1

    assert stated_cap == kit_cap, (
        f"SESSION-NAME CAP DRIFT -- the 400 detail states a cap the server "
        f"does not enforce:\n\n"
        f"  main.py   error detail says     = {stated_cap}\n"
        f"            {_MAIN_PY_REL}:{line}\n"
        f"  tmux-kit  session-name cap      = {kit_cap}\n"
        f"            {kit_source}\n"
        f"            ({_kit_location()})\n\n"
        f"A user refused at {kit_cap} characters would be told the limit is "
        f"{stated_cap}. Quote the enforced cap instead of restating it -- "
        f"tmux-kit exports SESSION_NAME_MAX_LEN for exactly this reason "
        f"(muxplex-i1r's follow-up)."
    )


def test_app_js_maxlength_stays_derived_from_the_byte_cap():
    """app.js must not grow a SECOND literal for the same cap.

    ``SESSION_NAME_MAX_LENGTH`` (what the input's ``maxlength`` is set to) is
    written as a derivation of ``SESSION_NAME_MAX_BYTES`` precisely so the two
    cannot drift. Re-literalising it would reintroduce the bug inside a single
    file -- which is how this started across three repos.
    """
    match = re.search(
        r"^const\s+SESSION_NAME_MAX_LENGTH\s*=\s*([^;]+);", _APP_JS, re.MULTILINE
    )
    assert match is not None, (
        f"could not find `const SESSION_NAME_MAX_LENGTH = ...;` in {_APP_JS_REL}"
    )
    rhs = match.group(1).strip()
    line = _APP_JS[: match.start()].count("\n") + 1
    assert "SESSION_NAME_MAX_BYTES" in rhs, (
        f"{_APP_JS_REL}:{line} sets SESSION_NAME_MAX_LENGTH to {rhs!r} instead "
        f"of deriving it from SESSION_NAME_MAX_BYTES. Two literals that merely "
        f"match today is exactly how amplifier-workspace, tmux-kit and app.js "
        f"ended up carrying 32, 64 and 255 (muxplex-i8w)."
    )
