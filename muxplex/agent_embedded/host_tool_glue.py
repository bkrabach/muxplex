"""Host-tool yield glue -- ported into muxplex's own code.

Ported (not imported) from amplifier-agent's sidecar-oriented packages:

  * ``HostToolYield``       <- amplifier_agent_http/_host_tool_signal.py
  * ``HostToolProxy``       <- amplifier_agent_lib/bundle/host_tool_proxy.py
  * ``mount_host_tool_hook``<- amplifier_agent_lib/bundle/host_tool_hook.py

``HostToolProxy`` and the hook already lived in the (staying) *lib*
package; only the marker exception lived in *http* (the package slated
for deletion once the sidecar itself is retired -- a later pass, not this
one). Porting all three here means the embedded path has zero import
dependency on ``amplifier_agent_http`` for the one piece of behavior that
package's own eventual removal would otherwise break silently.

Mechanism, restated for this file's own docstring so it stands alone:

chat.js declares six tools on every turn (its ``TOOLS`` array) that
amplifier itself never executes -- the browser does, over its own
``/api/*`` fetch calls, under the user's ``muxplex_session`` cookie. When
the model picks one of these, the kernel calls the mounted ``Tool``'s
``execute()``, which has nothing real to run. ``HostToolProxy.execute()``
raises ``HostToolYield`` to escape the orchestrator loop cleanly.

``HostToolYield`` deliberately subclasses ``BaseException`` (not
``Exception``) so it slips past the kernel's own narrow ``except
Exception`` guards at the tool-dispatch and orchestrator-loop safety nets.
It does NOT, however, survive the ``AmplifierSession.execute()`` bridge:
that bridge collapses *any* exception crossing the Python<->Rust boundary
into a plain ``RuntimeError``, losing the original type. That is why the
caller (``runner.py``) cannot ``except HostToolYield`` at the call site --
it must instead read the ``yield_state`` side-channel dict this module's
hook writes into on ``tool:pre``, *before* the proxy raises.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from amplifier_core import ToolResult
from amplifier_core.models import HookResult

logger = logging.getLogger("muxplex.agent_embedded.host_tool_glue")

_TOOL_PRE_EVENT = "tool:pre"
_HOST_TOOL_CALL_EVENT_TYPE = "tool_calls/delta"


class HostToolYield(BaseException):
    """Raised by ``HostToolProxy.execute()`` to hand control back to the
    browser. See module docstring for why detection uses the yield_state
    side channel rather than ``except HostToolYield``."""

    def __init__(
        self, *, tool_call_id: str, name: str, arguments: dict[str, Any]
    ) -> None:
        super().__init__(f"host-tool yield: {name} (tool_call_id={tool_call_id})")
        self.tool_call_id = tool_call_id
        self.name = name
        self.arguments = arguments


class HostToolProxy:
    """Placeholder ``Tool`` for one browser-declared host tool.

    Constructed per-request from one entry of the wire's ``tools[]``
    (already unwrapped by ``message_shape.extract_host_tools``). Its
    ``execute()`` is unconditional: raise ``HostToolYield``. The wire-shape
    ``tool_calls/delta`` chunk itself is emitted by the hook below, from
    ``tool:pre`` -- BEFORE this proxy runs -- so the SSE stream already
    carries the tool call by the time the raise propagates.
    """

    def __init__(
        self, *, name: str, description: str, parameters: dict[str, Any]
    ) -> None:
        self._name = name
        self._description = description
        self._parameters = parameters

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def input_schema(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, input_data: Any) -> ToolResult:
        arguments: dict[str, Any]
        if isinstance(input_data, dict):
            arguments = input_data
        elif isinstance(input_data, str):
            try:
                arguments = json.loads(input_data) if input_data else {}
            except json.JSONDecodeError:
                arguments = {"_raw": input_data}
        else:
            arguments = {"_raw": str(input_data)}

        logger.debug("HostToolProxy.execute() raising HostToolYield for %r", self._name)
        raise HostToolYield(tool_call_id="", name=self._name, arguments=arguments)


async def mount_host_tool_hook(
    coordinator: Any, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Register the ``tool:pre`` hook that emits a ``tool_calls/delta``
    display event for each browser-declared host tool the model picks, and
    writes the ``yield_state`` side-channel dict the caller reads AFTER
    ``session.execute()`` returns (see ``HostToolYield``'s docstring).

    ``config["host_tools"]``: tool-name strings to treat as host-delegated.
    ``config["yield_state"]``: dict written to on yield -- keys ``yielded``
    (bool), ``tool_name`` (str), ``tool_call_id`` (str).
    """
    config = config or {}
    host_tools = frozenset(config.get("host_tools") or [])
    yield_state = config.get("yield_state")

    async def _on_tool_pre(event: str, data: dict[str, Any]) -> HookResult:
        tool_name = data.get("tool_name", "")
        if tool_name not in host_tools:
            return HookResult(action="continue")

        tool_call_id = data.get("tool_call_id", "") or ""
        tool_input = data.get("tool_input")
        if isinstance(tool_input, str):
            arguments_str = tool_input
        elif tool_input is None:
            arguments_str = "{}"
        else:
            try:
                arguments_str = json.dumps(tool_input, separators=(",", ":"))
            except (TypeError, ValueError):
                arguments_str = "{}"

        emit = (
            coordinator.get_capability("display.emit")
            if hasattr(coordinator, "get_capability")
            else None
        )
        if emit is None:
            logger.warning(
                "mount_host_tool_hook: no display.emit capability registered; "
                "tool_calls delta for %r will be missing on the wire",
                tool_name,
            )
        else:
            try:
                await emit(
                    {
                        "type": _HOST_TOOL_CALL_EVENT_TYPE,
                        "tool_call_id": tool_call_id,
                        "name": tool_name,
                        "arguments": arguments_str,
                        # Parallel index is always 0 -- matches the sidecar's
                        # own known limitation. chat.js's index:id keying
                        # (frontend/chat.js) already works around this; not
                        # "fixed" here -- out of scope for this pass.
                        "index": 0,
                    }
                )
            except Exception:
                logger.warning(
                    "mount_host_tool_hook: display.emit raised for %r",
                    tool_name,
                    exc_info=True,
                )

        if isinstance(yield_state, dict):
            yield_state["yielded"] = True
            yield_state["tool_name"] = tool_name
            yield_state["tool_call_id"] = tool_call_id

        return HookResult(action="continue")

    coordinator.hooks.register(
        event=_TOOL_PRE_EVENT,
        handler=_on_tool_pre,
        priority=50,
        name="host-tool-emit",
    )

    return {
        "name": "host-tool-hook",
        "version": "0.1.0",
        "host_tools_count": len(host_tools),
    }
