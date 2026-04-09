"""
Tests for settings sync logic in the poll cycle (main.py).

Covers:
- SETTINGS_SYNC_INTERVAL constant exists
- _settings_sync_counter module-level variable exists
- _sync_settings_with_remotes function exists and is callable
- Sync adopts newer remote settings (GET, remote_ts > local_ts)
- Sync pushes local settings to older remote (PUT, local_ts > remote_ts)
- Sync skips when timestamps are equal
- Sync gracefully handles 404 from older muxplex instances
- Sync gracefully handles connection errors
- Sync skips remotes with no URL
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import muxplex.main as main_mod
import muxplex.settings as settings_mod


# ---------------------------------------------------------------------------
# Autouse fixture: redirect settings to tmp_path
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def redirect_settings(tmp_path, monkeypatch):
    """Redirect SETTINGS_PATH to a temporary file for all tests."""
    fake_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_path)
    return fake_path


# ---------------------------------------------------------------------------
# Pattern / existence tests
# ---------------------------------------------------------------------------


def test_settings_sync_interval_constant_exists():
    """SETTINGS_SYNC_INTERVAL constant must exist in main module."""
    assert hasattr(main_mod, "SETTINGS_SYNC_INTERVAL")


def test_settings_sync_interval_is_15():
    """SETTINGS_SYNC_INTERVAL must equal 15 (15 * 2s = ~30s)."""
    assert main_mod.SETTINGS_SYNC_INTERVAL == 15


def test_settings_sync_counter_exists():
    """_settings_sync_counter module-level variable must exist in main module."""
    assert hasattr(main_mod, "_settings_sync_counter")


def test_sync_settings_with_remotes_is_callable():
    """_sync_settings_with_remotes must be an async callable in main module."""
    assert hasattr(main_mod, "_sync_settings_with_remotes")
    assert asyncio.iscoroutinefunction(main_mod._sync_settings_with_remotes)


# ---------------------------------------------------------------------------
# Behaviour tests: sync logic with mocked HTTP
#
# Patch at the main_mod level (not settings_mod) because the functions are
# imported into muxplex.main's namespace via "from muxplex.settings import ...".
# ---------------------------------------------------------------------------


async def test_sync_adopts_newer_remote_settings():
    """When remote timestamp is newer, apply_synced_settings is called with remote data."""
    local_ts = 100.0
    remote_ts = 200.0
    remote_settings = {"fontSize": 18, "sort_order": "alpha"}

    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = {
        "settings": remote_settings,
        "settings_updated_at": remote_ts,
    }
    get_resp.raise_for_status = MagicMock()

    http_client = MagicMock()
    http_client.get = AsyncMock(return_value=get_resp)
    http_client.put = AsyncMock()

    remote_config = {
        "remote_instances": [{"url": "http://remote1:8088", "key": "testkey"}]
    }

    with (
        patch.object(main_mod, "get_syncable_settings") as mock_get_sync,
        patch.object(main_mod, "apply_synced_settings") as mock_apply,
    ):
        mock_get_sync.return_value = {
            "fontSize": 14,
            "sort_order": "manual",
            "settings_updated_at": local_ts,
        }

        await main_mod._sync_settings_with_remotes(remote_config, http_client)

        mock_apply.assert_called_once_with(remote_settings, remote_ts)
        http_client.put.assert_not_called()


async def test_sync_pushes_local_to_older_remote():
    """When local timestamp is newer, PUT is called to push local settings to remote."""
    local_ts = 200.0
    remote_ts = 100.0
    local_syncable = {
        "fontSize": 16,
        "sort_order": "alpha",
        "settings_updated_at": local_ts,
    }

    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = {
        "settings": {"fontSize": 14},
        "settings_updated_at": remote_ts,
    }
    get_resp.raise_for_status = MagicMock()

    put_resp = MagicMock()
    put_resp.status_code = 200

    http_client = MagicMock()
    http_client.get = AsyncMock(return_value=get_resp)
    http_client.put = AsyncMock(return_value=put_resp)

    remote_config = {
        "remote_instances": [{"url": "http://remote1:8088", "key": "testkey"}]
    }

    with (
        patch.object(main_mod, "get_syncable_settings") as mock_get_sync,
        patch.object(main_mod, "apply_synced_settings") as mock_apply,
    ):
        mock_get_sync.return_value = local_syncable

        await main_mod._sync_settings_with_remotes(remote_config, http_client)

        mock_apply.assert_not_called()
        http_client.put.assert_called_once()
        call_kwargs = http_client.put.call_args
        assert "/api/settings/sync" in call_kwargs[0][0]
        payload = call_kwargs[1]["json"]
        assert payload["settings_updated_at"] == local_ts
        assert "fontSize" in payload["settings"]


async def test_sync_skips_equal_timestamps():
    """When timestamps are equal, neither apply_synced_settings nor PUT is called."""
    equal_ts = 100.0

    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = {
        "settings": {"fontSize": 14},
        "settings_updated_at": equal_ts,
    }
    get_resp.raise_for_status = MagicMock()

    http_client = MagicMock()
    http_client.get = AsyncMock(return_value=get_resp)
    http_client.put = AsyncMock()

    remote_config = {
        "remote_instances": [{"url": "http://remote1:8088", "key": "testkey"}]
    }

    with (
        patch.object(main_mod, "get_syncable_settings") as mock_get_sync,
        patch.object(main_mod, "apply_synced_settings") as mock_apply,
    ):
        mock_get_sync.return_value = {"fontSize": 14, "settings_updated_at": equal_ts}

        await main_mod._sync_settings_with_remotes(remote_config, http_client)

        mock_apply.assert_not_called()
        http_client.put.assert_not_called()


async def test_sync_handles_404_gracefully():
    """A 404 from the remote (older muxplex, no sync endpoint) is silently skipped."""
    get_resp = MagicMock()
    get_resp.status_code = 404

    http_client = MagicMock()
    http_client.get = AsyncMock(return_value=get_resp)
    http_client.put = AsyncMock()

    remote_config = {
        "remote_instances": [{"url": "http://remote1:8088", "key": "testkey"}]
    }

    with (
        patch.object(main_mod, "get_syncable_settings") as mock_get_sync,
        patch.object(main_mod, "apply_synced_settings") as mock_apply,
    ):
        mock_get_sync.return_value = {"fontSize": 14, "settings_updated_at": 100.0}

        # Must not raise
        await main_mod._sync_settings_with_remotes(remote_config, http_client)

        mock_apply.assert_not_called()
        http_client.put.assert_not_called()


async def test_sync_handles_connection_error_gracefully():
    """Connection errors during sync are caught and logged, not re-raised."""
    http_client = MagicMock()
    http_client.get = AsyncMock(side_effect=ConnectionError("timeout"))
    http_client.put = AsyncMock()

    remote_config = {
        "remote_instances": [{"url": "http://remote1:8088", "key": "testkey"}]
    }

    with (
        patch.object(main_mod, "get_syncable_settings") as mock_get_sync,
        patch.object(main_mod, "apply_synced_settings") as mock_apply,
    ):
        mock_get_sync.return_value = {"fontSize": 14, "settings_updated_at": 100.0}

        # Must not raise even with connection error
        await main_mod._sync_settings_with_remotes(remote_config, http_client)

        mock_apply.assert_not_called()


async def test_sync_skips_remote_with_no_url():
    """Remotes without a URL are silently skipped (no HTTP call made)."""
    http_client = MagicMock()
    http_client.get = AsyncMock()
    http_client.put = AsyncMock()

    remote_config = {"remote_instances": [{"url": "", "key": "somekey"}]}

    with patch.object(main_mod, "get_syncable_settings") as mock_get_sync:
        mock_get_sync.return_value = {"fontSize": 14, "settings_updated_at": 100.0}

        await main_mod._sync_settings_with_remotes(remote_config, http_client)

        http_client.get.assert_not_called()
        http_client.put.assert_not_called()


# ---------------------------------------------------------------------------
# Pattern test: PUT response is checked via raise_for_status()
# ---------------------------------------------------------------------------


def test_sync_put_response_calls_raise_for_status():
    """_sync_settings_with_remotes must capture the PUT response and call raise_for_status() on it."""
    import inspect

    source = inspect.getsource(main_mod._sync_settings_with_remotes)
    # The PUT response must be assigned to a variable (not fire-and-forget)
    assert "put_resp" in source, (
        "_sync_settings_with_remotes must capture the PUT response in put_resp "
        "so errors (401, 500) are not silently swallowed"
    )
    # raise_for_status() must be called on the captured response
    assert "raise_for_status" in source, (
        "_sync_settings_with_remotes must call raise_for_status() on the PUT response "
        "so non-2xx errors propagate to the outer exception handler"
    )
