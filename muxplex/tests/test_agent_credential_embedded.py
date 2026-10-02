"""Credential adapter and HTTP contract tests, without an installed Agent SDK.

Use only the synthetic public ``amplifier_agent.create_agent`` seam and official
HTTP requests intercepted by MockTransport. The installer suite/CI independently
probe the installed SDK; these unit tests must neither import CLI internals nor
skip when the optional SDK is absent. All credential storage is under tmp_path.
"""

from __future__ import annotations

import asyncio
import builtins
import json
import os
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from muxplex.agent_embedded import credentials as creds
from muxplex.agent_embedded import runner
from muxplex.auth import create_session_cookie
from muxplex.main import (
    _AGENT_CREDENTIAL_ALLOWED_PROVIDERS,
    ProviderCredentialRequest,
    _auth_secret,
    _auth_ttl,
    app,
)

# Synthetic distinct values detect candidate/env/file mixups without real keys.
FILE_KEY = "unit-test-file-credential"
ENV_KEY = "unit-test-operator-credential"
CANDIDATE_KEY = "unit-test-candidate-credential"
NEW_KEY = "unit-test-replacement-credential"
_OPTIONS: Any = object()
_ENV_VARS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
_OFFICIAL_URLS = {
    "anthropic": "https://api.anthropic.com/v1/models",
    "openai": "https://api.openai.com/v1/models",
}
# Capture BEFORE patching: creds.httpx is the same module object as httpx.
_ORIGINAL_ASYNC_CLIENT = httpx.AsyncClient


def _authed_client() -> TestClient:
    cookie = create_session_cookie(_auth_secret, _auth_ttl)
    client = TestClient(app, base_url="http://192.168.1.1")
    client.cookies.set("muxplex_session", cookie)
    return client


def _mock_http(monkeypatch, handler):
    """Keep the real client lifecycle/parser, replacing only its transport."""
    requests = []
    client_options = []

    def recording_handler(request):
        requests.append(request)
        return handler(request)

    def client_with_transport(*args, **kwargs):
        client_options.append(dict(kwargs))
        kwargs["transport"] = httpx.MockTransport(recording_handler)
        return _ORIGINAL_ASYNC_CLIENT(*args, **kwargs)

    monkeypatch.setattr(creds.httpx, "AsyncClient", client_with_transport)
    return requests, client_options


def _assert_official_request(request, provider, candidate=CANDIDATE_KEY):
    assert request.method == "GET"
    assert str(request.url.copy_with(query=None)) == _OFFICIAL_URLS[provider]
    if provider == "anthropic":
        assert request.headers["x-api-key"] == candidate
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert "authorization" not in request.headers
        assert request.url.params["limit"] == "1000"
    else:
        assert request.headers["authorization"] == f"Bearer {candidate}"
        assert "x-api-key" not in request.headers
        assert not request.url.query


def _mock_model_response(
    monkeypatch, *, provider="anthropic", candidate=CANDIDATE_KEY, status_code=200
):
    def handler(request):
        _assert_official_request(request, provider, candidate)
        if status_code == 200:
            return httpx.Response(
                200, json={"data": [{"id": "unit-test-model"}], "has_more": False}
            )
        # Deliberately hostile body: the adapter must not echo it.
        return httpx.Response(status_code, text=f"rejected {candidate}")

    return _mock_http(monkeypatch, handler)


def _install_public_factory(monkeypatch, factory):
    module = ModuleType("amplifier_agent")
    monkeypatch.setattr(module, "create_agent", factory, raising=False)
    monkeypatch.setitem(sys.modules, "amplifier_agent", module)
    return module


@pytest.fixture(autouse=True)
def _isolate_agent_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(creds, "_credential_root", None)
    monkeypatch.setattr(creds, "_file_environment", {})
    monkeypatch.setattr(creds, "_construction_lock", asyncio.Lock())
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path))
    for name in _ENV_VARS.values():
        # Register even initially ABSENT variables before removing them.
        # Factory assignments use os.environ directly, not monkeypatch; a bare
        # delenv on an absent variable would fail to undo those at teardown.
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)

    def unexpected_request(request):
        raise AssertionError(f"unexpected provider HTTP request to {request.url.host}")

    # Fail closed if a test/route forgets to supply its own mock response.
    _mock_http(monkeypatch, unexpected_request)


@pytest.fixture(autouse=True)
def _force_embedded_mode(monkeypatch):
    monkeypatch.setattr("muxplex.agent_embedded.is_embedded_mode", lambda: True)


@pytest.fixture(autouse=True)
def _assume_library_available(monkeypatch):
    async def available():
        return None

    monkeypatch.setattr(runner, "library_unavailable_reason", available)
    monkeypatch.setattr(runner, "active_provider", lambda: "anthropic")
    monkeypatch.setattr(runner, "default_model", lambda: "unit-test-model")


