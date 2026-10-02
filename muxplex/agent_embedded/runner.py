# pyright: reportMissingImports=false
"""Public SDK handles only; a single event pump owns each live turn.

Credential construction is lane A's stable service-wide environment seam.
Durable SDK storage and muxplex owner/interruption metadata have separate roots.
No SDK private storage, kernel mounting, or new turns for browser tool results.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from . import credentials, wire
from .bridge import BrowserBridge, validate_result
from .errors import AgentRequestError
from .host_tool_glue import browser_tools
from .message_shape import browser_protocol, turn_input, validate_messages
from .state import SessionLease

logger = logging.getLogger(__name__)
_PROVIDER_ID = "anthropic"
_DEFAULT_MODEL_ID = "claude-sonnet-5"
_KEEPALIVE_INTERVAL_SECONDS = 3.0
_DRAIN_SECONDS = 8.0
LIBRARY_MISSING_MESSAGE = (
    "The Agent isn't installed on this server yet. Whoever runs muxplex can "
    "install it with: muxplex ensure-agent"
)
_runs: dict[str, LiveRun] = {}


def active_provider() -> str:
    return _PROVIDER_ID


def default_model() -> str:
    return _DEFAULT_MODEL_ID


async def library_unavailable_reason() -> str | None:
    try:
        from amplifier_agent import (
            AgentOptions,
            SessionOptions,
            TurnInput,
            create_agent,
        )

        # Public surface inspection only, no bundle activation or agent creation.
        if not all((AgentOptions, SessionOptions, TurnInput, create_agent)):
            return LIBRARY_MISSING_MESSAGE
    except (ImportError, OSError):
        return LIBRARY_MISSING_MESSAGE
    return None


async def check_available() -> str | None:
    reason = await library_unavailable_reason()
    if reason:
        return reason
    status = credentials.resolve_status(_PROVIDER_ID)
    if status["source"] == "not_set":
        return (
            f"No {_PROVIDER_ID} credential is configured. Set one via Settings -> Agent "
            f"or export {status.get('env_var') or 'the provider environment variable'}."
        )
    return None


class LiveRun:
    def __init__(
        self, *, owner: str, session_id: str, model: str, browser: bool
    ) -> None:
        self.run_id = uuid.uuid4().hex
        self.session_id = session_id
        self.owner = owner
        self.model = model
        self.browser = browser
        self.queue: asyncio.Queue = asyncio.Queue()
        self.bridge = (
            BrowserBridge(owner, session_id, self.run_id, self.queue)
            if browser
            else None
        )
        self.lease: SessionLease | None = None
        self.agent: Any = None
        self.session: Any = None
        self.turn: Any = None
        self.pump: asyncio.Task | None = None
        self.terminal: Any = None
        self.pump_error: Exception | None = None
        self.closed = False
        self.closing = False
        self.cleanup_task: asyncio.Task | None = None
        self.prepared = asyncio.Event()
        self.prepared.set()  # manual/test LiveRuns have no in-flight admission

    @property
    def headers(self) -> dict[str, str]:
        return {
            "X-Muxplex-Agent-Session-Id": self.session_id,
            "X-Muxplex-Agent-Run-Id": self.run_id,
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        }

    async def _pump(self) -> None:
        """The ONLY consumer of turn.events(), including during cancellation."""
        try:
            previous = 0
            async for event in self.turn.events():
                if (
                    event.session_id != self.turn.info.session_id
                    or event.turn_id != self.turn.info.turn_id
                    or event.sequence != previous + 1
                    or event.contract_version != "turn-events/1"
                    or self.terminal is not None
                ):
                    raise AgentRequestError(
                        "invalid_event_stream",
                        "Agent event identity/order is invalid.",
                        "Start a new conversation.",
                        502,
                    )
                previous = event.sequence
                if event.type == "terminal":
                    self.terminal = event.payload
                if not self.closing:
                    self.queue.put_nowait(event)
        except Exception as exc:
            self.pump_error = exc
        finally:
            self.queue.put_nowait(None)

    async def stream(self) -> AsyncGenerator[bytes, None]:
        chunk_id = wire.new_chunk_id()
        usage = None
        try:
            yield wire.sse_data(wire.role_chunk(chunk_id, self.model))
            while True:
                try:
                    event = await asyncio.wait_for(
                        self.queue.get(), _KEEPALIVE_INTERVAL_SECONDS
                    )
                except TimeoutError:
                    yield wire.sse_keepalive()
                    continue
                if event is None:
                    break
                if isinstance(event, dict):
                    # Only callback invocation produces capability frames.
                    yield wire.sse_data(event)
                elif event.type == "output_delta":
                    text = "".join(part.text for part in event.payload.content)
                    if text:
                        yield wire.sse_data(
                            wire.content_delta_chunk(chunk_id, self.model, text)
                        )
                elif event.type == "usage":
                    usage = event.payload.snapshot  # full cumulative replacement
                # tool_call/tool_result/approvals/progress are observations only.
            if self.pump_error is not None:
                yield wire.sse_data(wire.public_error(self.pump_error))
                return
            result = self.terminal
            if result is None:
                yield wire.sse_data(
                    wire.error_envelope(
                        "incomplete_turn",
                        "Agent stream ended without a terminal result.",
                        "Start a new conversation; do not retry uncertain effects.",
                    )
                )
                return
            if result.state != "success":
                if result.error is not None:
                    yield wire.sse_data(wire.public_error(result.error))
                else:
                    yield wire.sse_data(
                        wire.error_envelope(
                            f"turn_{result.state}",
                            f"Agent turn {result.state}.",
                            "Start a new conversation; do not retry uncertain effects.",
                        )
                    )
                return
            yield wire.sse_data(
                wire.stop_chunk(chunk_id, self.model, usage=result.usage or usage)
            )
            yield wire.sse_done()
        finally:
            # ASGI disconnect cancels this generator, not the event pump. Shield
            # cleanup so the existing pump drains cancellation pairs/terminal.
            cleanup = asyncio.create_task(self.close())
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise

    async def close(self) -> None:
        if self.cleanup_task is None:
            self.closing = True
            if self.bridge is not None:
                self.bridge.close()
            self.cleanup_task = asyncio.create_task(self._cleanup())
        # SDK v0.20.0 deliberately defers cancellation in close/cancel. A
        # wait_for timeout would itself wait indefinitely for that deferral.
        # Supervise instead: do NOT cancel/abandon handles or release a lease
        # while cleanup still owns them. A slow run remains quarantined.
        done, _ = await asyncio.wait({self.cleanup_task}, timeout=_DRAIN_SECONDS)
        if done:
            self.cleanup_task.result()
        else:
            logger.warning("agent cleanup still active; session remains quarantined")

    async def _cleanup(self) -> None:
        try:
            # Shutdown may close a run during awaited Agent/Session creation.
            # Keep the lease/registry and own every handle that admission returns.
            await self.prepared.wait()
            if self.turn is not None and self.terminal is None:
                with contextlib.suppress(Exception):
                    await self.turn.cancel()
            if self.pump is not None:
                await self.pump
        finally:
            for handle in (self.session, self.agent):
                if handle is not None:
                    try:
                        await handle.close()
                    except Exception:
                        logger.warning("agent handle close failed (details withheld)")
            # Even terminal may report a FAILED durable commit (including
            # cancelled + persistence_error). Keep the marker until the NEXT
            # freshly resumed public history proves that exact turn persisted.
            if self.lease is not None:
                self.lease.close()
            self.closed = True
            _runs.pop(self.run_id, None)


async def prepare_chat(body: dict[str, Any], *, owner: str = "") -> LiveRun:
    """Prepare headers/session before SSE; register run before callbacks exist."""
    browser = browser_protocol(body)
    messages = validate_messages(body, browser=browser)
    if browser and not owner:
        raise AgentRequestError(
            "operator_cookie_required",
            "Browser tools require a verified operator session cookie.",
            "Sign in to muxplex in this browser.",
            403,
        )
    from amplifier_agent import AgentOptions, SessionOptions

    model = body.get("model") or _DEFAULT_MODEL_ID
    prior = body["muxplex_agent"].get("session_id") if browser else None
    session_id = prior or uuid.uuid4().hex
    run = LiveRun(owner=owner, session_id=session_id, model=model, browser=browser)
    run.prepared.clear()
    _runs[run.run_id] = run
    try:
        root = credentials.credential_home()
        if browser:
            run.lease = SessionLease(
                root / "muxplex-browser-state", session_id, owner, new=not prior
            )
        options = AgentOptions(
            provider=active_provider(),
            model=model,
            instructions=(
                "You are a muxplex dashboard assistant. Browser tools run with the "
                "logged-in user's existing authority. Terminal input requires the "
                "browser's exact-action confirmation and server input fences. "
                "Never repeat declined, refused, or uncertain terminal effects."
            ),
            tools=browser_tools(run.bridge) if browser else [],
            skills=[],
            mcp_servers=[],
            approvals="allow",  # callbacks only; effect confirmation stays browser-side
            tool_error_policy="continue",
            storage=root / "muxplex-sdk",
        )
        run.agent = await credentials.create_agent_with_credentials(options)
        if run.closing:
            raise AgentRequestError(
                "run_closing",
                "Agent admission was interrupted by shutdown.",
                "Start a new conversation after restart.",
                503,
            )
        if prior:
            try:
                run.session = await run.agent.resume_session(session_id)
            except Exception as exc:
                if run.lease is not None and run.lease.data.get("run") is not None:
                    raise AgentRequestError(
                        "session_interrupted",
                        "The prior turn cannot be reconciled through public SDK history.",
                        "Start a new conversation; do not replay an uncertain terminal effect.",
                        409,
                    ) from exc
                raise
            if run.lease is not None:
                run.lease.reconcile(run.session.history)
        else:
            run.session = await run.agent.create_session(
                SessionOptions(
                    session_id=session_id,
                    persistence="durable" if browser else "ephemeral",
                    model=model,
                )
            )
        if run.closing:
            raise AgentRequestError(
                "run_closing",
                "Agent admission was interrupted by shutdown.",
                "Start a new conversation after restart.",
                503,
            )
        if run.lease is not None:
            run.lease.mark(run.run_id)
        run.turn = await run.session.start_turn(
            turn_input(
                messages, model, browser=browser, context=body.get("context", "")
            )
        )
        run.pump = asyncio.create_task(run._pump())
        if run.lease is not None:
            run.lease.mark(run.run_id, run.turn.info.turn_id)
        run.prepared.set()
        if run.closing:
            raise AgentRequestError(
                "run_closing",
                "Agent admission was interrupted by shutdown.",
                "Start a new conversation after restart.",
                503,
            )
        return run
    except BaseException:
        run.prepared.set()
        await run.close()
        raise


def submit_browser_result(body: Any, *, owner: str) -> None:
    body = validate_result(body)
    run = _runs.get(body["run_id"])
    if run is None or run.bridge is None:
        raise AgentRequestError(
            "run_closed",
            "Agent run is closed or unknown.",
            "Do not retry uncertain effects.",
            410,
        )
    run.bridge.resolve(body, owner)


async def shutdown() -> None:
    await asyncio.gather(
        *(run.close() for run in list(_runs.values())), return_exceptions=True
    )


async def stream_embedded_chat_completion(
    body: dict[str, Any], *, client_session_id: str = ""
) -> AsyncGenerator[bytes, None]:
    """Compatibility entry for ordinary stateless clients (never browser tools)."""
    del client_session_id  # legacy caller-supplied IDs grant no durable ownership
    if browser_protocol(body):
        raise AgentRequestError(
            "operator_cookie_required",
            "Browser runs require HTTP cookie verification.",
            "Sign in.",
            403,
        )
    run = await prepare_chat(body)
    async for chunk in run.stream():
        yield chunk
