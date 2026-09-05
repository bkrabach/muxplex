"""muxplex-y15 -- the displayed model, checked against what the provider
actually serves.

muxplex-nnl made Settings -> Agent name the provider and model the Agent
is using, read live from the in-process runner and pinned to the same
constant the turn path falls back to, so the panel cannot display a model
the turn would not send. That closed the INTRA-muxplex half of the
mismatch.

It left the other half wide open: nothing checked that the provider will
actually SERVE that model. A renamed, retired, or mistyped model id
displayed with total confidence and failed only at turn time, mid-stream,
after the response had already started. That is this batch's recurring
defect in its purest form -- the software knowing something the user does
not -- and a confidently-wrong model name is worse than a blank one,
because it invites the user to trust it.

THREE OUTCOMES, AND THE MIDDLE ONE IS THE HARD PART. Most of this file is
about it:

    validated    the provider serves this model.
    not_served   the provider answered, and it does not. Say which model
                 was asked for AND what is available.
    unknown      we could not check -- no credential, offline, refused
                 key, a provider that publishes no list, or a list in a
                 shape we cannot parse.

"unknown" must look like NEITHER a pass NOR a failure. Every one of those
paths is a place where the tempting move is to round to the nearest
confident verdict, and each rounding direction has its own harm: rounding
to "validated" reinstates the original bug; rounding to "not_served"
tells a user their working configuration is broken and invites them to
change something that was fine. The tests below pin every one of those
paths to "unknown" individually, because they are individually tempting.

WHY THIS IS NOT ON THE GATE PATH, tested rather than merely commented.
``full_status()`` backs both the Settings tab AND ``checkAgentGate()``,
which chat.js polls -- and that gate FAILS OPEN on error by design
(muxplex-at9). A live provider round-trip there would make the gate slow
and network-dependent, and a validation failure reaching it would be
silently invisible. So the check lives behind its own endpoint, and
``test_full_status_never_calls_the_provider`` fails the suite if anyone
ever inlines it.

WHAT IS NOT VERIFIED HERE, said plainly. amplifier-agent is an optional
extra and is NOT installed on the host this was written on, so no test
here has ever spoken to a real provider. What IS real: the enumeration
call itself is not new code -- ``validate_key()`` has always called
``instance.list_models()`` and this change routes both callers through
one shared ``_enumerate_models()`` rather than adding a second call site.
The seams around it are what these tests hold, and they hold them without
a key by standing in for the two functions that reach amplifier-agent
(``_resolve_api_key``, ``_enumerate_models``) -- the same shape
test_agent_active_target.py already uses to test ``full_status()``
without the extra installed.
"""

from __future__ import annotations

import pathlib

from fastapi.testclient import TestClient

from muxplex.agent_embedded import credentials as creds
from muxplex.agent_embedded import runner as agent_embedded_runner
from muxplex.auth import create_session_cookie
from muxplex.main import _auth_secret, _auth_ttl, app

_FRONTEND = pathlib.Path(__file__).parent.parent / "frontend"
_CHAT_JS = (_FRONTEND / "chat.js").read_text()
_INDEX_HTML = (_FRONTEND / "index.html").read_text()

_FAKE_KEY = "sk-test-not-a-real-credential"


def _authed_client() -> TestClient:
    """A TestClient with a valid session cookie (non-localhost address --
    matches test_agent_active_target.py's identical helper)."""
    cookie = create_session_cookie(_auth_secret, _auth_ttl)
    client = TestClient(app, base_url="http://192.168.1.1")
    client.cookies.set("muxplex_session", cookie)
    return client


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


def _stub_credential(monkeypatch, key: str = _FAKE_KEY) -> None:
    monkeypatch.setattr(creds, "_resolve_api_key", lambda provider: key)


def _stub_enumeration(monkeypatch, verdict, detail, models, calls=None):
    """Stand in for the one function that talks to a provider.

    THE seam of this change. Patching here (rather than at
    amplifier_agent_cli's internals, as test_agent_credential_embedded.py
    must) is what lets every outcome below be exercised on a host with no
    `agent` extra and no key -- i.e. on the host this was written on.
    """

    async def _fake(provider, api_key, *, timeout_seconds=15.0):
        if calls is not None:
            calls.append((provider, api_key))
        return verdict, detail, models

    monkeypatch.setattr(creds, "_enumerate_models", _fake)


