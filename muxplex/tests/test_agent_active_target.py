"""muxplex-nnl -- the panel must be able to say WHAT it is talking to.

Before this, nothing in muxplex's UI named the provider or the model. A
user on an Anthropic deployment and a user on some other one saw exactly
the same screen, and neither could tell which model a turn would run
against. "Knowing what you are talking to is the minimum" (muxplex-757,
which this was split out of).

WHAT THE FILED ITEM ASSUMED, AND WHY IT NO LONGER HOLDS. The item was
written against the SIDECAR architecture and argued the display was
blocked on new plumbing: the agent ran as a separate process bound to
127.0.0.1, muxplex proxied exactly one of its routes, the bearer lived
only on muxplex's side, so "the browser cannot ask the sidecar directly"
and muxplex would have to add a read path proxying `GET /v1/models`.

That premise is now stale in the specific ways that matter here:

  * There is no sidecar process to ask. The agent runs IN-PROCESS
    (``muxplex.agent_embedded.runner``), so the provider/model are plain
    local facts, not something behind an HTTP hop and a bearer.
  * There is no `/v1/models` anywhere in this repo to proxy -- grep is
    empty. The served-model list the item wanted to enumerate was a
    property of the sidecar's HTTP surface, which no longer exists.
  * The read path already exists: ``GET /api/agent/provider-credential``
    is read-only, sits behind the same shared auth middleware as every
    other ``/api/`` route, and returns no key and no bearer. That is the
    item's third acceptance criterion, already met by a route that
    shipped for a different reason.

So the real gap was never plumbing. It was that ``full_status()`` did not
report the two facts the panel needed, and the panel did not display
them. That is what this file pins.

THE HONESTY REQUIREMENT, which is the actual substance here. A model name
shown confidently and wrongly is worse than no model name at all -- it
converts "I don't know what I'm talking to" into "I was told, and told
wrong", which is strictly harder for a user to recover from. Two
consequences, both tested below:

  1. When there is no runner (amplifier-agent isn't installed on this
     box -- the state every fresh install is in), ``active`` reports
     ``None`` for both fields, and the panel renders "unknown". It does
     NOT fall back to the plausible-looking "anthropic / claude-sonnet-5".
  2. The model the server reports and the model chat.js actually SENDS
     are pinned equal across the language boundary. They are two separate
     literals in two files; nothing but a test can keep them together,
     and if they drift the panel displays a model no turn will use.

(2) is the same cross-language seam muxplex-at9 established one commit
earlier with ``AGENT_NOT_CONFIGURED_ERROR_TYPE``, and it is that item's
lesson applied before the fact rather than after: a coupling that is real
but unpinned does not fail when it breaks -- it just quietly starts
lying.
"""

from __future__ import annotations

import pathlib
import re

from fastapi.testclient import TestClient

from muxplex.agent_embedded import credentials as agent_embedded_credentials
from muxplex.agent_embedded import runner as agent_embedded_runner
from muxplex.auth import create_session_cookie
from muxplex.main import _auth_secret, _auth_ttl, app

_FRONTEND = pathlib.Path(__file__).parent.parent / "frontend"
_CHAT_JS = (_FRONTEND / "chat.js").read_text()
_INDEX_HTML = (_FRONTEND / "index.html").read_text()
_RUNNER_PY = (
    pathlib.Path(__file__).parent.parent / "agent_embedded" / "runner.py"
).read_text()


def _authed_client() -> TestClient:
    """A TestClient with a valid session cookie (non-localhost address --
    matches test_agent_credential_embedded.py's identical helper)."""
    cookie = create_session_cookie(_auth_secret, _auth_ttl)
    client = TestClient(app, base_url="http://192.168.1.1")
    client.cookies.set("muxplex_session", cookie)
    return client


