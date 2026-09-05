"""A key REWRITE must not be resurrected by a peer (muxplex-w6g).

muxplex-npg made federation sync MERGE `views` per view and per member,
using an LWW-Element-Set whose tombstones are DERIVED from
`settings["views_changed_at"]` (see views.py's "Federation merge of `views`"
header). Under that model an absent member with NO stamp reads as "this
device never knew about it", and add-wins keeps it.

Two paths REWRITE a session key inside `views[*].sessions` -- they remove one
key and add another -- without stamping either side:

  1. `main._migrate_session_name`  -- a rename rewrites `<device>:<old>` to
     `<device>:<new>`.
  2. `views.normalize_session_keys` -- a legacy bare-name entry is upgraded
     to canonical `<device_id>:<name>` form.

The peer still holds the OLD key. Unstamped, our removal reads as ignorance,
the merge resurrects it, and the view carries a dead key until
`prune_stale_keys` finally removes it (24h by default).

Every behavioural test here drives the REAL `main._sync_settings_with_remotes()`
against a stubbed peer with the real settings file (redirected to tmp)
underneath -- the same discipline as test_federation_views_merge.py, and for
the same reason: the timestamp comparison that decides whether the merge runs
at all lives in the caller.

The safety cases matter as much as the bug repros. A rewrite is an INFERENCE,
not a user decision, and a tombstone propagates as a real deletion -- so these
also pin the boundary of what a rewrite is allowed to retire:

  * `test_rename_does_not_retire_another_devices_same_named_pin`
  * `test_bare_key_live_on_a_known_peer_is_not_retired`
  * `test_normalize_without_peer_evidence_stamps_nothing`
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

import muxplex.main as main_mod
import muxplex.settings as settings_mod
import muxplex.views as views_mod

# Fixed stamps. "OLD" is what both devices agreed on before the rewrite; the
# rewrite itself stamps with real wall-clock time, which is far larger.
OLD = 900.0
LOCAL_EDIT = 1000.0
PEER_EDIT = 1500.0
PEER_SETTINGS_TS = 2000.0

LOCAL_DEVICE = "dev-a"
REMOTE_URL = "http://peer.invalid:8088"


@pytest.fixture(autouse=True)
def redirect_settings(tmp_path, monkeypatch):
    """Redirect SETTINGS_PATH to a temporary file for every test here.

    conftest.py enforces this globally already; kept local so this file is
    safe read in isolation, exactly like the sibling merge tests.
    """
    fake_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_path)
    return fake_path


def _seed_local(views: list, views_changed_at: dict, **extra) -> dict:
    """Write this device's starting settings and return them."""
    current = settings_mod.load_settings()
    current["views"] = views
    current["views_changed_at"] = views_changed_at
    current["settings_updated_at"] = LOCAL_EDIT
    current["views_updated_at"] = LOCAL_EDIT
    current.update(extra)
    settings_mod.save_settings(current)
    return current


def _peer(views: list, views_changed_at: dict):
    """A stubbed peer serving one GET /api/settings/sync response."""
    payload = {
        "settings": {"views": views},
        "settings_updated_at": PEER_SETTINGS_TS,
        "views_updated_at": PEER_EDIT,
        "views_changed_at": views_changed_at,
    }

    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = payload
    get_resp.raise_for_status = MagicMock()

    put_resp = MagicMock()
    put_resp.status_code = 200
    put_resp.json.return_value = {}
    put_resp.raise_for_status = MagicMock()

    client = MagicMock()
    client.get = AsyncMock(return_value=get_resp)
    client.put = AsyncMock(return_value=put_resp)
    return client


def _remote_config() -> dict:
    return {"remote_instances": [{"url": REMOTE_URL, "key": "testkey"}]}


def _members(settings: dict, view_name: str) -> list:
    for view in settings.get("views") or []:
        if view.get("name") == view_name:
            return list(view.get("sessions") or [])
    raise AssertionError(
        f"view {view_name!r} is missing from {settings.get('views')!r}"
    )


def _rename(settings: dict, old_name: str, new_name: str) -> None:
    """Run the REAL rename migration over *settings*, then persist it.

    Deliberately the real `_migrate_session_name` rather than a hand-written
    "views array with the new key in it" -- the stamping under test lives in
    that function, and a hand-built fixture would test nothing.
    """
    main_mod._migrate_session_name(
        {},  # state.json: bell / session_order / followups
        settings,
        {},  # manifest (returned, not mutated)
        {},  # pruning.json
        old_name,
        new_name,
        LOCAL_DEVICE,
    )
    settings_mod.save_settings(settings)


