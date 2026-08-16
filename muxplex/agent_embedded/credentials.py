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
import logging
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


async def validate_key(
    provider: str, api_key: str, *, timeout_seconds: float = 15.0
) -> tuple[str, str]:
    """Validate *api_key* for *provider* with a REAL, lightweight provider
    call -- the in-process equivalent of the sidecar's scratch-home
    ``auth set`` + ``models list --provider`` validation (see
    docs/designs/agent-credentials.md SS3.3). Never touches the
    credentials file or any process-wide state: the candidate key is used
    to instantiate the provider class directly and discarded when this
    function returns, so a concurrent turn using the REAL stored/env
    credential is never at risk.

    Returns ``(verdict, detail)``:

    * ``"ok"``             -- key accepted; detail names how many models came back.
    * ``"bad_key"``        -- the provider rejected the credential (401/auth error).
    * ``"unreachable"``    -- timeout or a non-auth connection error.
    * ``"module_missing"`` -- the provider's Python module isn't installed.
    * ``"error"``          -- couldn't even attempt the call (bad plumbing).
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
        return "error", f"amplifier-agent CLI package not importable: {exc}"

    try:
        _load_provider_module(provider)
    except ImportError as exc:
        return (
            "module_missing",
            f"provider module not installed for {provider!r}: {exc}",
        )

    provider_class = load_provider_class(provider)
    if provider_class is None:
        return "error", f"no provider class found for {provider!r}"

    instance = _try_instantiate_provider(
        provider_class, credentials={"api_key": api_key}
    )
    if instance is None:
        return "error", f"could not instantiate the {provider!r} provider class"

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
            return "bad_key", f"{type(exc).__name__}: {exc}"
        return "unreachable", f"{type(exc).__name__}: {exc}"
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
        return "ok", "0 models returned (may be expected for this provider)"
    return "ok", f"{len(models)} model(s) returned"


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
    """
    from . import runner as _runner

    library_reason = await _runner.library_unavailable_reason()
    if library_reason:
        return {
            "state": "not_installed",
            "message": library_reason,
            "providers": {},
            "sidecar": "running",
            "models": [],
            "mode": "embedded",
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
        "models": [],
        "mode": "embedded",
    }


__all__ = [
    "ALLOWED_PROVIDERS",
    "full_status",
    "persist_key",
    "resolve_status",
    "validate_key",
]
