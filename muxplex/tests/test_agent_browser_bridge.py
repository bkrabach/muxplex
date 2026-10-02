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
