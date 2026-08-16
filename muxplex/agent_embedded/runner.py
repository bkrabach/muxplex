# pyright: reportMissingImports=false
# amplifier-agent (amplifier_agent_lib / amplifier_agent_cli) is an OPTIONAL
# dependency -- see pyproject.toml's `agent` extra. Every import of it below
# is deliberately lazy (inside a function, inside a try/except ImportError)
# so muxplex runs fine without the extra installed (MUXPLEX_AGENT_MODE=sidecar,
# or check_available()'s clean error path). pyright can't resolve the module
# in an environment that hasn't installed the extra -- that's expected here,
# not a real missing-dependency bug, hence the file-level suppression rather
# than chasing per-line `# type: ignore` comments through ruff's import
# reformatting.
"""Embedded (in-process) amplifier-agent turn runner for muxplex.

Alongside (not replacing) the sidecar HTTP proxy in ``muxplex/main.py``
(``agent_chat_completions_proxy``), this module runs one amplifier-agent
chat turn IN-PROCESS as a Python library call, rather than proxying to a
separate ``amplifier-agent serve chat-completions`` OS process. Selected
via ``MUXPLEX_AGENT_MODE`` (default "embedded"; see ``agent_embedded/
__init__.py``).

The turn-execution shape (per-request provider injection under a lock,
fresh ``AmplifierSession`` per turn against one process-lifetime
``PreparedBundle``, queue-drained streaming with a keepalive, host-tool
yield detection via a side-channel dict) mirrors amplifier-agent's own
sidecar implementation (``amplifier_agent_http/_session_runner.py`` +
``routes/chat_completions.py``) closely -- that architecture is proven,
this module adapts it to run without that package, using only
``amplifier_agent_lib`` (session/bundle mechanics) and
``amplifier_agent_cli`` (provider injection), plus this package's own
ported host-tool glue and wire builders.

THE ONE THING THAT BITES: a continuation turn (``session.execute("")``
after seeding a ``{role: "tool"}`` result) fails with ``cache_control
cannot be set for empty text blocks`` unless the Anthropic provider's
``enable_prompt_caching`` is explicitly ``False``. This is a known,
filed-upstream bug the sidecar's own deployment already routes around
(see ``docs/AGENT_CHAT_SIDECAR.md`` \u00a73) -- ``_ENABLE_PROMPT_CACHING``
below defaults to the same workaround.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from . import wire
from .host_tool_glue import HostToolProxy, mount_host_tool_hook
from .message_shape import extract_host_tools, split_history_and_prompt

logger = logging.getLogger("muxplex.agent_embedded.runner")

#: Workspace name amplifier-agent uses for its own on-disk state (agent
#: overlays, per-session transcripts under context-intelligence, etc.).
#: Analogous to the sidecar's ``AMPLIFIER_AGENT_HTTP_WORKSPACE``.
_WORKSPACE = os.environ.get("MUXPLEX_AGENT_WORKSPACE", "muxplex-embedded")

#: How often to emit an SSE keepalive comment during silent phases
#: (extended thinking, multi-step internal tool runs). Matches the
#: sidecar's own interval (``chat_completions.py``).
_KEEPALIVE_INTERVAL_SECONDS: float = 3.0

_PROVIDER_ID = "anthropic"

#: A workaround for a filed upstream bug (see module docstring), NOT a
#: preference -- mirrors the sidecar's own host-config
#: (``docs/AGENT_CHAT_SIDECAR.md`` \u00a73: ``enable_prompt_caching: false``).
#: Re-check whether it's still needed before flipping this default.
_ENABLE_PROMPT_CACHING = os.environ.get(
    "MUXPLEX_AGENT_ENABLE_PROMPT_CACHING", "false"
).strip().lower() in (
    "1",
    "true",
    "yes",
)

# Guards the one-time PreparedBundle build (see _get_prepared).
_prepared_lock = asyncio.Lock()
# Guards the per-request mount_plan["providers"] swap + create_session
# sequence, mirroring _session_runner.py's _create_session_lock: mount_plan
# is shared, process-wide state, mutated transiently for one create_session
# call.
_create_session_lock = asyncio.Lock()
_prepared: Any = None


class EmbeddedAgentUnavailable(RuntimeError):
    """amplifier-agent isn't importable in this Python environment."""