def _stub_resolution(monkeypatch, source: str = "file") -> None:
    """Pin ``resolve_status`` so these tests exercise the ``active`` block
    without needing the optional ``agent`` extra installed.

    Credential RESOLUTION is already covered end-to-end in
    test_agent_credential_embedded.py (behind ``needs_amplifier_agent_cli``);
    duplicating that here would only mean these assertions skip on the
    same environments, and the active-provider/model contract holds
    regardless of whether a key happens to resolve.
    """
    monkeypatch.setattr(
        agent_embedded_credentials,
        "resolve_status",
        lambda provider: {"source": source, "masked": "sk-abc...wxyz", "env_var": None},
    )


def _assume_library_available(monkeypatch) -> None:
    async def _available() -> None:
        return None

    monkeypatch.setattr(agent_embedded_runner, "library_unavailable_reason", _available)


def _assume_library_missing(monkeypatch) -> None:
    async def _unavailable() -> str:
        return agent_embedded_runner.LIBRARY_MISSING_MESSAGE

    monkeypatch.setattr(
        agent_embedded_runner, "library_unavailable_reason", _unavailable
    )


# ---------------------------------------------------------------------------
# The runner exposes what it actually uses (not a second copy of it).
# ---------------------------------------------------------------------------


def test_default_model_is_the_runners_own_constant():
    assert agent_embedded_runner.default_model() == (
        agent_embedded_runner._DEFAULT_MODEL_ID
    )
    assert agent_embedded_runner.default_model()  # non-empty


def test_the_turn_path_falls_back_to_the_very_model_we_advertise():
    """The exposed default must be the one a turn ACTUALLY falls back to.

    A source assertion rather than a behavioral one on purpose: driving
    ``stream_embedded_chat_completion`` far enough to observe the fallback
    requires a real prepared bundle and a real credential, so the
    behavioral version of this test could only ever run on a fully
    provisioned box -- i.e. it would skip in exactly the environment where
    someone is most likely to re-inline the literal. What must not recur
    is a SECOND copy of the model id: before muxplex-nnl the fallback was
    a bare ``"claude-sonnet-5"`` inline, and publishing an `active.model`
    sourced from anywhere else would have made the panel's claim
    structurally unverifiable.
    """
    assert 'body.get("model") or _DEFAULT_MODEL_ID' in _RUNNER_PY
    occurrences = _RUNNER_PY.count(f'"{agent_embedded_runner._DEFAULT_MODEL_ID}"')
    assert occurrences == 1, (
        f"the model id literal appears {occurrences}x in runner.py -- it must "
        "exist exactly once (the _DEFAULT_MODEL_ID definition), so there is "
        "one place to change and nothing to drift against"
    )


def test_active_provider_and_default_model_are_distinct_facts():
    """Guards a plausible-looking refactor that collapses the two
    accessors: the provider is fixed by the runner, the model is a
    request-overridable default. They answer different questions and must
    not become aliases of one value."""
    assert agent_embedded_runner.active_provider() == "anthropic"
    assert (
        agent_embedded_runner.default_model() != agent_embedded_runner.active_provider()
    )


# ---------------------------------------------------------------------------
# full_status() reports the active target -- and reports UNKNOWN honestly.
# ---------------------------------------------------------------------------


async def test_full_status_reports_the_runners_active_provider_and_model(monkeypatch):
    _assume_library_available(monkeypatch)
    _stub_resolution(monkeypatch)

    status = await agent_embedded_credentials.full_status()

    assert status["active"] == {
        "provider": agent_embedded_runner.active_provider(),
        "model": agent_embedded_runner.default_model(),
    }


async def test_active_target_is_reported_even_with_no_credential(monkeypatch):
    """ "Which provider/model would this server use" and "is there a key"
    are separate questions. A box with the library installed but no key
    yet can still answer the first one truthfully, and a user setting the
    key up is precisely who wants to know what they are about to talk
    to."""
    _assume_library_available(monkeypatch)
    _stub_resolution(monkeypatch, source="not_set")

    status = await agent_embedded_credentials.full_status()

    assert status["state"] == "not_configured"
    assert status["active"]["provider"] == agent_embedded_runner.active_provider()
    assert status["active"]["model"] == agent_embedded_runner.default_model()


