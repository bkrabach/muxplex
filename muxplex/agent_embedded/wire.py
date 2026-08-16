"""OpenAI Chat Completions wire-shape helpers -- ported subset.

Ported from amplifier-agent's sidecar-oriented ``amplifier_agent_http``
package (``_wire.py`` chunk builders + ``_event_translator.py``'s event
translation), for the same reason as ``host_tool_glue.py``: the embedded
path has zero import dependency on that package. Chunk shapes are
byte-for-byte compatible with what ``main.py``'s sidecar proxy has always
relayed -- chat.js cannot tell embedded and sidecar output apart, which is
the whole point of this pass.

Deliberately NOT ported (no consumer in ``frontend/chat.js`` -- verified,
not assumed): ``reasoning_delta_chunk`` / ``thinking/delta`` translation,
``activeMode`` (mode support isn't part of the embedded path this pass),
and ``cost_usd`` accumulation. Dropped the same as every other
internal-only kernel event chat.js already ignores.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any


def new_chunk_id() -> str:
    """Stable per-response chunk id, OpenAI shape ``chatcmpl-XXXXX``."""
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def _base_chunk(chunk_id: str, model: str) -> dict[str, Any]:
    return {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
    }


def role_chunk(chunk_id: str, model: str) -> dict[str, Any]:
    """First chunk of a stream -- announces the assistant role, no content."""
    chunk = _base_chunk(chunk_id, model)
    chunk["choices"] = [
        {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
    ]
    return chunk


def content_delta_chunk(chunk_id: str, model: str, content: str) -> dict[str, Any]:
    chunk = _base_chunk(chunk_id, model)
    chunk["choices"] = [
        {"index": 0, "delta": {"content": content}, "finish_reason": None}
    ]
    return chunk


def tool_call_delta_chunk(
    chunk_id: str,
    model: str,
    *,
    index: int,
    tool_call_id: str,
    name: str,
    arguments: str,
) -> dict[str, Any]:
    """A tool-call delta chunk. ``arguments`` MUST be a JSON-serialized
    string (not a dict) per OpenAI's wire."""
    chunk = _base_chunk(chunk_id, model)
    chunk["choices"] = [
        {
            "index": 0,
            "delta": {
                "tool_calls": [
                    {
                        "index": index,
                        "id": tool_call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": arguments},
                    }
                ],
            },
            "finish_reason": None,
        }
    ]
    return chunk


def _usage_block(
    *, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0
) -> dict[str, Any]:
    usage: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    if prompt_tokens or completion_tokens:
        usage["prompt_tokens_details"] = {"cached_tokens": cached_tokens}
    return usage


def stop_chunk(
    chunk_id: str,
    model: str,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cached_tokens: int = 0,
) -> dict[str, Any]:
    """Final chunk for a turn that ended normally -- finish_reason: stop."""
    chunk = _base_chunk(chunk_id, model)
    chunk["choices"] = [{"index": 0, "delta": {}, "finish_reason": "stop"}]
    chunk["usage"] = _usage_block(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_tokens=cached_tokens,
    )
    return chunk


def tool_calls_stop_chunk(
    chunk_id: str,
    model: str,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cached_tokens: int = 0,
) -> dict[str, Any]:
    """Terminal chunk for a turn that ends with a host-tool yield --
    finish_reason: tool_calls. This is the signal chat.js watches for to
    run the tool host-side and re-POST with the result."""
    chunk = _base_chunk(chunk_id, model)
    chunk["choices"] = [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]
    chunk["usage"] = _usage_block(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_tokens=cached_tokens,
    )
    return chunk


def sse_data(chunk: dict[str, Any]) -> str:
    return f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n"


def sse_done() -> str:
    return "data: [DONE]\n\n"


def sse_keepalive() -> str:
    return ": keepalive\n\n"


def sse_error(message: str) -> str:
    """Fatal, whole-turn error frame. Matches ``main.py``'s
    sidecar-unreachable error shape exactly (a top-level ``error`` field,
    no ``choices``), so chat.js's ``chunk.error`` branch -- checked BEFORE
    its ``!choice`` guard -- renders both transports' failures identically.
    """
    return (
        sse_data({"error": {"message": message, "type": "server_error"}}) + sse_done()
    )


def translate_event(
    event: dict[str, Any], chunk_id: str, model_id: str
) -> dict[str, Any] | None:
    """Kernel display event -> OpenAI SSE chunk dict, or ``None`` to drop.

    Ported subset of ``_event_translator.translate_event``: ``result/delta``,
    ``tool_calls/delta``, ``error``. Everything else (``thinking/*``,
    ``tool/started``, ``tool/completed``, ``result/final``, ``progress``,
    ``usage``) is dropped here -- ``usage`` is consumed separately by
    ``extract_usage`` for the terminal chunk.
    """
    event_type = event.get("type", "")

    if event_type == "result/delta":
        text = event.get("text", "")
        if isinstance(text, str) and text:
            return content_delta_chunk(chunk_id, model_id, text)
        return None

    if event_type == "tool_calls/delta":
        name = event.get("name", "")
        if not isinstance(name, str) or not name:
            return None
        tool_call_id = event.get("tool_call_id", "") or ""
        arguments = event.get("arguments", "{}") or "{}"
        try:
            index = int(event.get("index", 0) or 0)
        except (TypeError, ValueError):
            index = 0
        return tool_call_delta_chunk(
            chunk_id,
            model_id,
            index=index,
            tool_call_id=str(tool_call_id),
            name=name,
            arguments=str(arguments),
        )

    if event_type == "error":
        code = event.get("code", "")
        message = event.get("message", "Unknown error")
        text = f"\n\n[amplifier-agent error: {code} {message}]\n"
        return content_delta_chunk(chunk_id, model_id, text)

    return None


def extract_usage(event: dict[str, Any]) -> dict[str, Any] | None:
    """If ``event`` is a ``usage`` event, extract token counts in OpenAI
    shape; otherwise ``None``. See ``_event_translator.extract_usage`` for
    the full accounting of Anthropic's three-bucket cache token split --
    this port keeps the token math, drops the ``cost_usd`` extension (no
    consumer in chat.js)."""
    if event.get("type") != "usage":
        return None

    def _to_int(value: Any) -> int:
        try:
            return int(value) if value is not None else 0
        except (TypeError, ValueError):
            return 0

    new_input = _to_int(event.get("inputTokens"))
    cache_read = _to_int(event.get("cacheReadTokens"))
    cache_write = _to_int(event.get("cacheWriteTokens"))
    output = _to_int(event.get("outputTokens"))
    prompt_total = new_input + cache_read + cache_write
    return {
        "prompt_tokens": prompt_total,
        "completion_tokens": output,
        "cached_tokens": cache_read,
    }