async def _get_prepared() -> Any:
    """Build (once) and cache the PreparedBundle for this process's
    lifetime, mirroring the sidecar lifespan's one-bundle pattern."""
    global _prepared
    if _prepared is not None:
        return _prepared
    async with _prepared_lock:
        if _prepared is None:
            try:
                # amplifier-agent is an OPTIONAL dependency (see pyproject.toml's
                # `agent` extra) -- pyright can't resolve it in an environment
                # that hasn't installed the extra, and that's the point: this
                # import is deliberately lazy so muxplex runs fine without it
                # (MUXPLEX_AGENT_MODE=sidecar, or check_available()'s error path).
                from amplifier_agent_lib import __version__ as aaa_version
                from amplifier_agent_lib._runtime import prepare_bundle_for_session
                from amplifier_agent_lib.bundle.cache import load_and_prepare_cached
            except ImportError as exc:
                raise EmbeddedAgentUnavailable(
                    "amplifier-agent is not installed in this Python environment "
                    "(pip install amplifier-agent, or set MUXPLEX_AGENT_MODE=sidecar "
                    "to use the separate sidecar process instead)"
                ) from exc
            prepared = await load_and_prepare_cached(aaa_version=aaa_version)
            prepare_bundle_for_session(prepared, host_config={}, workspace=_WORKSPACE)
            _prepared = prepared
    return _prepared


async def check_available() -> str | None:
    """Return ``None`` if the embedded path is ready to run a turn, or a
    human-readable reason it is not (missing library / missing
    credential). Call this BEFORE opening the SSE stream so an
    unavailable embedded path returns a clean JSON error response instead
    of a stream that immediately emits an error frame.
    """
    try:
        await _get_prepared()
    except EmbeddedAgentUnavailable as exc:
        return str(exc)

    from amplifier_agent_cli.provider_sources import resolve_credential_detailed

    if not resolve_credential_detailed(_PROVIDER_ID).resolved:
        return (
            f"amplifier-agent embedded mode: no {_PROVIDER_ID} credential resolvable "
            "in this process's environment (ANTHROPIC_API_KEY unset)"
        )
    return None