class _FakeModel:
    """Matches the shape test_agent_credential_embedded.py's own double
    uses -- an object with `.id` -- so both files agree on what
    list_models() is believed to return."""

    def __init__(self, model_id: str) -> None:
        self.id = model_id


def setup_function() -> None:
    """A cached enumeration must never leak between tests -- a stale entry
    would make a later test pass without doing the work it claims."""
    creds.clear_served_models_cache()


# ---------------------------------------------------------------------------
# _model_ids: an unreadable shape is an UNKNOWN, never an empty list.
# ---------------------------------------------------------------------------


def test_model_ids_reads_bare_strings():
    assert creds._model_ids(["a", "b"]) == ["a", "b"]


def test_model_ids_reads_objects_with_an_id_attribute():
    assert creds._model_ids([_FakeModel("claude-sonnet-5")]) == ["claude-sonnet-5"]


def test_model_ids_reads_mappings():
    assert creds._model_ids([{"id": "gpt-5"}, {"name": "gpt-4o"}]) == [
        "gpt-5",
        "gpt-4o",
    ]


def test_model_ids_returns_none_for_a_shape_it_cannot_read():
    """THE distinction this function exists for.

    muxplex does not own list_models()'s return type -- it lives in
    amplifier-agent's provider classes and can change upstream. If it
    ever returns something unparseable, the honest answer is "could not
    check". Returning [] instead would flow downstream as "the provider
    serves nothing", which renders as an accusation that the user's model
    is wrong -- a confident lie sourced from our own parsing failure.
    """
    assert creds._model_ids([object()]) is None
    assert creds._model_ids([{"unexpected": "shape"}]) is None
    assert creds._model_ids([12345]) is None


def test_model_ids_rejects_the_whole_list_if_any_entry_is_unreadable():
    """A partial list is worse than no list: the one entry we could not
    parse might be the very model being checked, which would report a
    served model as absent."""
    assert creds._model_ids([_FakeModel("claude-sonnet-5"), object()]) is None


def test_model_ids_passes_an_empty_list_through_as_empty():
    """Empty is a real answer ("the provider named nothing"), distinct
    from None ("we never got an answer"). served_model_check treats it as
    unknown, but that decision belongs there, not here."""
    assert creds._model_ids([]) == []


# ---------------------------------------------------------------------------
# _match_served: leans away from crying wolf, on purpose.
# ---------------------------------------------------------------------------


def test_match_served_exact():
    assert creds._match_served("claude-sonnet-5", ["a", "claude-sonnet-5"]) == (
        "claude-sonnet-5"
    )


def test_match_served_accepts_a_dated_variant_and_names_it():
    """Providers publish dated ids (claude-sonnet-5-20260101) for the same
    model a caller names undated. Reporting that as "not served" would be
    a false alarm on a working configuration -- so it matches, and the
    caller surfaces the exact served id rather than hiding the
    difference."""
    assert (
        creds._match_served("claude-sonnet-5", ["claude-sonnet-5-20260101"])
        == "claude-sonnet-5-20260101"
    )
    assert creds._match_served("claude-sonnet-5-20260101", ["claude-sonnet-5"]) == (
        "claude-sonnet-5"
    )


def test_match_served_does_not_match_a_bare_prefix_without_a_boundary():
    """ "claude-sonnet-5" must not be satisfied by "claude-sonnet-50" -- the
    family match is dash-delimited, not a substring."""
    assert creds._match_served("claude-sonnet-5", ["claude-sonnet-50"]) is None


def test_match_served_returns_none_when_genuinely_absent():
    assert creds._match_served("gpt-4o", ["claude-sonnet-5", "claude-opus-5"]) is None


# ---------------------------------------------------------------------------
# The three outcomes.
# ---------------------------------------------------------------------------


async def test_validated_when_the_provider_serves_the_model(monkeypatch):
    _assume_library_available(monkeypatch)
    _stub_credential(monkeypatch)
    _stub_enumeration(
        monkeypatch, "ok", "2 model(s) returned", [_FakeModel("claude-sonnet-5")]
    )

    result = await creds.served_model_check()

    assert result["status"] == "validated"
    assert result["model"] == agent_embedded_runner.default_model()
    assert result["provider"] == agent_embedded_runner.active_provider()
    assert result["served"] == ["claude-sonnet-5"]