# Schema, allowlist, and read-side invariants.
# closing() closes the client WITHOUT entering TestClient's app lifespan:
# these are route tests, not service/background-poll startup tests.


def test_request_model_has_no_endpoint_field():
    assert set(ProviderCredentialRequest.model_fields) == {"provider", "api_key"}


def test_allowlist_is_exactly_anthropic_and_openai():
    assert _AGENT_CREDENTIAL_ALLOWED_PROVIDERS == frozenset({"anthropic", "openai"})
    assert creds.ALLOWED_PROVIDERS == _AGENT_CREDENTIAL_ALLOWED_PROVIDERS


@pytest.mark.parametrize(
    "provider", ["azure-openai", "ollama", "github-copilot", "made-up-provider"]
)
def test_post_rejects_non_allowlisted_providers(provider, monkeypatch):
    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("must not validate a disallowed provider")

    monkeypatch.setattr(creds, "validate_key", fail_if_called)
    with closing(_authed_client()) as client:
        response = client.post(
            "/api/agent/provider-credential",
            json={"provider": provider, "api_key": CANDIDATE_KEY},
        )
    assert response.status_code == 400
    assert CANDIDATE_KEY not in response.text


def test_post_endpoint_field_is_silently_dropped_not_an_error(monkeypatch):
    requests, _ = _mock_model_response(monkeypatch)
    with closing(_authed_client()) as client:
        response = client.post(
            "/api/agent/provider-credential",
            json={
                "provider": "anthropic",
                "api_key": CANDIDATE_KEY,
                "endpoint": "https://evil.example.com",
            },
        )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert len(requests) == 1
    _assert_official_request(requests[0], "anthropic")


def test_resolve_status_reports_not_set_when_nothing_configured():
    assert creds.resolve_status("anthropic") == {
        "source": "not_set",
        "masked": None,
        "env_var": "ANTHROPIC_API_KEY",
    }


def test_resolve_status_reports_env_and_masks_the_value(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", ENV_KEY)
    status = creds.resolve_status("anthropic")
    assert status["source"] == "env"
    assert status["masked"] == f"{ENV_KEY[:6]}...{ENV_KEY[-4:]}"
    assert ENV_KEY not in json.dumps(status)


def test_resolve_status_reports_file_when_only_stored(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text(
        json.dumps({"version": 1, "providers": {"anthropic": {"api_key": FILE_KEY}}})
    )
    status = creds.resolve_status("anthropic")
    assert status["source"] == "file"
    assert status["masked"] == f"{FILE_KEY[:6]}...{FILE_KEY[-4:]}"
    assert FILE_KEY not in json.dumps(status)


def test_resolve_status_env_wins_over_file(monkeypatch):
    creds.persist_key("anthropic", FILE_KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", ENV_KEY)
    status = creds.resolve_status("anthropic")
    assert status["source"] == "env"
    assert status["masked"] == f"{ENV_KEY[:6]}...{ENV_KEY[-4:]}"


# Stable legacy root and public factory construction (no real SDK imported).


async def test_legacy_home_cached_and_removed_before_public_factory_import(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path / "operator-home"))
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "sdk-home"))
    seen_imports = []
    options_seen = []
    agent = object()

    async def factory(options):
        options_seen.append(options)
        assert "AMPLIFIER_AGENT_HOME" not in os.environ
        return agent

    module = _install_public_factory(monkeypatch, factory)
    original_import = builtins.__import__

    def checked_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "amplifier_agent":
            seen_imports.append(name)
            assert creds._credential_root == tmp_path
            assert "AMPLIFIER_AGENT_HOME" not in os.environ
            return module
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", checked_import)
    assert await creds.create_agent_with_credentials(_OPTIONS) is agent
    assert seen_imports == ["amplifier_agent"]
    assert options_seen == [_OPTIONS]
    assert os.environ["HOME"] == str(tmp_path / "operator-home")
    assert os.environ["AMPLIFIER_HOME"] == str(tmp_path / "sdk-home")

    # Even a newly supplied retired override cannot redirect the cached store.
    monkeypatch.setenv("AMPLIFIER_AGENT_HOME", str(tmp_path / "redirect"))
    assert creds.credential_home() == tmp_path
    assert "AMPLIFIER_AGENT_HOME" not in os.environ
    await creds.create_agent_with_credentials(_OPTIONS)
    assert "AMPLIFIER_AGENT_HOME" not in os.environ


