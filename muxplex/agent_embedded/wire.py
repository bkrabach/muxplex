"""OpenAI-compatible text SSE plus typed, non-successful failure frames."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, cast

# Optional SDK imports are lazy in public_error.
# pyright: reportMissingImports=false


def new_chunk_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def _chunk(chunk_id: str, model: str, delta: dict, finish: str | None = None) -> dict:
    return {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def role_chunk(chunk_id: str, model: str) -> dict:
    return _chunk(chunk_id, model, {"role": "assistant"})


def content_delta_chunk(chunk_id: str, model: str, content: str) -> dict:
    return _chunk(chunk_id, model, {"content": content})


def usage_block(usage: Any) -> dict | None:
    """SDK tokens_in includes cache reads, excludes writes; never sum snapshots."""
    if usage is None or not usage.entries:
        return None
    entries = usage.entries
    # Missing is unknown, not a measured zero. Writes are included in the
    # OpenAI-facing prompt total only when known, just like input/output.
    prompt = (
        sum(entry.tokens_in + entry.cache_write_tokens for entry in entries)
        if all(
            entry.tokens_in is not None and entry.cache_write_tokens is not None
            for entry in entries
        )
        else None
    )
    completion = (
        sum(entry.tokens_out for entry in entries)
        if all(entry.tokens_out is not None for entry in entries)
        else None
    )
    cached = (
        sum(entry.cache_read_tokens for entry in entries)
        if all(entry.cache_read_tokens is not None for entry in entries)
        else None
    )
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion
        if prompt is not None and completion is not None
        else None,
        "prompt_tokens_details": {"cached_tokens": cached},
    }


def stop_chunk(chunk_id: str, model: str, *, usage: Any = None) -> dict:
    chunk = _chunk(chunk_id, model, {}, "stop")
    if (block := usage_block(usage)) is not None:
        chunk["usage"] = block
    return chunk


def sse_data(chunk: dict) -> bytes:
    return f"data: {json.dumps(chunk, separators=(',', ':'), allow_nan=False)}\n\n".encode()


def sse_done() -> bytes:
    return b"data: [DONE]\n\n"


def sse_keepalive() -> bytes:
    return b": keepalive\n\n"


def error_envelope(
    code: str, message: str, remedy: str, *, category: str = "server_error"
) -> dict:
    return {
        "error": {"type": category, "code": code, "message": message, "remedy": remedy}
    }


def public_error(exc: Exception) -> dict:
    # Only explicitly typed errors are exposed; raw provider/transport errors
    # can contain credentials or callback capability data.
    from .errors import AgentRequestError

    if isinstance(exc, AgentRequestError):
        return exc.envelope()
    try:
        from amplifier_agent import AgentError
    except ImportError:
        return error_envelope(
            "agent_unavailable",
            "The Agent isn't installed here.",
            "Run muxplex ensure-agent.",
        )
    if isinstance(exc, AgentError):
        error = cast(Any, exc)  # optional SDK may be absent in a base-only type-check
        return error_envelope(
            error.code, error.message, error.remedy, category=error.category
        )
    return error_envelope(
        "agent_failed",
        "The agent turn failed.",
        "Start a new conversation; do not repeat an uncertain terminal action.",
    )