async def test_not_served_names_the_model_asked_for_and_what_is_available(monkeypatch):
    """The item's second acceptance criterion, verbatim: the mismatch is
    named specifically -- which model was asked for, which are available
    -- rather than surfacing later as a generic failed turn."""
    _assume_library_available(monkeypatch)
    _stub_credential(monkeypatch)
    _stub_enumeration(
        monkeypatch,
        "ok",
        "2 model(s) returned",
        [_FakeModel("claude-opus-5"), _FakeModel("claude-haiku-4-5")],
    )

    result = await creds.served_model_check()

    assert result["status"] == "not_served"
    assert result["model"] == agent_embedded_runner.default_model()
    # Which model was asked for...
    assert agent_embedded_runner.default_model() in result["detail"]
    # ...and which are available. Both halves, in the sentence a user reads.
    assert "claude-opus-5" in result["detail"]
    assert "claude-haiku-4-5" in result["detail"]
    assert result["served"] == ["claude-opus-5", "claude-haiku-4-5"]


async def test_unknown_when_the_agent_library_is_not_installed(monkeypatch):
    """No importable runner means no provider to ask and no model whose
    servability could even be in question -- the same reason full_status()
    reports a null active provider/model in this state. The state every
    fresh install is in must not read as a failure."""
    _assume_library_missing(monkeypatch)

    result = await creds.served_model_check()

    assert result["status"] == "unknown"
    assert result["reason"] == "library_missing"
    assert result["provider"] is None
    assert result["model"] is None
    assert result["served"] is None


async def test_unknown_when_there_is_no_credential(monkeypatch):
    """A box with the library but no key yet still knows WHICH model it
    would use -- it just cannot check it. Both facts are reported, and the
    verdict is unknown, not failure."""
    _assume_library_available(monkeypatch)
    _stub_credential(monkeypatch, key="")

    result = await creds.served_model_check()

    assert result["status"] == "unknown"
    assert result["reason"] == "no_credential"
    assert result["model"] == agent_embedded_runner.default_model()
    assert result["served"] is None
    assert "not a sign the model is wrong" in result["detail"]


async def test_unknown_when_the_provider_is_unreachable(monkeypatch):
    """A timeout is not evidence about the model. This is the acceptance
    criterion's third case at the source: a lookup failure is reported as
    "could not check", never as a mismatch."""
    _assume_library_available(monkeypatch)
    _stub_credential(monkeypatch)
    _stub_enumeration(monkeypatch, "unreachable", "timed out after 15s", None)

    result = await creds.served_model_check()

    assert result["status"] == "unknown"
    assert result["reason"] == "unreachable"
    assert result["served"] is None
    assert "not a sign the model is wrong" in result["detail"]


async def test_unknown_when_the_key_is_refused(monkeypatch):
    """A refused key means we could not check -- it does not mean the
    model is absent. Credential state is already reported separately by
    full_status(); this line must not restate it as a model problem."""
    _assume_library_available(monkeypatch)
    _stub_credential(monkeypatch)
    _stub_enumeration(monkeypatch, "bad_key", "AuthenticationError: 401", None)

    result = await creds.served_model_check()

    assert result["status"] == "unknown"
    assert result["reason"] == "bad_key"


async def test_unknown_when_the_provider_publishes_no_model_list(monkeypatch):
    """validate_key's own docstring already records that some providers
    (azure-openai) return an empty list BY DESIGN with a perfectly good
    key. That is "nothing to check against", not "your model is absent" --
    the single most tempting place in this file to round to not_served."""
    _assume_library_available(monkeypatch)
    _stub_credential(monkeypatch)
    _stub_enumeration(monkeypatch, "ok", "0 models returned", [])

    result = await creds.served_model_check()

    assert result["status"] == "unknown"
    assert result["reason"] == "no_enumeration"


async def test_unknown_when_the_model_list_shape_cannot_be_read(monkeypatch):
    _assume_library_available(monkeypatch)
    _stub_credential(monkeypatch)
    _stub_enumeration(monkeypatch, "ok", "1 model(s) returned", [object()])

    result = await creds.served_model_check()

    assert result["status"] == "unknown"
    assert result["reason"] == "unreadable_model_list"
    assert result["served"] is None


async def test_a_dated_variant_validates_and_says_which_id_it_matched(monkeypatch):
    _assume_library_available(monkeypatch)
    _stub_credential(monkeypatch)
    dated = agent_embedded_runner.default_model() + "-20260101"
    _stub_enumeration(monkeypatch, "ok", "1 model(s) returned", [_FakeModel(dated)])

    result = await creds.served_model_check()

    assert result["status"] == "validated"
    # The difference is surfaced, not hidden: a user comparing this line
    # against the one above it should see why they differ.
    assert dated in result["detail"]