async def test_active_target_is_unknown_when_the_agent_is_not_installed(monkeypatch):
    """THE honesty case. No importable library means no runner, which
    means there is no active provider or model to report -- so both are
    ``None`` (rendered "unknown"), never the plausible default pair.

    This is the state every fresh muxplex install is in, so it is the
    state most likely to be seen and least likely to be tested."""
    _assume_library_missing(monkeypatch)

    status = await agent_embedded_credentials.full_status()

    assert status["state"] == "not_installed"
    assert status["active"] == {"provider": None, "model": None}
    # Specifically NOT the values a reader might expect to see defaulted in.
    assert status["active"]["provider"] != agent_embedded_runner.active_provider()
    assert status["active"]["model"] != agent_embedded_runner.default_model()


# ---------------------------------------------------------------------------
# The read path itself: already-existing route, already-correct posture.
# ---------------------------------------------------------------------------


def test_the_endpoint_serves_the_active_block(monkeypatch):
    _assume_library_available(monkeypatch)
    _stub_resolution(monkeypatch)

    resp = _authed_client().get("/api/agent/provider-credential")

    assert resp.status_code == 200
    body = resp.json()
    assert body["active"]["provider"] == agent_embedded_runner.active_provider()
    assert body["active"]["model"] == agent_embedded_runner.default_model()


def test_the_read_path_requires_auth_like_every_other_api_route():
    """muxplex-nnl's third acceptance criterion. Not a new property --
    this route has always been behind the shared auth middleware -- but
    the item asked for it to be true of whatever path exposes the
    provider/model, so it is asserted where that claim is made.

    ``Accept: application/json`` is the panel's own header (chat.js's
    ``_fetchAgentCredentialStatus``) and it is load-bearing here: the
    middleware answers an unauthenticated BROWSER navigation with a 307 to
    /login, which TestClient follows to a 200 login page. Asserting
    without the header would therefore have read as "this route is open"
    when it is not -- an inverted result, not a flaky one.
    """
    resp = TestClient(app, base_url="http://192.168.1.1").get(
        "/api/agent/provider-credential", headers={"Accept": "application/json"}
    )
    assert resp.status_code == 401


def test_the_read_path_never_returns_a_key_or_a_bearer(monkeypatch):
    """Same criterion's second half: read-only, and the browser learns
    what is set WITHOUT being handed the credential itself."""
    _assume_library_available(monkeypatch)
    _stub_resolution(monkeypatch)

    body = _authed_client().get("/api/agent/provider-credential").json()

    serialized = repr(body)
    assert "sk-abc...wxyz" in serialized  # the masked form is fine
    assert "api_key" not in serialized
    assert "bearer" not in serialized.lower()


# ---------------------------------------------------------------------------
# Cross-language seam: the displayed model must be the model SENT.
# ---------------------------------------------------------------------------


def _chat_js_model_literal() -> str:
    match = re.search(r'var\s+MODEL\s*=\s*"([^"]+)"', _CHAT_JS)
    assert match, 'chat.js no longer declares `var MODEL = "..."`'
    return match.group(1)


def test_chat_js_sends_the_model_the_server_advertises():
    """The panel DISPLAYS the server's `active.model` but SENDS its own
    `MODEL` on every turn (runner.py uses `body["model"]` verbatim as the
    provider's model_override). If these two literals drift, Settings ->
    Agent confidently names a model that no turn will ever run against --
    the exact wrong-with-confidence failure muxplex-nnl exists to remove.

    Nothing but this assertion holds them together: they live in two
    files, in two languages, and a change to either one is individually
    reasonable and individually silent.
    """
    assert _chat_js_model_literal() == agent_embedded_runner.default_model()


def test_the_panel_reads_the_active_block_rather_than_assuming():
    assert "data.active" in _CHAT_JS or "active.provider" in _CHAT_JS
    assert "_renderActiveAgentTarget" in _CHAT_JS


def test_the_display_element_exists_in_the_settings_markup():
    """chat.js writes into `#agent-active-target`; if index.html stops
    shipping that element the render silently no-ops (the function returns
    early by design, for older frontend builds). Pin the pair."""
    assert 'id="agent-active-target"' in _INDEX_HTML
    assert 'getElementById("agent-active-target")' in _CHAT_JS
