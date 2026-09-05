# pyright: reportMissingImports=false
# amplifier-agent (amplifier_agent_cli) is an OPTIONAL dependency -- see
# runner.py's module-level note for why every import of it below is
# deliberately lazy (inside a function, inside a try/except ImportError).
"""Embedded-mode credential status, validation, and persistence.

Alongside ``runner.py``'s turn execution, this module owns the Settings ->
Agent credential lifecycle for ``MUXPLEX_AGENT_MODE=embedded`` (the
default): resolving per-provider status for
``GET /api/agent/provider-credential``, validating a candidate key with a
real (but throwaway) provider call before it is ever persisted, and
writing a validated key to the SAME credentials file amplifier-agent's own
library already reads on every turn --
``~/.amplifier-agent/credentials.json`` (or ``$AMPLIFIER_AGENT_HOME`` if
set; see ``amplifier_agent_lib.persistence.amplifier_agent_home``).

Resolution order (env first, per the owner's explicit direction -- "I'd
prefer to read env first, do it right") is NOT reimplemented here: it
already lives in
``amplifier_agent_cli.provider_sources.resolve_credential_detailed`` and is
used verbatim by both this module's status reporting (:func:`resolve_status`)
and by ``runner.py``'s per-turn ``inject_provider`` call. This module only
adds a WRITE path (persist a validated key) that mirrors
``amplifier-agent auth set``'s own file format, so a key saved through
muxplex's Settings -> Agent panel is picked up automatically the moment the
environment variable is absent -- one mechanism (the credentials file),
never two.

No subprocess, no ``aa-svc``, no ``systemctl`` -- the sidecar's design
(``docs/designs/agent-credentials.md``) shells out to a separate OS process
because ITS credential store belongs to a different, isolated user. In
embedded mode the credential store belongs to muxplex's own process, so
every operation here is a plain, in-process function call.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("muxplex.agent_embedded.credentials")

#: Mirrors main.py's ``_AGENT_CREDENTIAL_ALLOWED_PROVIDERS`` -- key-only
#: providers this UI may ever set. ``azure-openai``/``ollama`` carry a
#: caller-controlled endpoint/host (see docs/designs/agent-credentials.md
#: SS3.6/SS7.3); ``github-copilot`` is environment-only and ``auth set``
#: refuses it upstream. Duplicated here (not imported from main.py) so this
#: module has no dependency on the FastAPI app -- main.py imports FROM
#: here, never the reverse. A test pins the two constants stay equal.
ALLOWED_PROVIDERS: frozenset[str] = frozenset({"anthropic", "openai"})


def _mask(value: str) -> str:
    """Display-safe redaction: first 6 + last 4 chars.

    Matches the exact convention ``amplifier_agent_cli.admin.auth._mask``
    uses, reimplemented locally (a handful of lines) rather than importing
    a leading-underscore symbol across a package boundary for a one-line
    display convention -- see IMPLEMENTATION_PHILOSOPHY.md's "conventions
    via instructions, not code."
    """
    if not value:
        return "<not set>"
    if len(value) <= 12:
        return "***"
    return f"{value[:6]}...{value[-4:]}"


def resolve_status(provider: str) -> dict[str, Any]:
    """Resolve one provider's credential status for the Settings -> Agent
    panel: source (``"env"`` | ``"file"`` | ``"not_set"``), masked display
    value, and the env var name a user would export to override a stored
    key.

    Pure and synchronous -- only imports
    ``amplifier_agent_cli.provider_sources`` (no bundle preparation), so it
    is cheap to call on every ``GET /api/agent/provider-credential``
    without waiting on the embedded runner's ``_get_prepared()``.
    """
    from amplifier_agent_cli.provider_sources import resolve_credential_detailed

    resolution = resolve_credential_detailed(provider)
    api_key = resolution.fields.get("api_key", "")
    masked = _mask(api_key) if resolution.resolved and api_key else None
    return {
        "source": resolution.source if resolution.resolved else "not_set",
        "masked": masked,
        "env_var": resolution.env_var,
    }


async def _enumerate_models(
    provider: str, api_key: str, *, timeout_seconds: float = 15.0
) -> tuple[str, str, list[Any] | None]:
    """Ask *provider* what it serves, once, using *api_key*, and return
    ``(verdict, detail, raw_models)``.

    THE single place muxplex asks a provider to enumerate its models. Two
    callers want that answer for two different reasons:
    :func:`validate_key` only cares whether the call SUCCEEDED (is this
    candidate key any good?), while :func:`served_model_check` needs the
    LIST itself (is the model the panel displays actually served?). A
    second copy of this instantiate/call/close dance would be a second
    thing to keep correct, and the enumeration half is the part with a
    live network call in it.

    Never touches the credentials file or any process-wide state: the key
    is used to instantiate the provider class directly and discarded when
    this function returns, so a concurrent turn using the REAL stored/env
    credential is never at risk.

    Verdicts (this taxonomy is :func:`validate_key`'s original one,
    unchanged, so its contract is preserved exactly):

    * ``"ok"``             -- the provider answered; detail names how many models came back.
    * ``"bad_key"``        -- the provider rejected the credential (401/auth error).
    * ``"unreachable"``    -- timeout or a non-auth connection error.
    * ``"module_missing"`` -- the provider's Python module isn't installed.
    * ``"error"``          -- couldn't even attempt the call (bad plumbing).

    ``raw_models`` is ``None`` for every non-``"ok"`` verdict -- ABSENT,
    never an empty list. That distinction is load-bearing downstream: an
    empty list means "the provider answered, and named nothing", which is
    a legitimate answer for some providers; ``None`` means "we never got
    an answer at all". Collapsing the two would let a failed call read as
    a provider that serves no models -- i.e. it would turn "I could not
    check" into "your model is not served", which is precisely the
    confidently-wrong failure this seam exists to prevent.
    """
    try:
        # Reaching into amplifier_agent_cli.admin.models' leading-underscore
        # helpers is deliberate, not an accident: this package already
        # imports amplifier-agent's private internals elsewhere (see
        # runner.py's module docstring) because there is no public
        # "validate a credential without persisting it" API upstream yet.
        # `load_provider_class` alone collapses "module not installed" and
        # "no provider class found" into a silent None, which would lose
        # the bad_key/unreachable/module_missing distinction the sidecar's
        # own validation reported -- so the private loader is used
        # directly to keep that taxonomy intact.
        from amplifier_agent_cli.admin.models import (
            _load_provider_module,
            _try_instantiate_provider,
            load_provider_class,
        )
    except ImportError as exc:
        return "error", f"amplifier-agent CLI package not importable: {exc}", None

    try:
        _load_provider_module(provider)
    except ImportError as exc:
        return (
            "module_missing",
            f"provider module not installed for {provider!r}: {exc}",
            None,
        )

    provider_class = load_provider_class(provider)
    if provider_class is None:
        return "error", f"no provider class found for {provider!r}", None

    instance = _try_instantiate_provider(
        provider_class, credentials={"api_key": api_key}
    )
    if instance is None:
        return "error", f"could not instantiate the {provider!r} provider class", None

    try:
        list_models = instance.list_models
        if asyncio.iscoroutinefunction(list_models):
            models = await asyncio.wait_for(list_models(), timeout=timeout_seconds)
        else:
            models = await asyncio.wait_for(
                asyncio.to_thread(list_models), timeout=timeout_seconds
            )
    except TimeoutError:
        return (
            "unreachable",
            f"timed out after {timeout_seconds}s calling {provider!r}'s API",
            None,
        )
    except Exception as exc:  # noqa: BLE001 -- classified by the provider's own error text below
        combined = str(exc).lower()
        bad_key_markers = (
            "authenticationerror",
            "401",
            "unauthorized",
            "invalid api key",
            "invalid x-api-key",
            "incorrect api key",
        )
        if any(marker in combined for marker in bad_key_markers):
            return "bad_key", f"{type(exc).__name__}: {exc}", None
        return "unreachable", f"{type(exc).__name__}: {exc}", None
    finally:
        close = getattr(instance, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                if asyncio.iscoroutinefunction(close):
                    await close()
                else:
                    close()

    if not models:
        # Some providers (azure-openai) legitimately return an empty list
        # by design even with a valid key -- matches list_provider_models's
        # own "no live model list available" advisory rather than treating
        # empty as failure (see amplifier_agent_cli/admin/models.py's
        # docstring contract table).
        return "ok", "0 models returned (may be expected for this provider)", []
    return "ok", f"{len(models)} model(s) returned", list(models)


async def validate_key(
    provider: str, api_key: str, *, timeout_seconds: float = 15.0
) -> tuple[str, str]:
    """Validate *api_key* for *provider* with a REAL, lightweight provider
    call -- the in-process equivalent of the sidecar's scratch-home
    ``auth set`` + ``models list --provider`` validation (see
    docs/designs/agent-credentials.md SS3.3).

    A thin projection of :func:`_enumerate_models` onto the only question
    this caller asks: did the call work? The served list itself is
    discarded here on purpose -- a candidate key being validated is not
    necessarily the credential a turn will actually use (env still wins
    over a stored key), so caching THIS list against the resolved
    credential would be wrong. :func:`served_model_check` does that
    against the resolved credential instead.

    Returns ``(verdict, detail)``; see :func:`_enumerate_models` for the
    verdict taxonomy, which this preserves unchanged.
    """
    verdict, detail, _models = await _enumerate_models(
        provider, api_key, timeout_seconds=timeout_seconds
    )
    return verdict, detail


#: How long a SUCCESSFUL enumeration is reused before the provider is
#: asked again.
#:
#: A served-model list changes on the order of months; a live API call on
#: every Settings -> Agent open is disproportionate to that, and the item
#: this implements (muxplex-y15) called for a caching policy by name. Only
#: successes are cached -- a timeout or a refused key is never remembered,
#: because a transient failure that stuck for five minutes would keep
#: reporting "could not check" long after the provider came back.
SERVED_MODELS_TTL_SECONDS: float = 300.0

#: provider -> (credential fingerprint, monotonic fetch time, model ids).
#:
#: Keyed by a fingerprint of the RESOLVED credential, not just by
#: provider, so swapping the environment variable or saving a new key
#: through the panel invalidates the entry by construction rather than by
#: remembering to call something. The fingerprint is a truncated SHA-256
#: -- never the key, and never even the masked form, which is displayed
#: and therefore reversible-ish by eye.
_served_models_cache: dict[str, tuple[str, float, tuple[str, ...]]] = {}
_served_models_lock = asyncio.Lock()


def _credential_fingerprint(api_key: str) -> str:
    """Stable, non-reversible cache key for a credential."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def clear_served_models_cache() -> None:
    """Forget every cached enumeration.

    Exists for tests and for any future caller that knows the credential
    landscape changed underneath us. Not called on the persist path: a
    newly stored key changes the fingerprint, so the entry is already
    invalid without anyone having to remember this function exists.
    """
    _served_models_cache.clear()