# ---------------------------------------------------------------------------
# Caching: only successes, and never across a credential change.
# ---------------------------------------------------------------------------


async def test_a_second_check_reuses_the_cached_list(monkeypatch):
    """A served-model list changes on the order of months. A live API call
    on every settings-tab open is disproportionate to that -- the item
    asked for a caching policy by name."""
    _assume_library_available(monkeypatch)
    _stub_credential(monkeypatch)
    calls: list[tuple[str, str]] = []
    _stub_enumeration(
        monkeypatch, "ok", "1 model(s) returned", [_FakeModel("claude-sonnet-5")], calls
    )

    await creds.served_model_check()
    await creds.served_model_check()

    assert len(calls) == 1


async def test_a_changed_credential_invalidates_the_cache(monkeypatch):
    """Keyed on a fingerprint of the RESOLVED credential, so unsetting an
    env var or saving a new key through the panel invalidates by
    construction -- not by someone remembering to call an invalidator."""
    _assume_library_available(monkeypatch)
    _stub_credential(monkeypatch, key="sk-first")
    calls: list[tuple[str, str]] = []
    _stub_enumeration(
        monkeypatch, "ok", "1 model(s) returned", [_FakeModel("claude-sonnet-5")], calls
    )

    await creds.served_model_check()
    _stub_credential(monkeypatch, key="sk-second")
    await creds.served_model_check()

    assert len(calls) == 2


async def test_a_failed_lookup_is_never_cached(monkeypatch):
    """A transient failure that stuck for the TTL would keep reporting
    "could not check" long after the provider came back -- turning a blip
    into five minutes of wrong."""
    _assume_library_available(monkeypatch)
    _stub_credential(monkeypatch)
    calls: list[tuple[str, str]] = []
    _stub_enumeration(monkeypatch, "unreachable", "timed out", None, calls)

    await creds.served_model_check()
    await creds.served_model_check()

    assert len(calls) == 2


async def test_the_cache_never_holds_the_key_itself(monkeypatch):
    _assume_library_available(monkeypatch)
    _stub_credential(monkeypatch)
    _stub_enumeration(
        monkeypatch, "ok", "1 model(s) returned", [_FakeModel("claude-sonnet-5")]
    )

    await creds.served_model_check()

    assert _FAKE_KEY not in repr(creds._served_models_cache)


# ---------------------------------------------------------------------------
# The gate path stays local. This is the structural half of the fix.
# ---------------------------------------------------------------------------


async def test_full_status_never_calls_the_provider(monkeypatch):
    """full_status() backs checkAgentGate(), which chat.js polls. A live
    provider round-trip here would make the gate slow and
    network-dependent, and -- because the gate fails OPEN by design
    (muxplex-at9) -- a provider blip would silently influence whether the
    panel is usable. Inlining the served-model check into full_status is
    the obvious-looking refactor; this fails the suite if anyone does it.
    """
    _assume_library_available(monkeypatch)
    monkeypatch.setattr(
        creds,
        "resolve_status",
        lambda provider: {"source": "file", "masked": "sk-abc...wxyz", "env_var": None},
    )

    async def _explode(*args, **kwargs):
        raise AssertionError(
            "full_status() reached the provider -- the credential-status "
            "route backs checkAgentGate() and must stay purely local"
        )

    monkeypatch.setattr(creds, "_enumerate_models", _explode)

    status = await creds.full_status()

    assert status["state"] == "configured"


async def test_full_status_no_longer_carries_a_permanently_empty_models_field(
    monkeypatch,
):
    """The vestigial sidecar-shape field is GONE, deliberately. It was []
    on every embedded response with zero frontend readers, and now that a
    real served list exists behind its own endpoint, keeping an
    always-empty `models` here would leave two answers to one question --
    with the permanently-empty one being the one a reader finds first."""
    _assume_library_available(monkeypatch)
    monkeypatch.setattr(
        creds,
        "resolve_status",
        lambda provider: {"source": "file", "masked": "sk-abc...wxyz", "env_var": None},
    )

    assert "models" not in await creds.full_status()

    _assume_library_missing(monkeypatch)
    assert "models" not in await creds.full_status()


# ---------------------------------------------------------------------------
# The endpoint.
# ---------------------------------------------------------------------------