def test_default_credential_home_is_legacy_home_not_sdk_home(tmp_path, monkeypatch):
    monkeypatch.delenv("AMPLIFIER_AGENT_HOME")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "sdk-home"))
    assert creds.credential_home() == tmp_path / ".amplifier-agent"
    path = creds.persist_key("anthropic", FILE_KEY)
    assert path == tmp_path / ".amplifier-agent" / "credentials.json"
    assert os.environ["AMPLIFIER_HOME"] == str(tmp_path / "sdk-home")


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
async def test_true_environment_wins_during_public_factory(provider, monkeypatch):
    creds.persist_key(provider, FILE_KEY)
    name = _ENV_VARS[provider]
    monkeypatch.setenv(name, ENV_KEY)
    captured = []

    async def factory(options):
        assert options is _OPTIONS
        captured.append(os.environ[name])
        assert creds.resolve_status(provider)["source"] == "env"
        return object()

    _install_public_factory(monkeypatch, factory)
    await creds.create_agent_with_credentials(_OPTIONS)
    assert captured == [ENV_KEY]
    assert os.environ[name] == ENV_KEY
    assert name not in creds._file_environment


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
async def test_file_fallback_stays_file_and_next_factory_captures_saved_key(
    provider, monkeypatch
):
    creds.persist_key(provider, FILE_KEY)
    name = _ENV_VARS[provider]
    captured = []

    async def factory(_options):
        captured.append(os.environ[name])
        assert creds.resolve_status(provider)["source"] == "file"
        return object()

    _install_public_factory(monkeypatch, factory)
    await creds.create_agent_with_credentials(_OPTIONS)
    assert os.environ[name] == FILE_KEY
    assert creds._file_environment[name] == FILE_KEY
    assert creds.resolve_status(provider)["source"] == "file"

    creds.persist_key(provider, NEW_KEY)
    # Saving does not mutate an environment potentially being captured.
    assert os.environ[name] == FILE_KEY
    assert creds.resolve_status(provider)["source"] == "file"
    await creds.create_agent_with_credentials(_OPTIONS)
    assert captured == [FILE_KEY, NEW_KEY]
    assert os.environ[name] == NEW_KEY
    assert creds._file_environment[name] == NEW_KEY
    assert creds.resolve_status(provider)["source"] == "file"


@pytest.mark.parametrize("failure", ["error", "cancel"])
async def test_failed_or_cancelled_factory_does_not_restore_fallback(
    failure, monkeypatch
):
    creds.persist_key("anthropic", FILE_KEY)

    async def successful_factory(_options):
        return object()

    _install_public_factory(monkeypatch, successful_factory)
    await creds.create_agent_with_credentials(_OPTIONS)
    creds.persist_key("anthropic", NEW_KEY)
    entered = asyncio.Event()

    async def failing_factory(_options):
        assert os.environ["ANTHROPIC_API_KEY"] == NEW_KEY
        assert "AMPLIFIER_AGENT_HOME" not in os.environ
        entered.set()
        if failure == "error":
            raise RuntimeError("synthetic construction failure")
        await asyncio.Event().wait()

    _install_public_factory(monkeypatch, failing_factory)
    task = asyncio.create_task(creds.create_agent_with_credentials(_OPTIONS))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        if failure == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(RuntimeError, match="synthetic construction failure"):
                await task
        assert os.environ["ANTHROPIC_API_KEY"] == NEW_KEY
        assert creds._file_environment["ANTHROPIC_API_KEY"] == NEW_KEY
        assert "AMPLIFIER_AGENT_HOME" not in os.environ
        assert not creds._construction_lock.locked()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_concurrent_public_constructors_serialize_across_await(monkeypatch):
    creds.persist_key("anthropic", FILE_KEY)
    entered = asyncio.Event()
    release = asyncio.Event()
    second_started = asyncio.Event()
    captured = []

    async def factory(_options):
        captured.append(os.environ["ANTHROPIC_API_KEY"])
        if len(captured) == 1:
            entered.set()
            await release.wait()
            assert os.environ["ANTHROPIC_API_KEY"] == FILE_KEY
        return object()

    async def second_constructor():
        second_started.set()
        return await creds.create_agent_with_credentials(_OPTIONS)

    _install_public_factory(monkeypatch, factory)
    first = asyncio.create_task(creds.create_agent_with_credentials(_OPTIONS))
    tasks = [first]
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        creds.persist_key("anthropic", NEW_KEY)
        second = asyncio.create_task(second_constructor())
        tasks.append(second)
        await asyncio.wait_for(second_started.wait(), timeout=1)
        assert captured == [FILE_KEY]
        assert not second.done()
        assert creds._construction_lock.locked()
        assert os.environ["ANTHROPIC_API_KEY"] == FILE_KEY
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)
        assert captured == [FILE_KEY, NEW_KEY]
        assert os.environ["ANTHROPIC_API_KEY"] == NEW_KEY
        assert not creds._construction_lock.locked()
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_save_during_await_changes_only_disk_then_next_capture(monkeypatch):
    creds.persist_key("anthropic", FILE_KEY)
    entered = asyncio.Event()
    release = asyncio.Event()
    snapshots = []

    async def factory(_options):
        before = os.environ["ANTHROPIC_API_KEY"]
        entered.set()
        await release.wait()
        after = os.environ["ANTHROPIC_API_KEY"]
        snapshots.append((before, after))
        return object()

    _install_public_factory(monkeypatch, factory)
    task = asyncio.create_task(creds.create_agent_with_credentials(_OPTIONS))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        path = creds.persist_key("anthropic", NEW_KEY)
        assert (
            json.loads(path.read_text())["providers"]["anthropic"]["api_key"] == NEW_KEY
        )
        assert os.environ["ANTHROPIC_API_KEY"] == FILE_KEY
        assert creds._file_environment["ANTHROPIC_API_KEY"] == FILE_KEY
        assert creds.resolve_status("anthropic")["source"] == "file"
        release.set()
        await asyncio.wait_for(task, timeout=1)
        await creds.create_agent_with_credentials(_OPTIONS)
        assert snapshots == [(FILE_KEY, FILE_KEY), (NEW_KEY, NEW_KEY)]
        assert os.environ["ANTHROPIC_API_KEY"] == NEW_KEY
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("replacement", [None, ENV_KEY])
async def test_removed_file_clears_owned_env_but_not_operator_replacement(
    replacement, monkeypatch
):
    path = creds.persist_key("anthropic", FILE_KEY)
    captured = []

    async def factory(_options):
        captured.append(os.environ.get("ANTHROPIC_API_KEY"))
        return object()

    _install_public_factory(monkeypatch, factory)
    await creds.create_agent_with_credentials(_OPTIONS)
    path.unlink()
    if replacement is not None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", replacement)
    await creds.create_agent_with_credentials(_OPTIONS)
    assert captured == [FILE_KEY, replacement]
    assert "ANTHROPIC_API_KEY" not in creds._file_environment
    if replacement is None:
        assert "ANTHROPIC_API_KEY" not in os.environ
        assert creds.resolve_status("anthropic")["source"] == "not_set"
    else:
        assert os.environ["ANTHROPIC_API_KEY"] == ENV_KEY
        assert creds.resolve_status("anthropic")["source"] == "env"