# ---------------------------------------------------------------------------
# The reported symptom: rename here, the old pin returns from the peer
# ---------------------------------------------------------------------------


async def test_rename_here_is_not_resurrected_by_a_peer_holding_the_old_key():
    """A session renamed on THIS device does not come back under its old key.

    Device A renames `work` to `work-ci`. Device B has not heard about it and
    still pins `dev-a:work`. The rewrite removed `dev-a:work` here -- but
    until it is STAMPED, the merge cannot tell that removal from "device A
    never knew about this pin", so add-wins drags the dead key back.

    Against the unstamped code this fails with the reported symptom: the view
    carries `dev-a:work` again, matching no live session, until the stale-key
    prune eventually removes it.
    """
    settings = _seed_local(
        [{"name": "Work", "sessions": ["dev-a:work"]}],
        {"Work": {"at": None, "members": {"dev-a:work": OLD}}},
    )
    _rename(settings, "work", "work-ci")

    client = _peer(
        [{"name": "Work", "sessions": ["dev-a:work"]}],
        {"Work": {"at": None, "members": {"dev-a:work": OLD}}},
    )

    await main_mod._sync_settings_with_remotes(_remote_config(), client)

    result = settings_mod.load_settings()
    assert _members(result, "Work") == ["dev-a:work-ci"], (
        "the renamed-away key was resurrected by the peer -- a rename on one "
        "device leaves a dead pin on the other until it is pruned"
    )


async def test_bare_key_upgraded_here_is_not_resurrected_by_a_peer():
    """The same holds for a legacy bare-name entry upgraded to canonical form.

    `normalize_session_keys` rewrites `work` to `dev-a:work`. A peer that has
    not normalized yet still sends `work`, and the unstamped removal reads as
    ignorance, so the legacy entry is re-added on every cycle.

    `remote_live_names=set()` is the caller vouching for what it knows: no
    device currently known to this one has a live session named `work`, so
    the bare entry unambiguously denotes OUR session and is ours to retire.
    That evidence is what makes the tombstone safe -- see
    `test_bare_key_live_on_a_known_peer_is_not_retired` for the other side.
    """
    settings = _seed_local(
        [{"name": "Work", "sessions": ["work"]}],
        {"Work": {"at": None, "members": {"work": OLD}}},
    )
    views_mod.normalize_session_keys(
        settings,
        [{"name": "work", "sessionKey": f"{LOCAL_DEVICE}:work"}],
        remote_live_names=set(),
    )
    settings_mod.save_settings(settings)

    client = _peer(
        [{"name": "Work", "sessions": ["work"]}],
        {"Work": {"at": None, "members": {"work": OLD}}},
    )

    await main_mod._sync_settings_with_remotes(_remote_config(), client)

    result = settings_mod.load_settings()
    assert _members(result, "Work") == ["dev-a:work"], (
        "the legacy bare-name entry was resurrected by the peer, so the view "
        "carries both forms of the same pin indefinitely"
    )


# ---------------------------------------------------------------------------
# The boundary: what a rewrite is NOT allowed to retire
#
# A rewrite is an inference this device made about its OWN keyspace. It must
# never propagate as a deletion of a pin that names a DIFFERENT session
# somewhere else. These three pass before and after the fix by design -- they
# are the guard rails on it, not repros.
# ---------------------------------------------------------------------------


async def test_rename_does_not_retire_another_devices_same_named_pin():
    """Renaming `dev-a:work` leaves `dev-b:work` completely alone.

    Same bare name, different device -- a different session. `<device>:<name>`
    is what makes the rename tombstone provably safe: only device A can own
    `dev-a:work`, so retiring it is a statement about A's own keyspace, not
    an inference about B's.
    """
    settings = _seed_local(
        [{"name": "Work", "sessions": ["dev-a:work", "dev-b:work"]}],
        {
            "Work": {
                "at": None,
                "members": {"dev-a:work": OLD, "dev-b:work": OLD},
            }
        },
    )
    _rename(settings, "work", "work-ci")

    client = _peer(
        [{"name": "Work", "sessions": ["dev-a:work", "dev-b:work"]}],
        {
            "Work": {
                "at": None,
                "members": {"dev-a:work": OLD, "dev-b:work": OLD},
            }
        },
    )

    await main_mod._sync_settings_with_remotes(_remote_config(), client)

    members = _members(settings_mod.load_settings(), "Work")
    assert "dev-b:work" in members, (
        "renaming this device's session deleted a DIFFERENT device's pin that "
        "merely shared the bare name"
    )
    assert "dev-a:work-ci" in members
    assert "dev-a:work" not in members