def test_endpoint_reports_a_mismatch_and_leaks_no_key(monkeypatch):
    _assume_library_available(monkeypatch)
    _stub_credential(monkeypatch)
    _stub_enumeration(
        monkeypatch, "ok", "1 model(s) returned", [_FakeModel("some-other-model")]
    )

    resp = _authed_client().get("/api/agent/served-models")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "not_served"
    assert "some-other-model" in body["detail"]
    assert _FAKE_KEY not in resp.text


def test_endpoint_answers_200_even_when_it_could_not_check(monkeypatch):
    """An HTTP error here would be indistinguishable, to the caller, from
    the fetch itself failing -- and chat.js renders both as "could not
    check" anyway. So the interesting distinction (could not check vs.
    genuinely not served) would be exactly the one thrown away by using
    the status code to carry it."""
    _assume_library_available(monkeypatch)
    _stub_credential(monkeypatch)
    _stub_enumeration(monkeypatch, "unreachable", "timed out", None)

    resp = _authed_client().get("/api/agent/served-models")

    assert resp.status_code == 200
    assert resp.json()["status"] == "unknown"


def test_endpoint_is_no_more_exposed_than_the_status_route_it_sits_beside():
    """Pins the new route's auth posture RELATIVELY, not absolutely.

    An absolute assertion ("unauthenticated requests get a 401") would be
    asserting the test environment's auth configuration rather than
    anything about this route -- and it passes or fails for reasons that
    have nothing to do with this change. What must hold is that adding a
    route did not create a hole: an unauthenticated caller gets exactly
    the same treatment here as on the credential-status route that has
    always sat behind the shared /api/ middleware.
    """
    # Registered as a REAL route first. Without this the comparison below
    # passes vacuously on a tree where the route does not exist at all --
    # muxplex serves its SPA from a catch-all, so an unrouted /api/ path
    # answers 200 just like a routed one.
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/agent/served-models" in paths

    client = TestClient(app, base_url="http://192.168.1.1")
    existing = client.get("/api/agent/provider-credential")
    added = client.get("/api/agent/served-models")
    assert added.status_code == existing.status_code


# ---------------------------------------------------------------------------
# validate_key's contract survives the refactor.
# ---------------------------------------------------------------------------


async def test_validate_key_still_reports_its_original_verdicts(monkeypatch):
    """validate_key and served_model_check now share one enumeration call.
    validate_key's (verdict, detail) contract is depended on by
    main.py's POST handler and by test_agent_credential_embedded.py, so
    the refactor must be invisible to it."""
    _stub_enumeration(monkeypatch, "bad_key", "AuthenticationError: 401", None)
    assert await creds.validate_key("anthropic", "sk-x") == (
        "bad_key",
        "AuthenticationError: 401",
    )

    _stub_enumeration(monkeypatch, "ok", "3 model(s) returned", [1, 2, 3])
    assert await creds.validate_key("anthropic", "sk-x") == (
        "ok",
        "3 model(s) returned",
    )


async def test_validate_key_does_not_populate_the_served_model_cache(monkeypatch):
    """A candidate key being validated is not necessarily the credential a
    turn will use -- env still wins over a stored key. Caching ITS model
    list against the resolved credential would attribute one key's answer
    to another."""
    _stub_enumeration(
        monkeypatch, "ok", "1 model(s) returned", [_FakeModel("claude-sonnet-5")]
    )

    await creds.validate_key("anthropic", "sk-candidate")

    assert creds._served_models_cache == {}


# ---------------------------------------------------------------------------
# The cross-file half: the panel actually reads this, and the gate does not.
# ---------------------------------------------------------------------------


def test_the_panel_has_somewhere_to_render_the_answer():
    assert 'id="agent-model-check"' in _INDEX_HTML
    assert "agent-model-check" in _CHAT_JS


def test_the_gate_does_not_fetch_the_served_model_endpoint():
    """A source assertion because it is a structural claim, not a
    behavioural one: checkAgentGate() must reach exactly one endpoint. The
    node suite drives the runtime version of this; this one fails fast in
    the Python suite if the two functions are ever merged.
    """
    gate_start = _CHAT_JS.index("async function checkAgentGate()")
    gate_end = _CHAT_JS.index("function init()", gate_start)
    gate_source = _CHAT_JS[gate_start:gate_end]
    assert "/api/agent/provider-credential" in gate_source
    assert "/api/agent/served-models" not in gate_source