# Durable store: legacy upgrade, strict envelope, atomicity, permissions.


def test_persist_key_writes_private_file_with_private_parent_and_readback(tmp_path):
    tmp_path.chmod(0o777)
    path = creds.persist_key("anthropic", FILE_KEY)
    assert path == tmp_path / "credentials.json"
    assert json.loads(path.read_text()) == {
        "version": 1,
        "providers": {"anthropic": {"api_key": FILE_KEY}},
    }
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert creds.resolve_status("anthropic")["source"] == "file"
    # Replacement also repairs an existing overly permissive file.
    path.chmod(0o644)
    creds.persist_key("anthropic", NEW_KEY)
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text())["providers"]["anthropic"]["api_key"] == NEW_KEY
    assert not list(tmp_path.glob(".credentials-*"))


def test_persist_key_preserves_other_providers(tmp_path):
    creds.persist_key("anthropic", FILE_KEY)
    creds.persist_key("openai", NEW_KEY)
    data = json.loads((tmp_path / "credentials.json").read_text())
    assert data["providers"]["anthropic"]["api_key"] == FILE_KEY
    assert data["providers"]["openai"]["api_key"] == NEW_KEY


@pytest.mark.parametrize("entry", [FILE_KEY, {"api_key": FILE_KEY, "note": "retain"}])
def test_legacy_flat_scalar_and_object_upgrade_without_losing_unknowns(entry, tmp_path):
    unknown = {
        "api_key": "unit-test-unknown-provider",
        "endpoint": "retain",
        "extra": [1],
    }
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({"anthropic": entry, "future-provider": unknown}))
    original = path.read_bytes()
    assert creds.resolve_status("anthropic")["source"] == "file"
    assert path.read_bytes() == original  # Read upgrades only in memory.
    creds.persist_key("openai", NEW_KEY)
    data = json.loads(path.read_text())
    assert data["version"] == 1
    assert data["providers"]["anthropic"] == (
        entry if isinstance(entry, dict) else {"api_key": FILE_KEY}
    )
    assert data["providers"]["future-provider"] == unknown
    assert data["providers"]["openai"] == {"api_key": NEW_KEY}