def _model_ids(raw_models: list[Any]) -> list[str] | None:
    """Normalise whatever ``list_models()`` returned into model id
    strings, or ``None`` if the shape cannot be read.

    ``None`` -- not ``[]`` -- is the answer for an unreadable shape, and
    that is the whole reason this function exists separately. An empty
    list flows downstream as "the provider serves nothing we recognise",
    which would render as "your model is not served"; a shape we simply
    could not parse must render as "could not check" instead. muxplex does
    not own ``list_models()``'s return type (it lives in amplifier-agent's
    provider classes), so a future upstream change to it must degrade to
    an honest unknown rather than to a confident, wrong accusation.

    Three shapes are accepted because all three are plausible for a list
    of models and none costs anything to support: bare strings, objects
    with an ``id``/``name``/``model`` attribute (the shape this repo's own
    test doubles use), and mappings with those keys.
    """
    ids: list[str] = []
    for entry in raw_models:
        if isinstance(entry, str):
            value: Any = entry
        elif isinstance(entry, dict):
            value = entry.get("id") or entry.get("name") or entry.get("model")
        else:
            value = (
                getattr(entry, "id", None)
                or getattr(entry, "name", None)
                or getattr(entry, "model", None)
            )
        if not isinstance(value, str) or not value:
            # One unreadable entry is enough: a partial list would let a
            # served model look absent purely because its entry was the
            # one we could not parse.
            return None
        ids.append(value)
    return ids


