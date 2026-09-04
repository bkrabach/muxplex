"""muxplex-at9 -- an un-onboarded server must READ as un-onboarded.

A muxplex install where the agent has never been set up is the state
EVERY user starts in. It is not a fault, and it is not transient: no
amount of retrying can make an uninstalled amplifier-agent appear. The
panel must therefore say "the Agent isn't set up here, do X", never
"muxplex hit an error of its own, worth retrying once".

v0.48.1 (the original muxplex-at9 fix) got that right by having chat.js
regex the server's 503 prose for the phrase "not configured on this
server". That classification was PROSE-COUPLED, and the later
sidecar -> embedded refactor rewrote the prose
(``agent_embedded/runner.py``) without touching chat.js. Nothing failed;
the branch simply stopped matching, and every un-onboarded server went
back to being told to retry. This file pins the seam so that cannot
recur silently:

  1. The 503 carries a STABLE, machine-readable discriminator
     (``error.type == "agent_not_configured"``) that does not change when
     someone rewords a sentence.
  2. chat.js branches on that literal, so a rename on either side fails
     this suite instead of silently degrading a user-facing message.
  3. The prose a user is actually shown names a remedy that EXISTS.

(3) is the same defect class as the original report's `sudo: unknown
user aa-svc` leak: internals rendered at a user. Today's message advises
`pip install amplifier-agent` (amplifier-agent is deliberately NOT on
PyPI -- see pyproject.toml's ``[tool.uv.sources]``: "source-only ...
there is no registry copy to fall back to") and `MUXPLEX_AGENT_MODE=
sidecar` (the sidecar path was removed; ``is_embedded_mode()`` has zero
callers in main.py). Both instructions are impossible to carry out, and
``full_status()`` renders this exact string as the PRIMARY line in
Settings -> Agent.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from muxplex.agent_embedded import runner as agent_embedded_runner
from muxplex.auth import create_session_cookie
from muxplex.main import (
    AGENT_NOT_CONFIGURED_ERROR_TYPE,
    _auth_secret,
    _auth_ttl,
    app,
)

_CHAT_JS = (
    pathlib.Path(__file__).parent.parent / "frontend" / "chat.js"
).read_text()


def _authed_client() -> TestClient:
    """A TestClient with a valid session cookie (non-localhost address --
    matches test_agent_credential_embedded.py's identical helper)."""
    cookie = create_session_cookie(_auth_secret, _auth_ttl)
    client = TestClient(app, base_url="http://192.168.1.1")
    client.cookies.set("muxplex_session", cookie)
    return client


@pytest.fixture
def _unavailable(monkeypatch):
    """Pin the embedded path to "not usable on this server" -- the state
    every fresh install is in, and the one the DTU (where amplifier-agent
    IS installed) never exercises."""

    async def _reason():
        return "amplifier-agent is not installed in this Python environment"

    monkeypatch.setattr(agent_embedded_runner, "check_available", _reason)


# ---------------------------------------------------------------------------
# 1. The 503 is classifiable without reading its prose
# ---------------------------------------------------------------------------


def test_chat_completions_503_is_typed_not_configured(_unavailable):
    """The un-onboarded 503 must be distinguishable from a real server
    fault by a field, not by a phrase.

    Every reason `check_available()` can return means "the agent isn't
    set up here yet" -- missing library or missing credential. Neither is
    transient, so this branch has no "worth retrying" case to confuse it
    with, and one discriminator is sufficient.
    """
    resp = _authed_client().post(
        "/api/agent/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]}
    )
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["type"] == AGENT_NOT_CONFIGURED_ERROR_TYPE, (
        "an un-onboarded server must be typed as such; 'server_error' makes it "
        "indistinguishable from a genuine fault and sends the user back to retry"
    )


def test_chat_js_branches_on_the_same_literal_the_server_sends():
    """The drift guard the v0.48.1 fix lacked.

    chat.js must key off the SAME literal main.py emits. If either side
    renames the discriminator, this fails loudly here rather than
    degrading a message nobody re-reads.
    """
    assert AGENT_NOT_CONFIGURED_ERROR_TYPE in _CHAT_JS, (
        f"chat.js must classify the 503 on {AGENT_NOT_CONFIGURED_ERROR_TYPE!r}; "
        "prose-matching is what silently regressed at the sidecar -> embedded refactor"
    )


def test_chat_completions_503_body_is_json_not_sse(_unavailable):
    """Unchanged contract, pinned so the typing change above cannot
    accidentally turn the refusal into a stream."""
    resp = _authed_client().post(
        "/api/agent/chat/completions", json={"messages": []}
    )
    assert resp.headers["content-type"].startswith("application/json")
    json.loads(resp.content)  # parses -- not an SSE frame


# ---------------------------------------------------------------------------
# 2. The prose names a remedy that exists
# ---------------------------------------------------------------------------
#
# Asserted against the message CONSTANT, not against a live
# `library_unavailable_reason()` call: CI installs the `agent` extra, so a
# runtime assertion would skip in exactly the environment that is supposed
# to be guarding this. The constant is readable everywhere.


def test_library_missing_message_does_not_advise_the_impossible():
    """`pip install amplifier-agent` cannot work (not on PyPI) and
    `MUXPLEX_AGENT_MODE=sidecar` cannot work (the sidecar path is gone).

    This string is rendered VERBATIM as the primary line in
    Settings -> Agent (chat.js `_renderAgentCredentialStatus`'s
    `not_installed` branch reads `data.message`), so it is user-facing
    text, not a log line.
    """
    message = agent_embedded_runner.LIBRARY_MISSING_MESSAGE
    assert "pip install" not in message, (
        "amplifier-agent is source-only (pyproject.toml [tool.uv.sources]); "
        "pip cannot install it"
    )
    assert "MUXPLEX_AGENT_MODE" not in message, (
        "the sidecar mode was removed -- is_embedded_mode() has no callers in main.py, "
        "so this env var changes nothing"
    )


def test_library_missing_message_names_the_real_remedy():
    """`muxplex ensure-agent` is the actual, registered subcommand that
    installs amplifier-agent (cli.py's `ensure_agent()`, wired at
    cli.py's `sub.add_parser("ensure-agent", ...)`). Naming it is what
    turns a dead end into onboarding."""
    assert "ensure-agent" in agent_embedded_runner.LIBRARY_MISSING_MESSAGE, (
        "the message must point at the command that actually fixes this"
    )


async def test_a_genuinely_missing_library_reports_exactly_that_message(monkeypatch):
    """Pin the constant to the real code path, so the two assertions above
    cannot pass against a constant nothing actually uses.

    Simulates the absence structurally rather than by stubbing
    `_get_prepared` itself (which would assert nothing): `None` in
    `sys.modules` makes a real `import amplifier_agent_lib` raise
    ImportError, so `_get_prepared`'s OWN try/except is what runs. This
    works identically whether or not the extra is installed -- CI (which
    installs it) exercises the same branch a bare install hits.
    """
    import sys

    monkeypatch.setattr(agent_embedded_runner, "_prepared", None)
    monkeypatch.setitem(sys.modules, "amplifier_agent_lib", None)
    monkeypatch.setitem(sys.modules, "amplifier_agent_lib._runtime", None)

    reason = await agent_embedded_runner.library_unavailable_reason()
    assert reason == agent_embedded_runner.LIBRARY_MISSING_MESSAGE