def test_v1_save_preserves_top_level_and_provider_extra_fields(tmp_path):
    original = {
        "version": 1,
        "owner": {"label": "retain"},
        "providers": {
            "anthropic": {"api_key": FILE_KEY, "extra": {"keep": True}},
            "future-provider": {"token_kind": "unknown", "extra": [1, 2]},
        },
    }
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps(original))
    creds.persist_key("anthropic", NEW_KEY)
    expected = json.loads(json.dumps(original))
    expected["providers"]["anthropic"]["api_key"] = NEW_KEY
    assert json.loads(path.read_text()) == expected


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param('{"broken": "unit-test-file-credential",', id="invalid-json"),
        pytest.param(json.dumps([FILE_KEY]), id="not-object"),
        pytest.param(json.dumps({"version": 1, "extra": FILE_KEY}), id="no-providers"),
        pytest.param(json.dumps({"providers": {}}), id="no-version"),
        pytest.param(json.dumps({"version": True, "providers": {}}), id="bool-version"),
        pytest.param(json.dumps({"version": 1.0, "providers": {}}), id="float-version"),
        pytest.param(
            json.dumps({"version": "1", "providers": {}}), id="string-version"
        ),
        pytest.param(json.dumps({"version": 2, "providers": {}}), id="future-version"),
        pytest.param(json.dumps({"version": 1, "providers": []}), id="providers-list"),
        pytest.param(
            json.dumps({"version": 1, "providers": {"anthropic": FILE_KEY}}),
            id="v1-scalar-entry",
        ),
        pytest.param(
            json.dumps({"version": 1, "providers": {"anthropic": None}}),
            id="null-entry",
        ),
        pytest.param(
            json.dumps({"version": 1, "providers": {"anthropic": {"api_key": 42}}}),
            id="nonstring-key",
        ),
        pytest.param(json.dumps({"anthropic": {"api_key": None}}), id="flat-null-key"),
        pytest.param(json.dumps({"anthropic": 42}), id="flat-numeric-key"),
        pytest.param(
            json.dumps(
                {
                    "version": 1,
                    "providers": {
                        "anthropic": {"api_key": FILE_KEY},
                        "future-provider": {"api_key": []},
                    },
                }
            ),
            id="invalid-unknown-provider",
        ),
    ],
)
def test_invalid_credentials_refuse_read_and_save_without_overwrite_or_echo(
    raw, tmp_path
):
    path = tmp_path / "credentials.json"
    path.write_text(raw)
    original = path.read_bytes()
    with pytest.raises(ValueError) as read_error:
        creds.resolve_status("anthropic")
    with pytest.raises(ValueError) as save_error:
        creds.persist_key("anthropic", CANDIDATE_KEY)
    for error in (read_error, save_error):
        assert FILE_KEY not in str(error.value)
        assert CANDIDATE_KEY not in str(error.value)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".credentials-*"))
    assert not creds._file_environment
    assert all(name not in os.environ for name in _ENV_VARS.values())


async def test_invalid_envelope_refuses_factory_before_any_env_injection(
    tmp_path, monkeypatch
):
    path = tmp_path / "credentials.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "providers": {
                    "anthropic": {"api_key": FILE_KEY},
                    "openai": {"api_key": 42},
                },
            }
        )
    )

    async def forbidden_factory(_options):
        raise AssertionError("invalid envelope must not reach the public factory")

    _install_public_factory(monkeypatch, forbidden_factory)
    with pytest.raises(ValueError):
        await creds.create_agent_with_credentials(_OPTIONS)
    assert not creds._file_environment
    assert all(name not in os.environ for name in _ENV_VARS.values())


def test_atomic_replace_failure_preserves_prior_bytes_and_cleans_private_tmp(
    tmp_path, monkeypatch
):
    path = creds.persist_key("anthropic", FILE_KEY)
    original = path.read_bytes()
    attempted = []

    def fail_replace(source, destination):
        temporary = Path(source)
        assert Path(destination) == path
        assert temporary.parent == path.parent
        assert temporary.name.startswith(".credentials-")
        assert temporary.stat().st_mode & 0o777 == 0o600
        assert json.loads(temporary.read_text())["providers"]["anthropic"][
            "api_key"
        ] == (NEW_KEY)
        assert path.read_bytes() == original
        attempted.append(temporary)
        raise OSError("synthetic atomic replacement failure")

    monkeypatch.setattr(creds.os, "replace", fail_replace)
    with pytest.raises(OSError, match="synthetic atomic replacement failure"):
        creds.persist_key("anthropic", NEW_KEY)
    assert len(attempted) == 1
    assert not attempted[0].exists()
    assert path.read_bytes() == original
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert not list(tmp_path.glob(".credentials-*"))


def test_dangling_store_symlink_is_not_overwritten(tmp_path):
    path = tmp_path / "credentials.json"
    target = tmp_path / "missing-target"
    path.symlink_to(target)
    with pytest.raises(ValueError, match="repair the link"):
        creds.persist_key("anthropic", NEW_KEY)
    assert path.is_symlink()
    assert path.readlink() == target
    assert not target.exists()


# Explicit candidate validation through the fixed official HTTP boundary.


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
async def test_validate_key_ok_on_official_models_returned(provider, monkeypatch):
    requests, client_options = _mock_model_response(monkeypatch, provider=provider)
    verdict, detail = await creds.validate_key(
        provider, CANDIDATE_KEY, timeout_seconds=2.5
    )
    assert verdict == "ok"
    assert "1 model" in detail
    assert len(requests) == 1
    assert client_options == [
        {"timeout": 2.5, "follow_redirects": False, "trust_env": False}
    ]