def _match_served(model: str, served: list[str]) -> str | None:
    """Return the served id that satisfies *model*, or ``None``.

    Exact match first. Failing that, a dash-delimited family match in
    either direction -- ``claude-sonnet-5`` against a served
    ``claude-sonnet-5-20260101``, or vice versa -- counts as served, and
    the caller names the exact served id it matched so the difference is
    visible rather than hidden.

    This asymmetry is deliberate, and it is the one judgement call in this
    file. A false "not served" tells a user their working configuration is
    broken and invites them to change something that was fine; a false
    "served" merely fails to warn, which is exactly where this feature
    started. The cost of the two mistakes is not symmetric, so the
    matching leans away from crying wolf.
    """
    if model in served:
        return model
    for candidate in served:
        if candidate.startswith(model + "-") or model.startswith(candidate + "-"):
            return candidate
    return None


def _resolve_api_key(provider: str) -> str:
    """Return the credential a real turn would use for *provider*, or ``""``.

    The same env-first chain :func:`resolve_status` reports on and
    ``runner.check_available()`` gates on -- not a second resolution
    order. Only the KEY is returned, and only to :func:`served_model_check`
    one stack frame away; it is never stored, never cached (its
    fingerprint is), and never leaves the process.

    Split out as a named function for the same reason ``resolve_status``
    is one: it is the single place this module touches amplifier-agent's
    resolver, which makes it the seam a test can stand in for. Without it,
    every test of the served-model logic would need the optional ``agent``
    extra installed and would skip on the environments where this logic is
    most likely to be changed blind.
    """
    from amplifier_agent_cli.provider_sources import resolve_credential_detailed

    resolution = resolve_credential_detailed(provider)
    if not resolution.resolved:
        return ""
    return (resolution.credentials or {}).get("api_key", "") or ""


