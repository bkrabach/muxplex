"""Container-local Anthropic HTTP/SSE fixture for the REAL tagged SDK.

No SDK/factory/engine patching. An OS-allocated loopback socket is owned by the
context and all connections/tasks are closed at exit. Never use on the host:
the caller must explicitly opt in inside a DTU.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_ARGUMENTS = {
    "list_muxplex_sessions": {},
    "get_muxplex_session_details": {"session_name": "fixture-session", "lines": 20},
    "switch_muxplex_session": {"session_name": "fixture-session"},
    "switch_muxplex_view": {"view": "all"},
    "send_muxplex_session_input": {
        "session_name": "fixture-session",
        "text": "fixture",
        "enter": False,
    },
}


def require_container() -> None:
    assert os.environ.get("MUXPLEX_RUN_SDK_TESTS") == "1", (
        "Manager must explicitly set MUXPLEX_RUN_SDK_TESTS=1 inside the combined DTU."
    )
    systemd_marker = Path("/run/systemd/container")
    lxc = systemd_marker.is_file() and systemd_marker.read_text().strip() == "lxc"
    assert Path("/.dockerenv").exists() or Path("/run/.containerenv").exists() or lxc, (
        "SDK/provider socket tests are container-only; do not run them on the host."
    )


def frames(model: str, tool: str | None, ordinal: int):
    yield {
        "type": "message_start",
        "message": {
            "id": f"msg_fixture_{ordinal}",
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": model,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": 17,
                "output_tokens": 0,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 5,
            },
        },
    }
    block = (
        {"type": "tool_use", "id": f"call_fixture_{ordinal}", "name": tool, "input": {}}
        if tool
        else {"type": "text", "text": ""}
    )
    yield {"type": "content_block_start", "index": 0, "content_block": block}
    if tool:
        yield {
            "type": "content_block_delta",
            "index": 0,
            "delta": {
                "type": "input_json_delta",
                "partial_json": json.dumps(_ARGUMENTS[tool]),
            },
        }
    else:
        for text in ("Wire ", "reply"):
            yield {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            }
    yield {"type": "content_block_stop", "index": 0}
    yield {
        "type": "message_delta",
        "delta": {
            "stop_reason": "tool_use" if tool else "end_turn",
            "stop_sequence": None,
        },
        "usage": {"output_tokens": 2},
    }
    yield {"type": "message_stop"}


@dataclass
class ProviderFixture:
    plan: list[str | None] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    release: asyncio.Event | None = None
    failure: int | None = None
    partial_eof: bool = False
    connections: set[asyncio.StreamWriter] = field(default_factory=set)
    tasks: set[asyncio.Task] = field(default_factory=set)
    errors: list[Exception] = field(default_factory=list)

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        task = asyncio.current_task()
        self.tasks.add(task)
        self.connections.add(writer)
        try:
            header = (await reader.readuntil(b"\r\n\r\n")).decode("ascii")
            method, path, _version = header.splitlines()[0].split(" ")
            headers = dict(
                line.split(": ", 1) for line in header.splitlines()[1:] if ": " in line
            )
            headers = {key.lower(): value for key, value in headers.items()}
            assert headers.get("x-api-key") == "fixture-api-key"
            # The tagged engine's pinned provider fetches metadata and counts
            # tokens before some requests. They are not model completions and
            # must not consume the scripted response ordinals.
            if method == "GET" and path.startswith("/v1/models"):
                model = {
                    "id": "claude-sonnet-5",
                    "type": "model",
                    "display_name": "Fixture model",
                    "created_at": "2026-01-01T00:00:00Z",
                }
                value = (
                    {
                        "data": [model],
                        "has_more": False,
                        "first_id": model["id"],
                        "last_id": model["id"],
                    }
                    if path.split("?")[0] == "/v1/models"
                    else model
                )
                encoded = json.dumps(value).encode()
                writer.write(
                    f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(encoded)}\r\nConnection: close\r\n\r\n".encode()
                    + encoded
                )
                await writer.drain()
                return
            body = json.loads(await reader.readexactly(int(headers["content-length"])))
            if method == "POST" and path == "/v1/messages/count_tokens":
                encoded = json.dumps({"input_tokens": 20}).encode()
                writer.write(
                    f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(encoded)}\r\nConnection: close\r\n\r\n".encode()
                    + encoded
                )
                await writer.drain()
                return
            assert method == "POST" and path == "/v1/messages"
            self.requests.append(body)
            ordinal = len(self.requests)
            if self.failure:
                encoded = json.dumps(
                    {
                        "error": {
                            "type": "api_error",
                            "message": "Fixture provider failure",
                        }
                    }
                ).encode()
                writer.write(
                    f"HTTP/1.1 {self.failure} Fixture\r\nContent-Type: application/json\r\nContent-Length: {len(encoded)}\r\nConnection: close\r\n\r\n".encode()
                    + encoded
                )
                await writer.drain()
                return
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
            )
            tool = self.plan[ordinal - 1] if ordinal <= len(self.plan) else None
            for frame in frames(body["model"], tool, ordinal):
                encoded = (
                    "event: " + frame["type"] + "\ndata: " + json.dumps(frame) + "\n\n"
                ).encode()
                writer.write(f"{len(encoded):x}\r\n".encode() + encoded + b"\r\n")
                await writer.drain()
                if frame.get("delta", {}).get("text") == "Wire ":
                    if self.partial_eof:
                        return
                    if self.release is not None:
                        await self.release.wait()
            writer.write(b"0\r\n\r\n")
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass  # expected during SDK cancellation
        except Exception as exc:
            self.errors.append(exc)
        finally:
            self.connections.discard(writer)
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
            self.tasks.discard(task)

    @contextlib.asynccontextmanager
    async def running(self):
        require_container()
        server = await asyncio.start_server(self.serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            if self.release is not None:
                self.release.set()
            server.close()
            await server.wait_closed()
            for writer in list(self.connections):
                writer.close()
            for task in list(self.tasks):
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            assert not self.errors, "Provider fixture protocol failed."