async def stream_embedded_chat_completion(
    body: dict[str, Any], *, client_session_id: str = ""
) -> AsyncGenerator[bytes, None]:
    """Run one amplifier-agent turn in-process and yield raw SSE bytes.

    Wire-identical to what ``agent_chat_completions_proxy``'s relay has
    always forwarded from the sidecar. Any tool the model picks from
    ``body["tools"]`` is host-delegated (``HostToolProxy``): amplifier
    never executes it; the browser does, over its own ``/api/*`` fetch
    calls, and re-POSTs the result as a ``{role: "tool"}`` continuation
    turn -- the same recursive ``runTurn()`` chat.js has always used
    against the sidecar.
    """
    chunk_id = wire.new_chunk_id()
    model_id = body.get("model") or "claude-sonnet-5"

    # Defense in depth: main.py's route handler already calls
    # check_available() before opening the stream, but a race (library
    # uninstalled, credential revoked between the check and this call) is
    # cheap to guard here too -- as a graceful in-stream error rather than
    # a crash.
    try:
        prepared = await _get_prepared()
    except EmbeddedAgentUnavailable as exc:
        yield wire.sse_error(str(exc)).encode()
        return

    from amplifier_agent_cli.provider_sources import (
        inject_provider,
        resolve_credential_detailed,
    )
    from amplifier_agent_lib.bundle.hook_streaming import mount as mount_streaming_hook
    from amplifier_agent_lib.protocol_points.defaults_http import (
        HttpAutoApprovalSystem,
        HttpQueueDisplaySystem,
    )

    if not resolve_credential_detailed(_PROVIDER_ID).resolved:
        yield wire.sse_error(
            f"amplifier-agent embedded mode: no {_PROVIDER_ID} credential resolvable "
            "in this process's environment (ANTHROPIC_API_KEY unset)"
        ).encode()
        return

    messages = body.get("messages") or []
    history, prompt = split_history_and_prompt(messages)
    host_tool_specs = extract_host_tools(body.get("tools"))

    event_queue: asyncio.Queue[Any] = asyncio.Queue()
    display = HttpQueueDisplaySystem(event_queue)
    yield_state: dict[str, Any] = {
        "yielded": False,
        "tool_name": "",
        "tool_call_id": "",
    }

    sid = f"muxplex-embedded-{(client_session_id or uuid.uuid4().hex[:12])}"

    # Per-request provider injection + session creation, under the lock --
    # mount_plan["providers"] is shared, process-wide state (see
    # _create_session_lock's docstring above).
    async with _create_session_lock:
        saved_providers = list(prepared.mount_plan.get("providers") or [])
        prepared.mount_plan["providers"] = []
        inject_provider(
            prepared,
            _PROVIDER_ID,
            model_override=model_id,
            extra_config={"enable_prompt_caching": _ENABLE_PROMPT_CACHING},
        )
        try:
            session = await prepared.create_session(
                session_id=sid, session_cwd=Path.cwd(), is_resumed=False
            )
        finally:
            prepared.mount_plan["providers"] = saved_providers

    session.coordinator.register_capability("display.emit", display.emit)
    session.coordinator.register_capability(
        "approval.request", HttpAutoApprovalSystem().request
    )
    await mount_streaming_hook(session.coordinator, {})

    host_tool_names: list[str] = []
    if host_tool_specs:
        for spec in host_tool_specs:
            proxy = HostToolProxy(
                name=spec["name"],
                description=spec["description"],
                parameters=spec["parameters"],
            )
            await session.coordinator.mount("tools", proxy, name=proxy.name)
            host_tool_names.append(proxy.name)
        await mount_host_tool_hook(
            session.coordinator,
            {"host_tools": host_tool_names, "yield_state": yield_state},
        )
        logger.info(
            "embedded runner: host-tool delegation enabled: %d tool(s) -- %s",
            len(host_tool_names),
            host_tool_names,
        )

    if history:
        context_module = session.coordinator.get("context")
        if context_module is not None and hasattr(context_module, "set_messages"):
            await context_module.set_messages(history)
        else:
            logger.warning(
                "embedded runner: conversation seeding skipped: context module %r has no set_messages",
                context_module,
            )

    async def _run_turn() -> str | None:
        async with session:
            return await session.execute(prompt)

    turn_task: asyncio.Task[str | None] = asyncio.create_task(_run_turn())

    # Pre-flight window: give an immediately-failing turn (bad credential
    # caught downstream, provider rejects at first call, etc.) a brief
    # chance to fail BEFORE we commit to streaming the role chunk -- once
    # SSE bytes are on the wire there is no way back to a clean error
    # response. Mirrors chat_completions.py's "Edit C" pre-flight check.
    done, _ = await asyncio.wait([turn_task], timeout=0.05)
    if turn_task in done:
        exc = turn_task.exception()
        if exc is not None and not yield_state.get("yielded"):
            yield wire.sse_error(
                f"Provider initialization failed: {type(exc).__name__}: {exc}"
            ).encode()
            return

    async def _signal_done() -> None:
        try:
            await asyncio.shield(turn_task)
        except BaseException as exc:  # noqa: BLE001 -- intentional: must never crash the watcher
            logger.debug("embedded runner: turn task ended via %s", type(exc).__name__)
        finally:
            display.close()

    signal_task = asyncio.create_task(_signal_done())

    try:
        yield wire.sse_data(wire.role_chunk(chunk_id, model_id)).encode()

        usage_prompt = usage_completion = usage_cached = 0

        while True:
            try:
                event = await asyncio.wait_for(
                    event_queue.get(), timeout=_KEEPALIVE_INTERVAL_SECONDS
                )
            except TimeoutError:
                yield wire.sse_keepalive().encode()
                continue
            if event is None:
                break  # sentinel -- turn task is done (success, error, or cancel)
            if (u := wire.extract_usage(event)) is not None:
                usage_prompt += u.get("prompt_tokens", 0)
                usage_completion += u.get("completion_tokens", 0)
                usage_cached += u.get("cached_tokens", 0)
                continue
            chunk = wire.translate_event(event, chunk_id, model_id)
            if chunk is not None:
                yield wire.sse_data(chunk).encode()

        # Turn task has finished. Surface any exception now. finish_reason
        # is "tool_calls" if the host-tool hook signalled a yield (see
        # host_tool_glue.HostToolYield's docstring for why we check the
        # side-channel dict rather than the exception type).
        try:
            await turn_task
        except asyncio.CancelledError:
            logger.info(
                "embedded runner: turn task cancelled (client likely disconnected)"
            )
        except Exception as exc:
            if yield_state.get("yielded"):
                logger.info(
                    "embedded runner: turn ended with host-tool yield (wrapped): tool=%s id=%s -- wrapped exception: %s",
                    yield_state.get("tool_name") or "(unknown)",
                    yield_state.get("tool_call_id") or "(via hook)",
                    type(exc).__name__,
                )
            else:
                logger.exception("embedded runner: turn task raised")
                yield wire.sse_data(
                    wire.content_delta_chunk(
                        chunk_id,
                        model_id,
                        f"\n\n[amplifier-agent error: {type(exc).__name__}: {exc}]\n",
                    )
                ).encode()

        if yield_state.get("yielded"):
            yield wire.sse_data(
                wire.tool_calls_stop_chunk(
                    chunk_id,
                    model_id,
                    prompt_tokens=usage_prompt,
                    completion_tokens=usage_completion,
                    cached_tokens=usage_cached,
                )
            ).encode()
        else:
            yield wire.sse_data(
                wire.stop_chunk(
                    chunk_id,
                    model_id,
                    prompt_tokens=usage_prompt,
                    completion_tokens=usage_completion,
                    cached_tokens=usage_cached,
                )
            ).encode()
        yield wire.sse_done().encode()
    finally:
        # Cleanup: if the generator is closed before completion (e.g.
        # client disconnects mid-stream), cancel the turn task and the
        # watcher.
        if not turn_task.done():
            turn_task.cancel()
        if not signal_task.done():
            signal_task.cancel()
        await asyncio.gather(turn_task, signal_task, return_exceptions=True)