async def served_model_check(*, timeout_seconds: float = 15.0) -> dict[str, Any]:
    """Answer, honestly, whether the provider actually serves the model
    the Agent panel displays.

    THE POINT: before this, the panel showed the model muxplex BELIEVES it
    is using (muxplex-nnl pinned that display to the runner's own
    constant, so it cannot drift from what a turn sends). Nothing checked
    that the provider will serve it. A renamed, retired, or mistyped model
    id displayed with total confidence and failed only at turn time, mid
    stream -- the software knowing something the user does not.

    THREE OUTCOMES, deliberately distinct, because collapsing any two of
    them recreates the defect in a new place::

        status="validated"   the provider serves this model.
        status="not_served"  the provider answered, and this model is not
                             in the list. `served` names what IS available.
        status="unknown"     we could not check. `reason` says why.

    ``"unknown"`` must read as neither a pass nor a failure. Every path
    that cannot produce a real answer -- no library, no credential, a
    refused key, a timeout, a provider that enumerates nothing, a model
    list in a shape we cannot parse -- lands here rather than being
    rounded to the nearest confident verdict.

    NOT ON THE GATE PATH, and that is structural rather than incidental.
    ``full_status()`` backs both Settings -> Agent AND ``checkAgentGate()``,
    which chat.js polls, and that gate FAILS OPEN on error by design
    (muxplex-at9). Putting a live provider round-trip in ``full_status()``
    would make the gate slow, network-dependent, and would let a provider
    blip influence whether the panel is usable. So this is a separate
    function behind a separate endpoint, requested only when the settings
    tab renders.
    """
    from . import runner as _runner

    library_reason = await _runner.library_unavailable_reason()
    if library_reason:
        # No importable runner means no provider to ask and no model whose
        # servability could be in question -- the same reason full_status()
        # reports a null active provider/model here.
        return {
            "status": "unknown",
            "reason": "library_missing",
            "provider": None,
            "model": None,
            "served": None,
            "detail": library_reason,
        }

    provider = _runner.active_provider()
    model = _runner.default_model()

    api_key = _resolve_api_key(provider)
    if not api_key:
        return {
            "status": "unknown",
            "reason": "no_credential",
            "provider": provider,
            "model": model,
            "served": None,
            "detail": (
                f"No {provider} credential is set, so the served model list "
                "cannot be read. This is not a sign the model is wrong."
            ),
        }

    fingerprint = _credential_fingerprint(api_key)
    # The lock is held ACROSS the provider call, deliberately: it makes
    # this single-flight. Two settings tabs opening at once produce one
    # request, and the second caller finds the cache warm rather than
    # duplicating a live round-trip. The cost is that the second caller
    # waits out the first one's timeout -- which is the right trade for a
    # panel line, and is bounded by `timeout_seconds` either way.
    async with _served_models_lock:
        cached = _served_models_cache.get(provider)
        fresh = (
            cached is not None
            and cached[0] == fingerprint
            and (time.monotonic() - cached[1]) < SERVED_MODELS_TTL_SECONDS
        )
        if cached is not None and fresh:
            served = list(cached[2])
        else:
            verdict, detail, raw_models = await _enumerate_models(
                provider, api_key, timeout_seconds=timeout_seconds
            )
            if verdict != "ok" or raw_models is None:
                return {
                    "status": "unknown",
                    "reason": verdict if verdict != "ok" else "error",
                    "provider": provider,
                    "model": model,
                    "served": None,
                    "detail": (
                        f"Could not read {provider}'s model list: {detail}. "
                        "This is not a sign the model is wrong."
                    ),
                }
            ids = _model_ids(raw_models)
            if ids is None:
                return {
                    "status": "unknown",
                    "reason": "unreadable_model_list",
                    "provider": provider,
                    "model": model,
                    "served": None,
                    "detail": (
                        f"{provider} returned a model list in a shape muxplex "
                        "could not read, so the model could not be checked."
                    ),
                }
            if not ids:
                # Legitimate for some providers -- validate_key's own
                # docstring already notes azure-openai returns an empty
                # list by design with a perfectly good key. Not evidence
                # the model is absent; evidence there is nothing to check
                # against.
                return {
                    "status": "unknown",
                    "reason": "no_enumeration",
                    "provider": provider,
                    "model": model,
                    "served": [],
                    "detail": (
                        f"{provider} does not publish a model list, so the "
                        "model could not be checked against one."
                    ),
                }
            served = ids
            _served_models_cache[provider] = (
                fingerprint,
                time.monotonic(),
                tuple(ids),
            )

    matched = _match_served(model, served)
    if matched is None:
        return {
            "status": "not_served",
            "reason": None,
            "provider": provider,
            "model": model,
            "served": served,
            "detail": (
                f"{provider} does not serve {model!r}. A turn using it will "
                "fail. Available models: " + ", ".join(sorted(served))
            ),
        }
    if matched != model:
        return {
            "status": "validated",
            "reason": None,
            "provider": provider,
            "model": model,
            "served": served,
            "detail": f"{provider} serves {model!r} as {matched!r}.",
        }
    return {
        "status": "validated",
        "reason": None,
        "provider": provider,
        "model": model,
        "served": served,
        "detail": f"{provider} serves {model!r}.",
    }


