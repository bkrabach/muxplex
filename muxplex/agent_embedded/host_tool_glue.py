# pyright: reportMissingImports=false
"""The five server-owned browser tools, copied from chat.js's existing catalog.

Only these callbacks are offered. SDK tool_call events are observations, not
another dispatch path. No shell/filesystem tool, skill, or MCP authority.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_STRING = {"type": "string"}
_SESSION = {"type": "string", "description": "Exact tmux session name to make active."}
_KEYS = [
    "Enter",
    "Escape",
    "Tab",
    "C-c",
    "C-d",
    "Up",
    "Down",
    "Left",
    "Right",
    "PageUp",
    "PageDown",
]


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    result = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        result["required"] = required
    return result


TOOL_SPECS = [
    {
        "name": "list_muxplex_sessions",
        "description": (
            "List tmux sessions across the WHOLE federation -- this device "
            "plus any configured peer devices reachable over the federation "
            "link, not just this one (name, last activity, current working "
            "directory). EVERY entry is tagged with deviceId/deviceName so "
            "you always know which physical machine a session is on -- a "
            "session never has to be assumed local. remoteId is null for a "
            "session on THIS device, and the peer's device id otherwise. A "
            "peer that's offline or misconfigured shows up as its own status "
            'entry (e.g. status: "unreachable"/"auth_failed") instead of '
            "session data -- report that plainly rather than treating it as a "
            'tool failure. This is the ONE tool for "what sessions do I '
            'have"/"what\'s running" style questions, local or fleet-wide -- '
            "always call this first, never assume there is a separate "
            "local-only listing to reach for."
        ),
        "input_schema": _schema({}),
    },
    {
        "name": "get_muxplex_session_details",
        "description": (
            "Get one specific tmux session's recent pane content (its actual "
            "captured terminal output/scrollback) plus metadata (last activity, "
            "created time, working directory, pending follow-ups). Use this "
            "whenever the user asks what's happening/showing/printing/running "
            "INSIDE a named session, or wants to see its output or logs -- "
            "works for a session on THIS device or on any federated peer "
            "device, transparently. If you don't already know the exact "
            "session name, call list_muxplex_sessions first to look it up -- "
            "don't guess it. You normally do NOT need to pass device_id: this "
            "tool finds the right device on its own. Only pass device_id "
            "(the deviceId tag list_muxplex_sessions showed you) when you "
            "already know the session lives on a specific peer, or after a "
            "prior call came back reporting the same session name exists on "
            "more than one device and asking you to disambiguate -- never "
            "guess between them."
        ),
        "input_schema": _schema(
            {
                "session_name": {
                    **_STRING,
                    "description": "Exact tmux session name, e.g. one returned by list_muxplex_sessions.",
                },
                "lines": {
                    "type": "integer",
                    "description": "How many lines of recent pane scrollback to return (1-2000). Omit to use the server's default window.",
                },
                "device_id": {
                    **_STRING,
                    "description": "Optional. The deviceId this session lives on, from list_muxplex_sessions' deviceId/remoteId tags. Omit for the common case -- the tool locates the session automatically. Only needed to disambiguate when the same session name exists on more than one federated device.",
                },
            },
            ["session_name"],
        ),
    },
    {
        "name": "switch_muxplex_session",
        "description": (
            "Switch the dashboard's active tmux session -- the same effect as "
            "the user clicking that session's tile to open it (ensures a live "
            "terminal exists for it, then makes it the focused/active "
            "session). Use when the user asks to switch to, open, focus, or "
            "go to a named session. LOCAL-ONLY: only works for a session on "
            "THIS device (unlike list_muxplex_sessions and "
            "get_muxplex_session_details, this does not proxy to federated "
            "peers yet) -- if list_muxplex_sessions showed the session on a "
            "different device (remoteId set), tell the user it must be opened "
            "from that device's own dashboard rather than calling this tool. "
            "If you don't already know the exact session name, call "
            "list_muxplex_sessions first -- don't guess it."
        ),
        "input_schema": _schema({"session_name": _SESSION}, ["session_name"]),
    },
    {
        "name": "switch_muxplex_view",
        "description": (
            "Change which view filter of sessions is currently active in the "
            "dashboard -- the same effect as picking a view from the view "
            "dropdown/sidebar. 'all' shows every visible session; 'hidden' "
            "shows sessions the user has hidden; any other name must be one "
            "of the user's own configured views. An invalid name is rejected "
            "with an error naming the exact current valid list -- retry with "
            "one of those. Use when the user asks to switch/change/filter the "
            "view."
        ),
        "input_schema": _schema(
            {
                "view": {
                    **_STRING,
                    "description": 'View name to activate, e.g. "all", "hidden", or a configured view name.',
                },
            },
            ["view"],
        ),
    },
    {
        "name": "send_muxplex_session_input",
        "description": (
            "Type text and/or special keys into a tmux session's terminal, "
            "exactly as if the user had typed it at the keyboard -- this "
            "actually runs commands (remote code execution by design). It is "
            "OFF by default: the muxplex operator must explicitly enable it "
            "(settings.input_enabled) AND allow-list the specific session "
            "(settings.input_allowed_sessions) on the server -- a setting only "
            "changeable by editing a file on disk, never through this or any "
            "API call. If either is not set for the target session, this call "
            "fails with the server's real 403 error. Never retry it and never "
            "imply you can work around it -- but the error text itself tells "
            "the user how a local operator unblocks it, so relay that instead "
            'of dead-ending on "not possible". Separately, and even when '
            "enabled server-side: EVERY call to this tool pauses for an "
            "explicit human confirmation click in the browser before anything "
            "is sent -- there is no way to skip, pre-approve, or batch-approve "
            "this, including within one turn or across repeated calls. If the "
            "human declines, the call returns a decline error; do not retry "
            "the same request in this turn -- tell the user it was declined. "
            "Use only when the user explicitly asks you to type/run/send/press "
            "something into a named session."
        ),
        "input_schema": _schema(
            {
                "session_name": {
                    **_STRING,
                    "description": "Exact tmux session name to type into.",
                },
                "text": {
                    **_STRING,
                    "description": "Literal text to type (sent as literal characters, never shell-interpreted).",
                },
                "enter": {
                    "type": "boolean",
                    "description": "Press Enter after the text, submitting the line. Defaults to true.",
                },
                "keys": {
                    "type": "array",
                    "items": {"type": "string", "enum": _KEYS},
                    "description": 'Named special keys to send, in order, after text (e.g. ["C-c"] to interrupt).',
                },
            },
            ["session_name"],
        ),
    },
]


def browser_tools(bridge: Any) -> list[Any]:
    from amplifier_agent import Tool

    tools = []
    for spec in TOOL_SPECS:
        # A closure per tool, not a late-bound loop variable.
        def handler_for(name: str):
            async def invoke(arguments, context):
                return await bridge.invoke(name, arguments, context)

            return invoke

        tools.append(
            Tool(
                name=spec["name"],
                description=spec["description"],
                input_schema=deepcopy(spec["input_schema"]),
                handler=handler_for(spec["name"]),
            )
        )
    return tools