@pytest.mark.parametrize("status_code", [401, 403])
async def test_validate_key_bad_key_on_authentication_error(status_code, monkeypatch):
    _mock_model_response(monkeypatch, status_code=status_code)
    verdict, detail = await creds.validate_key("anthropic", CANDIDATE_KEY)
    assert verdict == "bad_key"
    assert str(status_code) in detail
    assert CANDIDATE_KEY not in detail


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout])
async def test_validate_key_unreachable_on_connection_or_timeout(
    error_type, monkeypatch
):
    def handler(request):
        _assert_official_request(request, "anthropic")
        raise error_type(f"failed with {CANDIDATE_KEY}", request=request)

    _mock_http(monkeypatch, handler)
    verdict, detail = await creds.validate_key("anthropic", CANDIDATE_KEY)
    assert verdict == "unreachable"
    assert CANDIDATE_KEY not in detail


@pytest.mark.parametrize("status_code", [302, 429, 500])
async def test_non_auth_http_status_is_unreachable_without_following_redirects(
    status_code, monkeypatch
):
    def handler(request):
        _assert_official_request(request, "anthropic")
        return httpx.Response(
            status_code,
            headers={"location": "https://evil.example.com"},
            text=f"hostile body {CANDIDATE_KEY}",
        )

    requests, _ = _mock_http(monkeypatch, handler)
    verdict, detail, models = await creds._enumerate_models("anthropic", CANDIDATE_KEY)
    assert verdict == "unreachable"
    assert models is None
    assert str(status_code) in detail
    assert CANDIDATE_KEY not in detail
    assert len(requests) == 1


@pytest.mark.parametrize(
    "body",
    [
        "not-json unit-test-candidate-credential",
        json.dumps([CANDIDATE_KEY]),
        json.dumps({"error": CANDIDATE_KEY}),
        json.dumps({"data": CANDIDATE_KEY}),
        json.dumps({"data": [{"id": "valid"}, {"api_key": CANDIDATE_KEY}]}),
        json.dumps({"data": [{"id": 42}]}),
    ],
)
async def test_malformed_model_response_is_error_not_partial_success(body, monkeypatch):
    def handler(request):
        _assert_official_request(request, "anthropic")
        return httpx.Response(200, text=body)

    _mock_http(monkeypatch, handler)
    verdict, detail, models = await creds._enumerate_models("anthropic", CANDIDATE_KEY)
    assert verdict == "error"
    assert models is None
    assert CANDIDATE_KEY not in detail


async def test_validate_key_never_persists_anything(monkeypatch, tmp_path):
    _mock_model_response(monkeypatch, status_code=401)
    await creds.validate_key("anthropic", CANDIDATE_KEY)
    assert not (tmp_path / "credentials.json").exists()


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
@pytest.mark.parametrize("status_code", [200, 403])
@pytest.mark.parametrize("operator_env", [False, True], ids=["file-fallback", "env"])
async def test_explicit_candidate_validation_leaves_env_and_file_unchanged(
    provider, status_code, operator_env, monkeypatch
):
    path = creds.persist_key(provider, FILE_KEY)

    async def factory(_options):
        return object()

    _install_public_factory(monkeypatch, factory)
    await creds.create_agent_with_credentials(_OPTIONS)
    assert creds.resolve_status(provider)["source"] == "file"
    if operator_env:
        monkeypatch.setenv(_ENV_VARS[provider], ENV_KEY)
    for name in ("ANTHROPIC_BASE_URL", "OPENAI_BASE_URL", "HTTP_PROXY", "HTTPS_PROXY"):
        monkeypatch.setenv(name, "https://evil.example.com")
    before_environment = dict(os.environ)
    before_provenance = dict(creds._file_environment)
    before_bytes = path.read_bytes()
    requests, client_options = _mock_model_response(
        monkeypatch, provider=provider, status_code=status_code
    )
    verdict, detail = await creds.validate_key(provider, CANDIDATE_KEY)
    assert verdict == ("ok" if status_code == 200 else "bad_key")
    assert dict(os.environ) == before_environment
    assert creds._file_environment == before_provenance
    assert path.read_bytes() == before_bytes
    assert len(requests) == 1
    assert client_options[0]["trust_env"] is False
    for value in (FILE_KEY, ENV_KEY, CANDIDATE_KEY):
        assert value not in detail


async def test_model_enumeration_has_whole_operation_timeout(monkeypatch):
    cancelled = asyncio.Event()

    async def handler(request):
        _assert_official_request(request, "anthropic")
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    requests, client_options = _mock_http(monkeypatch, handler)
    result = await asyncio.wait_for(
        creds._enumerate_models("anthropic", CANDIDATE_KEY, timeout_seconds=0.01),
        timeout=1,
    )
    verdict, detail, models = result
    assert verdict == "unreachable"
    assert "timed out" in detail
    assert models is None
    assert cancelled.is_set()
    assert len(requests) == 1
    assert client_options[0]["timeout"] == 0.01
    assert CANDIDATE_KEY not in detail