def persist_key(provider: str, api_key: str) -> Path:
    """Write *api_key* to the SAME credentials file
    ``resolve_credential_detailed`` (and therefore every embedded turn)
    already reads -- ``~/.amplifier-agent/credentials.json`` /
    ``$AMPLIFIER_AGENT_HOME``, mode 0600. Reuses the library's own
    load/save primitives
    (``amplifier_agent_cli.admin.auth._load_credentials`` /
    ``_save_credentials``) rather than hand-writing the file's JSON, for
    the exact reason docs/designs/agent-credentials.md SS3.2 gives for the
    sidecar's ``auth set`` call: the v1 envelope + legacy-shape upgrade
    path is owned by amplifier-agent, not muxplex, and reimplementing it
    creates a drift surface that breaks silently on an amplifier-agent
    upgrade.

    Only ever called after :func:`validate_key` returns ``"ok"`` -- see
    docs/designs/agent-credentials.md SS3.3 ("validate before
    persisting").
    """
    from amplifier_agent_cli.admin.auth import (
        _load_credentials,
        _save_credentials,
    )

    data = _load_credentials()
    providers = data.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        data["providers"] = providers
    entry = providers.get(provider) or {}
    if not isinstance(entry, dict):
        entry = {}
    entry["api_key"] = api_key
    providers[provider] = entry
    return _save_credentials(data)


