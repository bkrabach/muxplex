"""Correlation/auth/history rails; real SDK execution lives in integration tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from itsdangerous import TimestampSigner

from muxplex import main
from muxplex.agent_embedded import runner, wire
from muxplex.agent_embedded.bridge import (
    INPUT_TOOL,
    MAX_RESULT_BYTES,
    BrowserBridge,
    PendingCall,
    validate_result,
)
from muxplex.agent_embedded.errors import AgentRequestError
from muxplex.agent_embedded.message_shape import browser_protocol, validate_messages
from muxplex.agent_embedded.state import SessionLease


def result(**changes):
    return {
        "run_id": "run",
        "call_id": "call",
        "result_token": "capability",
        "outcome": "completed",
        "content": "ok",
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"outcome": []},
        {"outcome": {}},
        {"result_token": "not-ascii-\u00e9"},
        {"content": None},
        {"confirmed": 1},
        {"call_id": ""},
        {"outcome": "cancelled"},
    ],
)
def test_malformed_result_is_typed_400(changes):
    with pytest.raises(AgentRequestError) as exc:
        validate_result(result(**changes))
    assert exc.value.status == 400


def test_utf8_result_limit_counts_bytes_not_characters():
    with pytest.raises(AgentRequestError) as exc:
        validate_result(result(content="\u00e9" * MAX_RESULT_BYTES))
    assert exc.value.status == 413


@pytest.mark.parametrize(
    "body",
    [
        {"tools": []},
        {"tools": [{"type": "function", "function": {"name": "bash"}}]},
        {"messages": [{"role": "tool", "content": "effect"}]},
        {"messages": [{"role": "assistant", "tool_calls": [], "content": ""}]},
    ],
)
def test_legacy_tool_authority_is_refused_not_stripped(body):
    body = {"messages": [{"role": "user", "content": "hello"}], **body}
    with pytest.raises(AgentRequestError) as exc:
        validate_messages(body, browser=False)
    assert exc.value.code == "legacy_tool_history"
    assert "new conversation" in exc.value.remedy


@pytest.mark.parametrize(
    "protocol",
    [
        None,
        {},
        {"protocol": True, "browser_tools": True},
        {"protocol": 2, "browser_tools": True},
    ],
)
def test_unknown_browser_protocol_is_not_downgraded(protocol):
    with pytest.raises(AgentRequestError):
        browser_protocol({"muxplex_agent": protocol})


def test_browser_transcript_and_unbounded_focus_are_refused():
    with pytest.raises(AgentRequestError) as history:
        validate_messages(
            {
                "messages": [
                    {"role": "assistant", "content": "old"},
                    {"role": "user", "content": "new"},
                ]
            },
            browser=True,
        )
    assert history.value.code == "browser_history"
    with pytest.raises(AgentRequestError) as context:
        validate_messages(
            {
                "messages": [{"role": "user", "content": "new"}],
                "context": "x" * 1025,
            },
            browser=True,
        )
    assert context.value.code == "invalid_context"


async def test_future_is_registered_before_emission_and_resolves_same_handler():
    sdk = pytest.importorskip("amplifier_agent")
    queue = asyncio.Queue()
    bridge = BrowserBridge("owner", "session", "run", queue)
    task = asyncio.create_task(
        bridge.invoke("list_muxplex_sessions", {}, sdk.ToolContext("call"))
    )
    event = await asyncio.wait_for(queue.get(), 1)
    cap = event["muxplex_browser_tool"]
    assert bridge.pending[cap["call_id"]].future.done() is False
    payload = result(result_token=cap["result_token"])
    for changed, owner in [({"result_token": "wrong"}, "owner"), ({}, "other-owner")]:
        with pytest.raises(AgentRequestError) as refused:
            bridge.resolve({**payload, **changed}, owner)
        assert refused.value.status == 403
    bridge.resolve(payload, "owner")
    with pytest.raises(AgentRequestError) as duplicate:
        bridge.resolve(payload, "owner")
    assert duplicate.value.status == 409
    assert await task == "ok"


@pytest.mark.parametrize("outcome", ["failed", "unknown"])
async def test_typing_denial_or_uncertainty_prevents_second_emission(outcome):
    sdk = pytest.importorskip("amplifier_agent")
    queue = asyncio.Queue()
    bridge = BrowserBridge("owner", "session", "run", queue)
    task = asyncio.create_task(bridge.invoke(INPUT_TOOL, {}, sdk.ToolContext("call")))
    cap = (await asyncio.wait_for(queue.get(), 1))["muxplex_browser_tool"]
    payload = result(result_token=cap["result_token"], confirmed=False)
    with pytest.raises(AgentRequestError) as unconfirmed:
        bridge.resolve(payload, "owner")
    assert unconfirmed.value.code == "confirmation_required"
    bridge.resolve({**payload, "outcome": outcome, "error": "declined"}, "owner")
    with pytest.raises((sdk.ToolFailed, sdk.ToolOutcomeUnknown)):
        await task
    with pytest.raises((sdk.ToolFailed, sdk.ToolOutcomeUnknown)):
        await bridge.invoke(INPUT_TOOL, {}, sdk.ToolContext("second"))
    assert queue.empty()


async def test_closed_bridge_and_deadlines_never_retry_unknown_effects():
    sdk = pytest.importorskip("amplifier_agent")
    queue = asyncio.Queue()
    bridge = BrowserBridge("owner", "session", "run", queue)
    task = asyncio.create_task(
        bridge.invoke(
            INPUT_TOOL,
            {},
            sdk.ToolContext("call", datetime.now(UTC) + timedelta(milliseconds=25)),
        )
    )
    cap = (await asyncio.wait_for(queue.get(), 1))["muxplex_browser_tool"]
    with pytest.raises(sdk.ToolOutcomeUnknown):
        await task
    with pytest.raises(AgentRequestError) as stale:
        bridge.resolve(result(result_token=cap["result_token"]), "owner")
    assert stale.value.status == 410
    bridge.close()
    assert bridge.pending["call"].future.done()


async def test_operator_cookie_is_required_even_for_loopback():
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "muxplex_agent": {"protocol": 1, "browser_tools": True},
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(main.app), base_url="http://127.0.0.1"
    ) as client:
        chat = await client.post("/api/agent/chat/completions", json=body)
        reply = await client.post("/api/agent/browser-tool-results", json=result())
    assert chat.status_code == reply.status_code == 403
    assert chat.json()["error"]["code"] == "operator_cookie_required"


async def test_deeply_nested_json_is_a_typed_400():
    cookie = TimestampSigner(main._auth_secret).sign("fixture-owner").decode()
    nested = '{"content":' + "[" * 1500 + "0" + "]" * 1500 + "}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(main.app),
        base_url="http://127.0.0.1",
        cookies={"muxplex_session": cookie},
    ) as client:
        reply = await client.post(
            "/api/agent/browser-tool-results",
            content=nested,
            headers={"content-type": "application/json"},
        )
    assert reply.status_code == 400 and reply.json()["error"]["code"] == "invalid_json"


async def test_bearer_only_caller_cannot_create_browser_capability_or_submit_result(
    monkeypatch,
):
    # Loopback retains the existing shared auth bypass; the callback gate still
    # cannot mistake a Bearer header for a verified browser cookie.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(main.app),
        base_url="http://127.0.0.1",
        headers={"authorization": "Bearer fixture-federation-key"},
    ) as client:
        reply = await client.post("/api/agent/browser-tool-results", json=result())
    assert reply.status_code == 403


async def test_result_endpoint_status_order_owner_token_duplicate_oversized_stale():
    cookie = TimestampSigner(main._auth_secret).sign("fixture-owner-a").decode()
    other_cookie = TimestampSigner(main._auth_secret).sign("fixture-owner-b").decode()
    owner = hashlib.sha256(cookie.encode()).hexdigest()
    run = runner.LiveRun(
        owner=owner, session_id="a" * 32, model=runner.default_model(), browser=True
    )
    future = asyncio.get_running_loop().create_future()
    run.bridge.pending["call"] = PendingCall(
        "list_muxplex_sessions",
        "capability",
        datetime.now(UTC) + timedelta(seconds=30),
        future,
    )
    runner._runs[run.run_id] = run
    payload = result(run_id=run.run_id)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(main.app),
            base_url="http://127.0.0.1",
            cookies={"muxplex_session": cookie},
        ) as client:
            bad_token = await client.post(
                "/api/agent/browser-tool-results",
                json={**payload, "result_token": "wrong"},
            )
            assert bad_token.status_code == 403
            client.cookies.set("muxplex_session", other_cookie)
            wrong_owner = await client.post(
                "/api/agent/browser-tool-results", json=payload
            )
            assert wrong_owner.status_code == 403
            client.cookies.set("muxplex_session", cookie)
            malformed = await client.post(
                "/api/agent/browser-tool-results", json={**payload, "outcome": []}
            )
            assert malformed.status_code == 400
            too_large = await client.post(
                "/api/agent/browser-tool-results",
                json={**payload, "content": "x" * (MAX_RESULT_BYTES + 1)},
            )
            assert too_large.status_code == 413
            accepted = await client.post(
                "/api/agent/browser-tool-results", json=payload
            )
            assert accepted.status_code == 200 and future.done()
            duplicate = await client.post(
                "/api/agent/browser-tool-results", json=payload
            )
            assert duplicate.status_code == 409
            await run.close()
            stale = await client.post("/api/agent/browser-tool-results", json=payload)
            assert stale.status_code == 410
    finally:
        await run.close()


def test_owner_metadata_lock_interruption_and_public_history_reconciliation(tmp_path):
    sid = "a" * 32
    lease = SessionLease(tmp_path, sid, "owner", new=True)
    try:
        with pytest.raises(AgentRequestError) as busy:
            SessionLease(tmp_path, sid, "owner", new=False)
        assert busy.value.status == 409
        lease.mark("run", "turn")
    finally:
        lease.close()
    with pytest.raises(AgentRequestError) as wrong:
        SessionLease(tmp_path, sid, "other", new=False)
    assert wrong.value.status == 403
    resumed = SessionLease(tmp_path, sid, "owner", new=False)
    try:
        with pytest.raises(AgentRequestError) as interrupted:
            resumed.reconcile([])
        assert interrupted.value.code == "session_interrupted"
        assert (
            json.loads((tmp_path / f"{sid}.json").read_text())["run"]["turn_id"]
            == "turn"
        )
        resumed.reconcile(
            [SimpleNamespace(turn_id="turn", result=SimpleNamespace(state="success"))]
        )
        assert resumed.data["run"] is None
        assert (tmp_path / f"{sid}.json").stat().st_mode & 0o777 == 0o600
    finally:
        resumed.close()


def test_usage_is_replacement_projection_and_unknown_is_not_zero():
    snapshot = SimpleNamespace(
        entries=[
            SimpleNamespace(
                tokens_in=40,
                tokens_out=4,
                cache_read_tokens=6,
                cache_write_tokens=10,
            )
        ]
    )
    assert wire.usage_block(snapshot)["prompt_tokens"] == 50
    snapshot.entries[0].tokens_out = None
    assert wire.usage_block(snapshot)["completion_tokens"] is None
    assert wire.usage_block(snapshot)["total_tokens"] is None
    assert "usage" not in wire.stop_chunk("chunk", "model")


async def test_cleanup_budget_keeps_slow_handles_and_owner_lease_quarantined(
    monkeypatch, tmp_path
):
    # This tests the cleanup supervisor itself, not SDK integration. The real
    # SDK cancellation/drain is covered by test_agent_sdk_integration.py.
    run = runner.LiveRun(
        owner="owner", session_id="b" * 32, model="model", browser=True
    )
    lease = SessionLease(tmp_path, run.session_id, "owner", new=True)
    lease.mark(run.run_id, "turn")
    run.lease = lease
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_cancel():
        entered.set()
        await release.wait()

    run.turn = SimpleNamespace(cancel=slow_cancel)
    runner._runs[run.run_id] = run
    monkeypatch.setattr(runner, "_DRAIN_SECONDS", 0.025)
    try:
        await run.close()
        assert entered.is_set() and run.closing and not run.closed
        assert run.run_id in runner._runs and lease.fd >= 0
        assert run.bridge.closed
        with pytest.raises(AgentRequestError) as busy:
            SessionLease(tmp_path, run.session_id, "owner", new=False)
        assert busy.value.status == 409
    finally:
        release.set()
        await asyncio.wait_for(run.cleanup_task, 1)
    assert run.closed and lease.fd == -1 and run.run_id not in runner._runs
    assert json.loads(lease.path.read_text())["run"]["turn_id"] == "turn"


async def test_shutdown_during_preparation_owns_late_handles(monkeypatch, tmp_path):
    run = runner.LiveRun(
        owner="owner", session_id="c" * 32, model="model", browser=True
    )
    run.prepared.clear()
    run.lease = SessionLease(tmp_path, run.session_id, "owner", new=True)
    runner._runs[run.run_id] = run
    monkeypatch.setattr(runner, "_DRAIN_SECONDS", 0.025)
    closed = []

    async def close_late_handle():
        closed.append("closed")

    try:
        await run.close()
        assert not run.closed and run.lease.fd >= 0
        run.agent = SimpleNamespace(close=close_late_handle)
        run.prepared.set()
        await asyncio.wait_for(run.cleanup_task, 1)
    finally:
        run.prepared.set()
        await run.close()
    assert closed == ["closed"] and run.closed and run.lease.fd == -1


async def test_definitive_image_refusal_after_effect_allows_corrected_resume(
    monkeypatch, tmp_path
):
    # Unit admission seam: only public SDK records/operations are used here.
    # Real engine/provider execution remains the manager's integration suite.
    sdk = pytest.importorskip("amplifier_agent")
    history = []
    effects = []
    accepted_inputs = []
    closed_handles = []

    class Session:
        def __init__(self, sid, options):
            self.sid, self.options = sid, options
            self.history = list(history)

        async def start_turn(self, value):
            if any(isinstance(part, sdk.ImagePart) for part in value.content):
                raise sdk.AgentError(
                    "image_unsupported", "input", "Images refused.", "Send text only."
                )
            accepted_inputs.append(value)
            return Turn(self, value, len(history) == 0)

        async def close(self):
            closed_handles.append("session")

    class Turn:
        def __init__(self, session, value, first):
            self.session, self.value, self.first = session, value, first
            self.info = sdk.TurnInfo(session.sid, f"turn-{len(history) + 1}")

        async def events(self):
            if self.first:
                tool = next(
                    tool
                    for tool in self.session.options.tools
                    if tool.name == INPUT_TOOL
                )
                await tool.handler(
                    {"session_name": "fixture", "text": "effect", "enter": False},
                    sdk.ToolContext("first-call"),
                )
            terminal = sdk.TurnResult("success", content=[sdk.TextPart("Reply")])
            history.append(sdk.TurnRecord(self.info.turn_id, self.value, terminal))
            yield sdk.Event(
                "turn-events/1",
                self.info.session_id,
                self.info.turn_id,
                1,
                "terminal",
                terminal,
            )

    class Agent:
        def __init__(self, options):
            self.options = options

        async def create_session(self, options):
            assert options.persistence == "durable"
            return Session(options.session_id, self.options)

        async def resume_session(self, sid):
            return Session(sid, self.options)

        async def close(self):
            closed_handles.append("agent")

    async def create(options):
        return Agent(options)

    monkeypatch.setattr(
        runner.credentials, "credential_home", lambda: tmp_path, raising=False
    )
    monkeypatch.setattr(
        runner.credentials, "create_agent_with_credentials", create, raising=False
    )
    body = {
        "messages": [{"role": "user", "content": "First effect"}],
        "muxplex_agent": {"protocol": 1, "browser_tools": True},
    }
    first = await runner.prepare_chat(body, owner="owner")
    async with asyncio.timeout(2):
        async for chunk in first.stream():
            payload = json.loads(chunk[6:]) if chunk != wire.sse_done() else {}
            if "muxplex_browser_tool" in payload:
                cap = payload["muxplex_browser_tool"]
                effects.append(cap["call_id"])
                runner.submit_browser_result(
                    {
                        **{
                            key: cap[key]
                            for key in ("run_id", "call_id", "result_token")
                        },
                        "outcome": "completed",
                        "content": "Recorded",
                        "confirmed": True,
                    },
                    owner="owner",
                )
    assert first.terminal.state == "success" and effects == ["first-call"]
    resume = {
        **body,
        "muxplex_agent": {**body["muxplex_agent"], "session_id": first.session_id},
    }
    image = {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,aW1hZ2U="},
    }
    with pytest.raises(sdk.AgentError) as refused:
        await runner.prepare_chat(
            {**resume, "messages": [{"role": "user", "content": [image]}]},
            owner="owner",
        )
    assert refused.value.code == "image_unsupported"
    assert len(history) == 1 and len(accepted_inputs) == 1
    marker = tmp_path / "muxplex-browser-state" / f"{first.session_id}.json"
    assert json.loads(marker.read_text())["run"] is None
    assert not runner._runs
    corrected = await runner.prepare_chat(
        {**resume, "messages": [{"role": "user", "content": "Corrected text"}]},
        owner="owner",
    )
    assert len(corrected.session.history) == 1
    output = [chunk async for chunk in corrected.stream()]
    assert output[-1] == wire.sse_done()
    assert not any(b"muxplex_browser_tool" in chunk for chunk in output)
    assert effects == ["first-call"] and len(history) == 2
    assert len(accepted_inputs) == 2 and closed_handles == ["session", "agent"] * 3


@pytest.mark.parametrize("failure", ["cancelled", "untyped", "internal_failed"])
async def test_uncertain_admission_retains_provisional_interruption_marker(
    monkeypatch, tmp_path, failure
):
    sdk = pytest.importorskip("amplifier_agent")
    error = {
        "cancelled": asyncio.CancelledError(),
        "untyped": RuntimeError("Admission interrupted"),
        "internal_failed": sdk.AgentError(
            "internal_failed", "internal", "Unknown admission state.", "Start fresh."
        ),
    }[failure]

    async def start_turn(value):
        raise error

    async def close():
        pass

    async def create_session(options):
        return SimpleNamespace(start_turn=start_turn, close=close)

    async def create(options):
        return SimpleNamespace(create_session=create_session, close=close)

    monkeypatch.setattr(
        runner.credentials, "credential_home", lambda: tmp_path, raising=False
    )
    monkeypatch.setattr(
        runner.credentials, "create_agent_with_credentials", create, raising=False
    )
    with pytest.raises(type(error)):
        await runner.prepare_chat(
            {
                "messages": [{"role": "user", "content": "Hello"}],
                "muxplex_agent": {"protocol": 1, "browser_tools": True},
            },
            owner="owner",
        )
    (path,) = (tmp_path / "muxplex-browser-state").glob("*.json")
    assert json.loads(path.read_text())["run"]["turn_id"] is None
    lease = SessionLease(path.parent, path.stem, "owner", new=False)
    try:
        with pytest.raises(AgentRequestError) as interrupted:
            lease.reconcile([])
        assert interrupted.value.code == "session_interrupted"
    finally:
        lease.close()
    assert not runner._runs


@pytest.mark.parametrize(
    "state", ["failure", "cancelled", "rejected", "incomplete", "pump_error"]
)
@pytest.mark.parametrize("terminal_usage", [False, True])
async def test_unsuccessful_stream_projects_last_usage_once_without_success(
    tmp_path, state, terminal_usage
):
    sdk = pytest.importorskip("amplifier_agent")

    def usage(tokens_in, tokens_out):
        return sdk.Usage(
            [
                sdk.UsageEntry(
                    "anthropic",
                    "model",
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cache_read_tokens=3,
                    cache_write_tokens=5,
                )
            ]
        )

    run = runner.LiveRun(
        owner="owner", session_id="d" * 32, model="model", browser=True
    )
    run.lease = SessionLease(tmp_path, run.session_id, "owner", new=True)
    run.lease.mark(run.run_id, "accepted-turn")
    for snapshot in [usage(10, 1), usage(20, None)]:
        run.queue.put_nowait(
            SimpleNamespace(type="usage", payload=sdk.UsageEvent(snapshot))
        )
    error = sdk.AgentError(
        "image_unsupported", "input", "Measured turn failed.", "Choose another model."
    )
    if state == "pump_error":
        run.pump_error = error
    elif state != "incomplete":
        run.terminal = sdk.TurnResult(
            state, error=error, usage=usage(40, 4) if terminal_usage else None
        )
    run.queue.put_nowait(None)
    output = [chunk async for chunk in run.stream()]
    payloads = [json.loads(chunk[6:]) for chunk in output]
    projected = [payload for payload in payloads if "usage" in payload]
    assert len(projected) == 1 and "error" in projected[0]
    final = terminal_usage and state not in {"incomplete", "pump_error"}
    assert projected[0]["usage"] == {
        "prompt_tokens": 45 if final else 25,
        "completion_tokens": 4 if final else None,
        "total_tokens": 49 if final else None,
        "prompt_tokens_details": {"cached_tokens": 3},
    }
    assert projected[0]["error"]["code"] == (
        "incomplete_turn" if state == "incomplete" else "image_unsupported"
    )
    assert wire.sse_done() not in output
    assert not any(
        choice.get("finish_reason") == "stop"
        for payload in payloads
        for choice in payload.get("choices", [])
    )
    # An admitted turn with the SAME image_unsupported code must not erase its
    # marker: only a definitive refusal from start_turn is safe to clear.
    assert json.loads(run.lease.path.read_text())["run"]["turn_id"] == "accepted-turn"


async def test_unsuccessful_stream_without_usage_does_not_invent_zero_counts():
    run = runner.LiveRun(owner="", session_id="e" * 32, model="model", browser=False)
    run.terminal = SimpleNamespace(state="cancelled", error=None, usage=None)
    run.queue.put_nowait(None)
    output = [chunk async for chunk in run.stream()]
    payloads = [json.loads(chunk[6:]) for chunk in output]
    assert payloads[-1]["error"]["code"] == "turn_cancelled"
    assert not any("usage" in payload for payload in payloads)
    assert wire.sse_done() not in output