async def test_bare_key_live_on_a_known_peer_is_not_retired():
    """A bare name another known device is also running is NOT tombstoned.

    A bare entry has no owner: `filter_visible` matches it by NAME against
    every device's sessions, so `work` means "any live session called work",
    including the peer's own. Upgrading it here narrows it to ours -- fine
    locally, but propagating that as a deletion would unpin the peer's own
    live session, which the user never asked for.

    So the retirement is stamped only when the caller can show no other known
    device is running that name. Here one is, so nothing is stamped and the
    peer's entry survives the merge -- npg's conservative outcome (an extra
    redundant entry) rather than a lost pin.
    """
    settings = _seed_local(
        [{"name": "Work", "sessions": ["work"]}],
        {"Work": {"at": None, "members": {"work": OLD}}},
    )
    views_mod.normalize_session_keys(
        settings,
        [{"name": "work", "sessionKey": f"{LOCAL_DEVICE}:work"}],
        remote_live_names={"work"},
    )
    settings_mod.save_settings(settings)

    client = _peer(
        [{"name": "Work", "sessions": ["work"]}],
        {"Work": {"at": None, "members": {"work": OLD}}},
    )

    await main_mod._sync_settings_with_remotes(_remote_config(), client)

    members = _members(settings_mod.load_settings(), "Work")
    assert "work" in members, (
        "a bare name that a known peer is ALSO running was retired fleet-wide "
        "-- that deletes the peer's pin for its own live session"
    )
    assert "dev-a:work" in members


async def test_normalize_without_peer_evidence_stamps_nothing():
    """Omitting `remote_live_names` keeps npg's conservative no-stamp behavior.

    Only the poll cycle can vouch for what other devices are running. Every
    other caller (and every pre-existing one) passes nothing, and gets the
    pre-w6g outcome: the upgrade happens locally, no tombstone is written,
    and a peer may still resurrect the legacy entry. Not stamping can only
    ever leave an extra entry; stamping without evidence can lose a real pin.
    """
    settings = _seed_local(
        [{"name": "Work", "sessions": ["work"]}],
        {"Work": {"at": None, "members": {"work": OLD}}},
    )
    views_mod.normalize_session_keys(
        settings, [{"name": "work", "sessionKey": f"{LOCAL_DEVICE}:work"}]
    )
    settings_mod.save_settings(settings)

    client = _peer(
        [{"name": "Work", "sessions": ["work"]}],
        {"Work": {"at": None, "members": {"work": OLD}}},
    )

    await main_mod._sync_settings_with_remotes(_remote_config(), client)

    assert "work" in _members(settings_mod.load_settings(), "Work")


# ---------------------------------------------------------------------------
# Shape of what the rewrite actually records
# ---------------------------------------------------------------------------


def test_rename_stamps_both_sides_of_the_rewrite():
    """The rewrite records a removal AND an addition, in npg's own map.

    No third state: the tombstone a rewrite writes is the same one float per
    element `record_views_change` writes, read by the same `_survives`.
    """
    settings = _seed_local(
        [{"name": "Work", "sessions": ["dev-a:work"]}],
        {"Work": {"at": None, "members": {"dev-a:work": OLD}}},
    )
    _rename(settings, "work", "work-ci")

    members = settings["views_changed_at"]["Work"]["members"]
    assert members["dev-a:work"] > OLD, "the retired key was not stamped"
    assert members["dev-a:work-ci"] > OLD, "the new key was not stamped"


def test_rename_leaves_views_alone_when_no_pin_moved():
    """A rename of an unpinned session writes no stamps at all.

    `record_views_change` diffs; it does not back-fill. A session nobody
    pinned has no presence change to record, and inventing one would claim
    every existing pin changed at this instant.
    """
    settings = _seed_local(
        [{"name": "Work", "sessions": ["dev-a:other"]}],
        {"Work": {"at": None, "members": {"dev-a:other": OLD}}},
    )
    _rename(settings, "work", "work-ci")

    assert settings["views_changed_at"] == {
        "Work": {"at": None, "members": {"dev-a:other": OLD}}
    }
