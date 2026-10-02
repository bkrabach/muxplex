"""Manager-run DTU proof using v0.20.0 + its real installed engine.

Run in the combined DTU:
MUXPLEX_RUN_SDK_TESTS=1 python -m pytest -m integration \
    muxplex/tests/test_agent_sdk_integration.py
No factory, engine, provider, or credential-construction mock replaces this proof.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import httpx
import pytest
from itsdangerous import TimestampSigner

from muxplex import main
from muxplex.agent_embedded import credentials, runner
from muxplex.agent_embedded.errors import AgentRequestError
from muxplex.agent_embedded.host_tool_glue import TOOL_SPECS
from muxplex.tests.agent_provider_fixture import ProviderFixture, require_container

pytestmark = pytest.mark.integration


@pytest.fixture
def sdk_environment(monkeypatch, tmp_path):
    require_container()
    import amplifier_agent

    assert amplifier_agent.__version__ == "0.20.0", (
        "Use the frozen tagged SDK, not main."
    )
    # Lane A owns this supported storage/credential root; no AMPLIFIER_HOME.
    monkeypatch.setattr(credentials, "credential_home", lambda: tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-api-key")
    return tmp_path


def request(
    session_id=None, *, browser=True, content: str | list[dict[str, Any]] = "Hello"
):
    body = {
        "model": runner.default_model(),
        "messages": [{"role": "user", "content": content}],
    }
    if browser:
        body["muxplex_agent"] = {"protocol": 1, "browser_tools": True}
        if session_id:
            body["muxplex_agent"]["session_id"] = session_id
    return body


def data(chunk):
    if not chunk.startswith(b"data: ") or chunk == b"data: [DONE]\n\n":
        return None
    return json.loads(chunk[6:])


async def collect(run):
    return [chunk async for chunk in run.stream()]


def text(chunks):
    return "".join(
        part.get("choices", [{}])[0].get("delta", {}).get("content", "")
        for chunk in chunks
        if (part := data(chunk)) is not None
    )


async def test_real_sdk_streams_before_provider_finishes_and_projects_usage_once(
    sdk_environment, monkeypatch
):
    fixture = ProviderFixture(release=asyncio.Event())
    async with fixture.running() as url:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", url)
        run = await runner.prepare_chat(request(browser=False))
        output = []
        observed = asyncio.Event()

        async def read():
            async for chunk in run.stream():
                output.append(chunk)
                if text(output) == "Wire ":
                    observed.set()

        reader = asyncio.create_task(read())
        try:
            await asyncio.wait_for(observed.wait(), 15)
            assert not reader.done()  # genuine HTTP streaming, not buffered final
        finally:
            release = fixture.release
            assert release is not None
            release.set()
        await asyncio.wait_for(reader, 15)
    assert text(output) == "Wire reply"
    final = data(output[-2])
    assert final is not None
    assert final["choices"][0]["finish_reason"] == "stop"
    assert final["usage"]["prompt_tokens"] == 25
    assert final["usage"]["completion_tokens"] == 2
    assert final["usage"]["prompt_tokens_details"]["cached_tokens"] == 3
    assert output[-1] == b"data: [DONE]\n\n"
    assert run.closed and run.run_id not in runner._runs
    assert list(sdk_environment.glob("muxplex-browser-state/*.json")) == []


async def test_real_api_uses_tagged_sdk_and_returns_run_headers(
    sdk_environment, monkeypatch
):
    fixture = ProviderFixture()
    async with fixture.running() as url:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", url)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(main.app),
            base_url="http://127.0.0.1",
            cookies={
                "muxplex_session": TimestampSigner(main._auth_secret)
                .sign("fixture-owner")
                .decode()
            },
        ) as client:
            response = await client.post(
                "/api/agent/chat/completions", json=request(browser=False)
            )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert len(response.headers["X-Muxplex-Agent-Session-Id"]) == 32
    assert len(response.headers["X-Muxplex-Agent-Run-Id"]) == 32
    assert "Wire " in response.text and "reply" in response.text
    assert response.content.endswith(b"data: [DONE]\n\n")
    assert len(fixture.requests) == 1 and not runner._runs


@pytest.mark.parametrize("tool_name", [spec["name"] for spec in TOOL_SPECS])
async def test_real_sdk_same_turn_browser_callback_and_durable_resume(
    sdk_environment, monkeypatch, tool_name
):
    fixture = ProviderFixture(plan=[tool_name, None])
    capabilities = []
    async with fixture.running() as url:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", url)
        run = await runner.prepare_chat(request(), owner="owner-a")
        sdk_turn_id = run.turn.info.turn_id

        async def read():
            output = []
            async for chunk in run.stream():
                output.append(chunk)
                payload = data(chunk)
                if payload and "muxplex_browser_tool" in payload:
                    capability = payload["muxplex_browser_tool"]
                    capabilities.append(capability)
                    assert run.turn.info.turn_id == sdk_turn_id
                    assert (
                        capability["session_id"]
                        == run.headers["X-Muxplex-Agent-Session-Id"]
                    )
                    assert capability["run_id"] == run.headers["X-Muxplex-Agent-Run-Id"]
                    bridge = run.bridge
                    assert bridge is not None
                    assert capability["call_id"] in bridge.pending
                    result = {
                        key: capability[key]
                        for key in ("run_id", "call_id", "result_token")
                    }
                    result.update(
                        outcome="completed", content="Browser result recorded"
                    )
                    if tool_name == "send_muxplex_session_input":
                        result["confirmed"] = True
                    runner.submit_browser_result(result, owner="owner-a")
                    with pytest.raises(AgentRequestError) as duplicate:
                        runner.submit_browser_result(result, owner="owner-a")
                    assert duplicate.value.status == 409
            return output

        first = await asyncio.wait_for(read(), 20)
        assert len(capabilities) == 1
        assert len(fixture.requests) == 2
        assert "Browser result recorded" in json.dumps(fixture.requests[1])
        assert text(first) == "Wire reply"
        final = data(first[-2])
        assert final is not None
        assert final["usage"]["prompt_tokens"] == 50  # replacement, not 25+50
        assert final["usage"]["completion_tokens"] == 4
        advertised = fixture.requests[0]["tools"]
        assert {entry["name"] for entry in advertised} == {
            spec["name"] for spec in TOOL_SPECS
        }
        # New Agent/Session handles rehydrate SDK-owned durable storage after
        # all previous handles closed. No saved browser tool transcript is sent.
        resumed = await runner.prepare_chat(
            request(run.session_id, content="Continue"), owner="owner-a"
        )
        history = resumed.session.history
        assert len(history) == 1 and history[0].turn_id == sdk_turn_id
        second = await asyncio.wait_for(collect(resumed), 15)
    assert second[-1] == b"data: [DONE]\n\n"
    assert len(capabilities) == 1  # prior effect not replayed on resume
    assert "Browser result recorded" in json.dumps(fixture.requests[-1])
    assert len(fixture.requests) == 3


@pytest.mark.parametrize("outcome", ["failed", "unknown"])
async def test_real_sdk_does_not_redispatch_declined_or_unknown_typing(
    sdk_environment, monkeypatch, outcome
):
    fixture = ProviderFixture(
        plan=["send_muxplex_session_input", "send_muxplex_session_input", None]
    )
    count = 0
    async with fixture.running() as url:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", url)
        run = await runner.prepare_chat(request(), owner="owner-a")
        output = []
        async with asyncio.timeout(20):
            async for chunk in run.stream():
                output.append(chunk)
                payload = data(chunk)
                if payload and "muxplex_browser_tool" in payload:
                    count += 1
                    cap = payload["muxplex_browser_tool"]
                    runner.submit_browser_result(
                        {
                            **{
                                key: cap[key]
                                for key in ("run_id", "call_id", "result_token")
                            },
                            "outcome": outcome,
                            "error": "Declined"
                            if outcome == "failed"
                            else "Transport lost after effect",
                            "confirmed": False,
                        },
                        owner="owner-a",
                    )
    assert count == 1
    if outcome == "unknown":
        assert all(chunk != b"data: [DONE]\n\n" for chunk in output)
        assert any(
            payload.get("error")
            for chunk in output
            if (payload := data(chunk)) is not None
        )


@pytest.mark.parametrize("failure,partial", [(401, False), (503, False), (None, True)])
async def test_real_sdk_failure_and_eof_are_never_success(
    sdk_environment, monkeypatch, failure, partial
):
    fixture = ProviderFixture(failure=failure, partial_eof=partial)
    async with fixture.running() as url:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", url)
        run = await runner.prepare_chat(request(browser=False))
        output = await asyncio.wait_for(collect(run), 20)
    errors = [
        payload["error"]
        for chunk in output
        if (payload := data(chunk)) and "error" in payload
    ]
    assert errors and errors[-1]["code"] and errors[-1]["remedy"]
    assert b"data: [DONE]\n\n" not in output
    assert not any(
        payload.get("choices", [{}])[0].get("finish_reason") == "stop"
        for chunk in output
        if (payload := data(chunk))
    )
    assert len(fixture.requests) == 1


async def test_real_sdk_current_and_legacy_history_images_reach_provider(
    sdk_environment, monkeypatch
):
    encoded = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8FkAAAAASUVORK5CYII="
    image = {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{encoded}"},
    }
    fixture = ProviderFixture()
    async with fixture.running() as url:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", url)
        body = request(
            browser=False, content=[{"type": "text", "text": "current"}, image]
        )
        body["messages"].insert(0, {"role": "user", "content": [image]})
        body["messages"].insert(1, {"role": "assistant", "content": "Prior reply"})
        run = await runner.prepare_chat(body)
        output = await asyncio.wait_for(collect(run), 15)
    assert output[-1] == b"data: [DONE]\n\n"
    native_images = [
        part["source"]
        for message in fixture.requests[0]["messages"]
        for part in message["content"]
        if isinstance(part, dict) and part.get("type") == "image"
    ]
    assert (
        native_images
        == [{"type": "base64", "media_type": "image/png", "data": encoded}] * 2
    )


async def test_real_sdk_cancel_pending_bridge_drains_same_pump_and_closes(
    sdk_environment, monkeypatch
):
    fixture = ProviderFixture(plan=["send_muxplex_session_input"])
    async with fixture.running() as url:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", url)
        run = await runner.prepare_chat(request(), owner="owner-a")
        iterator = run.stream()
        async with asyncio.timeout(20):
            while True:
                chunk = await anext(iterator)
                payload = data(chunk)
                if payload and "muxplex_browser_tool" in payload:
                    cap = payload["muxplex_browser_tool"]
                    break
        pump = run.pump
        assert pump is not None
        await iterator.aclose()  # browser abort/disconnect, not a new turn
        assert run.pump is pump and pump.done()
        assert run.terminal is not None and run.terminal.state != "success"
        assert run.closed and not runner._runs
        with pytest.raises(AgentRequestError) as stale:
            runner.submit_browser_result(
                {
                    **{key: cap[key] for key in ("run_id", "call_id", "result_token")},
                    "outcome": "unknown",
                },
                owner="owner-a",
            )
        assert stale.value.status == 410
    assert len(fixture.requests) == 1


async def test_real_sdk_durable_owner_and_concurrent_turn_fences(
    sdk_environment, monkeypatch
):
    fixture = ProviderFixture(release=asyncio.Event())
    async with fixture.running() as url:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", url)
        run = await runner.prepare_chat(request(), owner="owner-a")
        with pytest.raises(AgentRequestError) as busy:
            await runner.prepare_chat(request(run.session_id), owner="owner-a")
        assert busy.value.status == 409
        release = fixture.release
        assert release is not None
        release.set()
        await asyncio.wait_for(collect(run), 15)
        with pytest.raises(AgentRequestError) as wrong_owner:
            await runner.prepare_chat(request(run.session_id), owner="owner-b")
        assert wrong_owner.value.status == 403
    assert len(fixture.requests) == 1


async def test_real_sdk_durable_resume_in_a_fresh_process_does_not_repeat_effect(
    sdk_environment, monkeypatch
):
    fixture = ProviderFixture(plan=["list_muxplex_sessions", None])
    async with fixture.running() as url:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", url)
        run = await runner.prepare_chat(
            request(content="First process"), owner="owner-a"
        )
        async with asyncio.timeout(20):
            async for chunk in run.stream():
                payload = data(chunk)
                if payload and "muxplex_browser_tool" in payload:
                    cap = payload["muxplex_browser_tool"]
                    runner.submit_browser_result(
                        {
                            **{
                                key: cap[key]
                                for key in ("run_id", "call_id", "result_token")
                            },
                            "outcome": "completed",
                            "content": "Recorded browser effect",
                        },
                        owner="owner-a",
                    )
        assert len(fixture.requests) == 2 and run.closed
        # Only storage-root redirection is test-specific. The child constructs
        # a REAL public Agent via the REAL lane-A credential helper.
        script = """
