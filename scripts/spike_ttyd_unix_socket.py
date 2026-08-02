#!/usr/bin/env python3
"""Spike Q1 -- does ttyd complete a REAL WebSocket upgrade over a UNIX socket?

Platform-verification probe for the per-session-ttyd architecture. Answers:
"is a ttyd bound to a UNIX domain socket a genuine RFC 6455 endpoint, or just
an HTTP server that happens to answer?"

Deliberately avoids the `websockets` library: this is an independent,
from-first-principles check. We hand-build the upgrade request over a raw
`AF_UNIX` socket, compute the expected `Sec-WebSocket-Accept` ourselves, and
verify the server's reply matches. If this passes, the transport is real --
not an artifact of a client library being forgiving.

Usage:
    python3 scripts/spike_ttyd_unix_socket.py            # spawns its own ttyd
    python3 scripts/spike_ttyd_unix_socket.py <sock>     # probe an existing one

Exit status: 0 if both the HTTP index and the WS upgrade check out.
Verified on Linux (ttyd 1.7.4) and macOS 26.6 arm64 (ttyd 1.7.7).
"""

import base64
import hashlib
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spike_ttyd_harness import ttyd_session  # noqa: E402

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def compute_accept(key: str) -> str:
    sha1 = hashlib.sha1((key + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(sha1).decode("ascii")


def test_http_index(sock_path: str) -> tuple[bool, bool]:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(sock_path)
    req = "GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
    s.sendall(req.encode())
    data = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        data += chunk
    s.close()
    status_line = data.split(b"\r\n", 1)[0].decode(errors="replace")
    print(f"[HTTP index] status line: {status_line!r}")
    lowered = data.lower()
    is_html = b"<html" in lowered or b"ttyd" in lowered or b"xterm" in lowered
    print(f"[HTTP index] looks like ttyd HTML index: {is_html}")
    return status_line.startswith("HTTP/1.1 200"), is_html


def test_ws_upgrade(sock_path: str) -> bool:
    key_bytes = b"spike-handshake-key-000001=="[:16]  # arbitrary 16 bytes
    ws_key = base64.b64encode(key_bytes).decode("ascii")
    expected_accept = compute_accept(ws_key)

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(sock_path)
    req = (
        "GET /ws HTTP/1.1\r\n"
        "Host: localhost\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {ws_key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "Sec-WebSocket-Protocol: tty\r\n"
        "\r\n"
    )
    s.sendall(req.encode())

    # Read until we have the full header block (terminated by a blank line)
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = s.recv(4096)
        if not chunk:
            break
        data += chunk
    s.close()

    header_block = data.split(b"\r\n\r\n", 1)[0].decode(errors="replace")
    lines = header_block.split("\r\n")
    status_line = lines[0]
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()

    print(f"[WS upgrade] status line: {status_line!r}")
    print(f"[WS upgrade] headers: {headers}")

    is_101 = status_line.startswith("HTTP/1.1 101")
    accept_header = headers.get("sec-websocket-accept", "")
    accept_matches = accept_header == expected_accept
    protocol_ack = headers.get("sec-websocket-protocol", "")

    print(f"[WS upgrade] expected Sec-WebSocket-Accept: {expected_accept}")
    print(f"[WS upgrade] server's Sec-WebSocket-Accept:  {accept_header}")
    print(f"[WS upgrade] RFC6455 accept hash matches: {accept_matches}")
    print(f"[WS upgrade] negotiated subprotocol: {protocol_ack!r} (expect 'tty')")

    return is_101 and accept_matches and protocol_ack == "tty"


def probe(sock_path: str) -> int:
    http_ok, looks_html = test_http_index(sock_path)
    print()
    ws_ok = test_ws_upgrade(sock_path)
    print()
    print(f"RESULT: HTTP index OK={http_ok} html={looks_html} | WS upgrade OK={ws_ok}")
    return 0 if (http_ok and ws_ok) else 1


def main() -> int:
    if len(sys.argv) > 1:
        return probe(sys.argv[1])
    with ttyd_session("q1") as sock:
        print(f"[harness] ttyd on {sock}\n")
        return probe(str(sock))


if __name__ == "__main__":
    sys.exit(main())