async def test_anthropic_pagination_uses_official_url_and_explicit_candidate(
    monkeypatch,
):
    def handler(request):
        _assert_official_request(request, "anthropic")
        if "after_id" not in request.url.params:
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "first"}],
                    "has_more": True,
                    "last_id": "cursor-one",
                },
            )
        assert request.url.params["after_id"] == "cursor-one"
        return httpx.Response(200, json={"data": [{"id": "second"}], "has_more": False})

    requests, _ = _mock_http(monkeypatch, handler)
    verdict, _detail, models = await creds._enumerate_models("anthropic", CANDIDATE_KEY)
    assert verdict == "ok"
    assert models == [{"id": "first"}, {"id": "second"}]
    assert len(requests) == 2


@pytest.mark.parametrize(
    "pagination",
    [
        {},
        {"has_more": True},
        {"has_more": True, "last_id": ""},
        {"has_more": True, "last_id": 42},
        {"has_more": True, "last_id": ["cursor"]},
        {"has_more": "true", "last_id": "cursor-two"},
        {"has_more": None, "last_id": "cursor-two"},
        {"has_more": 0, "last_id": "cursor-two"},
    ],
)
async def test_malformed_pagination_discards_already_fetched_models(
    pagination, monkeypatch
):
    def handler(request):
        _assert_official_request(request, "anthropic")
        if "after_id" not in request.url.params:
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "first"}],
                    "has_more": True,
                    "last_id": "cursor-one",
                },
            )
        return httpx.Response(200, json={"data": [{"id": "partial"}], **pagination})

    requests, _ = _mock_http(monkeypatch, handler)
    verdict, _detail, models = await creds._enumerate_models("anthropic", CANDIDATE_KEY)
    assert verdict == "error"
    assert models is None
    assert len(requests) == 2


async def test_repeated_pagination_cursor_discards_partial_models(monkeypatch):
    def handler(request):
        _assert_official_request(request, "anthropic")
        return httpx.Response(
            200,
            json={
                "data": [{"id": "partial"}],
                "has_more": True,
                "last_id": "same-cursor",
            },
        )

    requests, _ = _mock_http(monkeypatch, handler)
    verdict, _detail, models = await creds._enumerate_models("anthropic", CANDIDATE_KEY)
    assert verdict == "error"
    assert models is None
    assert len(requests) == 2
    assert requests[1].url.params["after_id"] == "same-cursor"


async def test_pagination_page_cap_discards_partial_models(monkeypatch):
    page_count = 0

    def handler(request):
        nonlocal page_count
        _assert_official_request(request, "anthropic")
        page_count += 1
        if page_count > 1:
            assert request.url.params["after_id"] == f"cursor-{page_count - 1}"
        return httpx.Response(
            200,
            json={
                "data": [{"id": f"model-{page_count}"}],
                "has_more": True,
                "last_id": f"cursor-{page_count}",
            },
        )

    _mock_http(monkeypatch, handler)
    verdict, _detail, models = await creds._enumerate_models("anthropic", CANDIDATE_KEY)
    assert verdict == "error"
    assert models is None
    assert page_count == 20


# Full status: availability belongs to the active provider, not any stored key.


async def test_full_status_not_installed_when_library_unavailable(monkeypatch):
    async def unavailable():
        return "amplifier-agent is not installed in this Python environment"

    monkeypatch.setattr(runner, "library_unavailable_reason", unavailable)
    status = await creds.full_status()
    assert status["state"] == "not_installed"
    assert status["providers"] == {}


async def test_full_status_not_configured_when_nothing_resolves():
    status = await creds.full_status()
    assert status["state"] == "not_configured"
    assert status["providers"]["anthropic"]["source"] == "not_set"
    assert status["providers"]["openai"]["source"] == "not_set"
    assert status["sidecar"] == "running"
    assert status["mode"] == "embedded"


async def test_full_status_configured_from_file():
    creds.persist_key("anthropic", FILE_KEY)
    status = await creds.full_status()
    assert status["state"] == "configured"
    assert status["providers"]["anthropic"]["source"] == "file"


async def test_full_status_configured_shadowed_when_env_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", ENV_KEY)
    status = await creds.full_status()
    assert status["state"] == "configured_shadowed"
    assert status["providers"]["anthropic"]["source"] == "env"


async def test_full_status_gating_ignores_a_non_active_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", ENV_KEY)
    status = await creds.full_status()
    assert status["state"] == "not_configured"
    assert status["providers"]["openai"]["source"] == "env"
    assert status["providers"]["anthropic"]["source"] == "not_set"


# Existing FastAPI GET/POST contract, in process without subprocesses or SDK.