import asyncio, json, sys
from pathlib import Path
from muxplex.agent_embedded import credentials, runner, wire
credentials.credential_home = lambda: Path(sys.argv[1])
async def main():
    body = {
        "messages": [{"role": "user", "content": "Second process"}],
        "muxplex_agent": {"protocol": 1, "browser_tools": True, "session_id": sys.argv[2]},
    }
    run = await runner.prepare_chat(body, owner="owner-a")
    history = run.session.history
    assert len(history) == 1
    assert history[0].result.state == "success"
    chunks = [chunk async for chunk in run.stream()]
    assert chunks[-1] == wire.sse_done()
    assert not any(b"muxplex_browser_tool" in chunk for chunk in chunks)
    print(json.dumps({"history_turns": len(history), "closed": run.closed}))
asyncio.run(main())
"""
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            str(sdk_environment),
            run.session_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _stderr = await asyncio.wait_for(process.communicate(), 30)
        finally:
            if process.returncode is None:
                process.kill()  # exact child handle only, never name matching
                await process.wait()
        assert process.returncode == 0, (
            "Fresh-process SDK resume failed; inspect fixture/provider locally."
        )
        assert json.loads(stdout) == {"history_turns": 1, "closed": True}
    assert len(fixture.requests) == 3
    assert "Recorded browser effect" in json.dumps(fixture.requests[-1])
    assert "First process" in json.dumps(fixture.requests[-1])
    assert "Second process" in json.dumps(fixture.requests[-1])


@pytest.mark.parametrize("state", ["failure", "cancelled"])
async def test_real_sdk_measured_work_then_failure_or_cancel_projects_usage_once(
    sdk_environment, monkeypatch, state
):
    fixture = ProviderFixture(plan=["send_muxplex_session_input"])
    calls = 0
    async with fixture.running() as url:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", url)
        run = await runner.prepare_chat(request(), owner="owner-a")
        output = []
        async with asyncio.timeout(20):
            async for chunk in run.stream():
                output.append(chunk)
                payload = data(chunk)
                if payload and "muxplex_browser_tool" in payload:
                    calls += 1
                    cap = payload["muxplex_browser_tool"]
                    if state == "cancelled":
                        await run.turn.cancel()  # public cancellation, same event pump
                    else:
                        # Continue policy allows a model to explain an uncertain
                        # effect. Fail that real follow-up HTTP request instead
                        # of assuming ToolOutcomeUnknown itself ends the turn.
                        fixture.failure = 400
                        runner.submit_browser_result(
                            {
                                **{
                                    key: cap[key]
                                    for key in ("run_id", "call_id", "result_token")
                                },
                                "outcome": "unknown",
                                "error": "Fixture lost the effect's authoritative result",
                            },
                            owner="owner-a",
                        )
    assert calls == 1
    assert len(fixture.requests) == (1 if state == "cancelled" else 2)
    assert run.terminal.state == state
    payloads = [payload for chunk in output if (payload := data(chunk))]
    projected = [payload for payload in payloads if "usage" in payload]
    assert len(projected) == 1
    assert projected[0]["error"]["code"] == (
        "turn_cancelled" if state == "cancelled" else "provider_failed"
    )
    # A failed provider request makes the SDK's cumulative totals unknown.
    # Keep that authoritative snapshot rather than reusing prior lower totals.
    known = state == "cancelled"
    assert projected[0]["usage"] == {
        "prompt_tokens": 25 if known else None,
        "completion_tokens": 2 if known else None,
        "total_tokens": 27 if known else None,
        "prompt_tokens_details": {"cached_tokens": 3 if known else None},
    }
    assert b"data: [DONE]\n\n" not in output
    assert not any(
        choice.get("finish_reason") == "stop"
        for payload in payloads
        for choice in payload.get("choices", [])
    )
    assert run.closed and not runner._runs