async def full_status() -> dict[str, Any]:
    """Compose the full ``GET /api/agent/provider-credential`` response
    body for embedded mode.

    ``state`` (and therefore whether the chat panel's gate opens) is
    anchored on the SAME provider ``runner.py`` actually mounts for a turn
    (:func:`muxplex.agent_embedded.runner.active_provider`) -- not on
    "is any allowlisted provider configured". The two are not
    interchangeable: the credential form offers both anthropic and openai,
    but the embedded runner only ever mounts one provider per turn. A key
    stored for a provider the runner does not use must never flip the gate
    open (muxplex-fx1's whole point: never claim the agent is usable and
    then fail on the first real turn).

    ``providers`` still reports every allowlisted provider's resolution --
    that part IS purely informational, matching the sidecar's shape so the
    Settings -> Agent tab's per-provider display code (chat.js
    ``_renderAgentCredentialStatus``) works unmodified for both modes.

    ``active`` (muxplex-nnl) answers the question nothing in the UI could
    answer before: WHICH provider and model am I talking to. Both values
    come from the runner itself (:func:`~muxplex.agent_embedded.runner.
    active_provider` / :func:`~muxplex.agent_embedded.runner.default_model`)
    rather than being restated here, so the panel cannot display a
    provider/model pair the runner would not actually mount.

    ``None`` in either field means UNKNOWN and must render as such. It is
    not a hole to paper over with a plausible default: when the library
    isn't importable there is no runner to have an active anything, and
    "anthropic / claude-sonnet-5" printed under those conditions would be
    a confident lie about a server that cannot run a turn at all.

    THE ``models`` FIELD IS GONE (muxplex-y15), deliberately rather than
    by oversight. It was a sidecar-shape leftover -- ``[]`` on every
    embedded response since embedded mode began, with zero readers in the
    frontend. muxplex-nnl noted it and left it; that was the right call
    then and the wrong one to repeat, because this change finally gives
    the served-model list a real home
    (:func:`served_model_check`, behind ``GET /api/agent/served-models``).
    Populating it HERE was the tempting alternative and is the one thing
    that must not happen: this response backs ``checkAgentGate()``, which
    chat.js polls, so a live provider round-trip in this function would
    make the gate network-dependent. Keeping an always-empty ``models``
    alongside a real served list elsewhere would leave two answers to one
    question, and the permanently-empty one is the one a reader would
    find first.
    """
    from . import runner as _runner

    library_reason = await _runner.library_unavailable_reason()
    if library_reason:
        return {
            "state": "not_installed",
            "message": library_reason,
            "providers": {},
            "sidecar": "running",
            "mode": "embedded",
            # Unknown, deliberately -- see the docstring. There is no
            # importable runner here, so there is nothing whose active
            # provider/model this could truthfully report.
            "active": {"provider": None, "model": None},
        }

    providers = {p: resolve_status(p) for p in sorted(ALLOWED_PROVIDERS)}
    active_provider = _runner.active_provider()
    active_status = providers.get(active_provider) or resolve_status(active_provider)

    if active_status["source"] not in ("env", "file"):
        state = "not_configured"
        message = "The Agent has no model provider key. It cannot run until one is set."
    elif active_status["source"] == "env":
        state = "configured_shadowed"
        message = "Embedded agent ready (using an environment-variable credential)."
    else:
        state = "configured"
        message = "Embedded agent ready."

    return {
        "state": state,
        "message": message,
        "providers": providers,
        # Always "running": embedded mode has no separate service process
        # to be down, and (unlike the sidecar) NEVER needs a restart to
        # pick up a newly-stored credential -- see persist_key's docstring.
        # This is also what keeps chat.js's restart-warning banner hidden
        # in every embedded state (its visibility is driven by this exact
        # field), which is correct: that warning describes a cost embedded
        # mode never has.
        "sidecar": "running",
        "mode": "embedded",
        # Reported even when `state` is "not_configured": the library IS
        # here, so the runner's provider/model are real facts about what a
        # turn would mount. Whether a credential exists is a separate
        # question, already answered by `state` and `providers`.
        "active": {"provider": active_provider, "model": _runner.default_model()},
    }


__all__ = [
    "ALLOWED_PROVIDERS",
    "SERVED_MODELS_TTL_SECONDS",
    "clear_served_models_cache",
    "full_status",
    "persist_key",
    "resolve_status",
    "served_model_check",
    "validate_key",
]