def test_get_embedded_not_configured():
    with closing(_authed_client()) as client:
        response = client.get("/api/agent/provider-credential")
    assert response.status_code == 200
    assert response.json()["state"] == "not_configured"
    assert response.json()["mode"] == "embedded"


def test_get_embedded_configured_shadowed_when_env_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", ENV_KEY)
    with closing(_authed_client()) as client:
        response = client.get("/api/agent/provider-credential")
    assert response.status_code == 200
    assert response.json()["state"] == "configured_shadowed"
    assert response.json()["providers"]["anthropic"]["source"] == "env"
    assert ENV_KEY not in response.text


def test_post_embedded_env_set_is_a_no_op(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", ENV_KEY)

    async def forbidden_validation(*_args, **_kwargs):
        raise AssertionError("operator env must bypass validation and persistence")

    monkeypatch.setattr(creds, "validate_key", forbidden_validation)
    with closing(_authed_client()) as client:
        response = client.post(
            "/api/agent/provider-credential",
            json={"provider": "anthropic", "api_key": CANDIDATE_KEY},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["no_op"] is True
    assert "ANTHROPIC_API_KEY" in body["detail"]
    assert not (tmp_path / "credentials.json").exists()
    assert ENV_KEY not in response.text
    assert CANDIDATE_KEY not in response.text


def test_post_embedded_valid_key_persists_and_is_then_resolvable(monkeypatch, tmp_path):
    path = tmp_path / "credentials.json"

    def handler(request):
        _assert_official_request(request, "anthropic")
        assert not path.exists()  # Validate BEFORE writing.
        return httpx.Response(
            200, json={"data": [{"id": "unit-test-model"}], "has_more": False}
        )

    requests, _ = _mock_http(monkeypatch, handler)
    with closing(_authed_client()) as client:
        response = client.post(
            "/api/agent/provider-credential",
            json={"provider": "anthropic", "api_key": CANDIDATE_KEY},
        )
        readback = client.get("/api/agent/provider-credential")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body.get("no_op") is False
    assert body["restarted"] is False
    assert "no restart needed" in body["detail"]
    assert CANDIDATE_KEY not in response.text
    assert len(requests) == 1
    assert json.loads(path.read_text())["providers"]["anthropic"]["api_key"] == (
        CANDIDATE_KEY
    )
    assert creds.resolve_status("anthropic")["source"] == "file"
    assert readback.status_code == 200
    assert readback.json()["providers"]["anthropic"]["source"] == "file"
    assert CANDIDATE_KEY not in readback.text


def test_post_embedded_bad_key_rejected_and_never_persisted(monkeypatch, tmp_path):
    _mock_model_response(monkeypatch, status_code=401)
    with closing(_authed_client()) as client:
        response = client.post(
            "/api/agent/provider-credential",
            json={"provider": "anthropic", "api_key": CANDIDATE_KEY},
        )
    assert response.status_code == 400
    assert "invalid" in response.text.lower()
    assert not (tmp_path / "credentials.json").exists()
    assert CANDIDATE_KEY not in response.text


def test_post_embedded_unreachable_is_502_and_never_persisted(monkeypatch, tmp_path):
    def handler(request):
        _assert_official_request(request, "anthropic")
        raise httpx.ConnectError(f"connection failed {CANDIDATE_KEY}", request=request)

    _mock_http(monkeypatch, handler)
    with closing(_authed_client()) as client:
        response = client.post(
            "/api/agent/provider-credential",
            json={"provider": "anthropic", "api_key": CANDIDATE_KEY},
        )
    assert response.status_code == 502
    assert not (tmp_path / "credentials.json").exists()
    assert CANDIDATE_KEY not in response.text


def test_post_embedded_rejects_non_allowlisted_provider_before_any_validation(
    monkeypatch,
):
    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("must not validate a disallowed provider")

    monkeypatch.setattr(creds, "validate_key", fail_if_called)
    with closing(_authed_client()) as client:
        response = client.post(
            "/api/agent/provider-credential",
            json={"provider": "ollama", "api_key": CANDIDATE_KEY},
        )
    assert response.status_code == 400
    assert CANDIDATE_KEY not in response.text


def test_post_embedded_never_shells_out(monkeypatch):
    async def fail_async_spawn(*_args, **_kwargs):
        raise AssertionError("embedded POST must never spawn a subprocess")

    def fail_sync_spawn(*_args, **_kwargs):
        raise AssertionError("embedded POST must never spawn a subprocess")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_async_spawn)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", fail_async_spawn)
    monkeypatch.setattr(subprocess, "Popen", fail_sync_spawn)
    monkeypatch.setattr(subprocess, "run", fail_sync_spawn)
    _mock_model_response(monkeypatch)
    with closing(_authed_client()) as client:
        response = client.post(
            "/api/agent/provider-credential",
            json={"provider": "anthropic", "api_key": CANDIDATE_KEY},
        )
    assert response.status_code == 200
