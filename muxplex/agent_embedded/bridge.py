# pyright: reportMissingImports=false
"""A live browser callback capability, bound to owner/session/run/call.

No terminal or HTTP effects execute here. A registered future is completed by
the verified browser's result POST, then the original SDK handler returns.
"""

from __future__ import annotations

import asyncio
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .errors import AgentRequestError

MAX_RESULT_BYTES = 131_072
CALL_TIMEOUT = 120.0
INPUT_TOOL = "send_muxplex_session_input"


@dataclass
class PendingCall:
    name: str
    token: str
    deadline: datetime
    future: asyncio.Future
    expired: bool = False


def validate_result(body: Any) -> dict:
    if not isinstance(body, dict) or set(body) - {
        "run_id",
        "call_id",
        "result_token",
        "outcome",
        "content",
        "error",
        "confirmed",
    }:
        raise AgentRequestError(
            "invalid_tool_result", "Invalid tool result object.", "Reload muxplex."
        )
    for key in ("run_id", "call_id", "result_token"):
        if not isinstance(body.get(key), str) or not body[key] or len(body[key]) > 200:
            raise AgentRequestError(
                "invalid_tool_result", f"Invalid {key}.", "Reload muxplex."
            )
    if not all(body[key].isascii() for key in ("run_id", "call_id", "result_token")):
        raise AgentRequestError(
            "invalid_tool_result",
            "Callback identities must be ASCII.",
            "Reload muxplex.",
        )
    if not isinstance(body.get("outcome"), str) or body["outcome"] not in {
        "completed",
        "failed",
        "unknown",
    }:
        raise AgentRequestError(
            "invalid_tool_result",
            "Invalid tool outcome.",
            "Report the actual effect outcome.",
        )
    if any(
        key in body and not isinstance(body[key], str) for key in ("content", "error")
    ):
        raise AgentRequestError(
            "invalid_tool_result", "Content/error must be strings.", "Reload muxplex."
        )
    if "confirmed" in body and type(body["confirmed"]) is not bool:
        raise AgentRequestError(
            "invalid_tool_result",
            "confirmed must be boolean.",
            "Use the confirmation dialog.",
        )
    if (
        sum(len(body.get(key, "").encode()) for key in ("content", "error"))
        > MAX_RESULT_BYTES
    ):
        raise AgentRequestError(
            "tool_result_too_large",
            "Tool result exceeds the byte limit.",
            "Return less output.",
            413,
        )
    if body["outcome"] == "completed" and "content" not in body:
        raise AgentRequestError(
            "invalid_tool_result",
            "Completed results require content.",
            "Return the effect result.",
        )
    return body


class BrowserBridge:
    def __init__(
        self, owner: str, session_id: str, run_id: str, queue: asyncio.Queue
    ) -> None:
        self.owner = owner
        self.session_id = session_id
        self.run_id = run_id
        self.queue = queue
        self.pending: dict[str, PendingCall] = {}
        self.closed = False
        self.typing_blocked = False
        self.uncertain = False
        self._serial = asyncio.Lock()

    async def invoke(self, name: str, arguments: dict, context: Any) -> str:
        from amplifier_agent import ToolFailed, ToolOutcomeUnknown

        # Serial dispatch prevents a sibling input from already being in flight
        # when a decline/fence refusal latches the run's typing prohibition.
        async with self._serial:
            if self.closed or self.uncertain:
                raise ToolOutcomeUnknown(
                    "Browser execution is closed or uncertain. Do not retry effects."
                )
            if name == INPUT_TOOL and self.typing_blocked:
                raise ToolFailed(
                    "Typing was declined or refused in this run. Do not retry."
                )
            call_id = context.call_id
            if not isinstance(call_id, str) or not call_id or call_id in self.pending:
                raise ToolFailed("Invalid or repeated SDK callback identity.")
            now = datetime.now(UTC)
            deadline = now + timedelta(seconds=CALL_TIMEOUT)
            if context.deadline is not None:
                deadline = min(deadline, context.deadline.astimezone(UTC))
            timeout = (deadline - now).total_seconds()
            if timeout <= 0:
                raise ToolFailed("Browser callback deadline expired before dispatch.")
            future = asyncio.get_running_loop().create_future()
            pending = PendingCall(name, secrets.token_urlsafe(32), deadline, future)
            # Register BEFORE disclosing the capability. Do not log this object.
            self.pending[call_id] = pending
            self.queue.put_nowait(
                {
                    "muxplex_browser_tool": {
                        "version": 1,
                        "run_id": self.run_id,
                        "session_id": self.session_id,
                        "call_id": call_id,
                        "result_token": pending.token,
                        "name": name,
                        "arguments": arguments,
                        "deadline": deadline.isoformat(),
                    }
                }
            )
            try:
                result = await asyncio.wait_for(asyncio.shield(future), timeout)
            except (TimeoutError, asyncio.CancelledError):
                pending.expired = True
                self.uncertain = True
                if not future.done():
                    future.set_result({"outcome": "unknown"})
                raise ToolOutcomeUnknown(
                    "Browser effect outcome is unknown. Do not retry."
                ) from None
            outcome = result["outcome"]
            if outcome == "unknown":
                self.uncertain = True
                raise ToolOutcomeUnknown(
                    "Browser effect outcome is unknown. Do not retry."
                )
            if outcome == "failed":
                if name == INPUT_TOOL:
                    self.typing_blocked = True
                raise ToolFailed(
                    result.get("error")
                    or "Browser action failed; do not repeat declined typing."
                )
            return result["content"]

    def resolve(self, body: dict, owner: str) -> None:
        if not hmac.compare_digest(self.owner, owner):
            raise AgentRequestError(
                "tool_owner_mismatch",
                "Tool result owner mismatch.",
                "Use the initiating browser.",
                403,
            )
        pending = self.pending.get(body["call_id"])
        if pending is None:
            raise AgentRequestError(
                "unknown_tool_call", "Unknown browser callback.", "Reload muxplex.", 410
            )
        if not hmac.compare_digest(pending.token, body["result_token"]):
            raise AgentRequestError(
                "tool_token_mismatch",
                "Tool capability mismatch.",
                "Use the initiating browser.",
                403,
            )
        if self.closed or pending.expired or datetime.now(UTC) >= pending.deadline:
            raise AgentRequestError(
                "tool_call_expired",
                "Browser callback is closed or expired.",
                "Do not retry uncertain effects.",
                410,
            )
        if pending.future.done():
            raise AgentRequestError(
                "duplicate_tool_result",
                "Browser callback already resolved.",
                "Do not resend this result.",
                409,
            )
        if (
            pending.name == INPUT_TOOL
            and body["outcome"] == "completed"
            and body.get("confirmed") is not True
        ):
            raise AgentRequestError(
                "confirmation_required",
                "Successful typing requires confirmed:true.",
                "Use the exact-action confirmation dialog.",
            )
        # Latch at receipt, before releasing the SDK handler/next serial call.
        if pending.name == INPUT_TOOL and body["outcome"] != "completed":
            self.typing_blocked = True
        if body["outcome"] == "unknown":
            self.uncertain = True
        pending.future.set_result(body)

    def close(self) -> None:
        self.closed = True
        for pending in self.pending.values():
            if not pending.future.done():
                pending.expired = True
                pending.future.set_result({"outcome": "unknown"})
