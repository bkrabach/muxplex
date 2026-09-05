"""Federation sync MERGES `views` instead of picking a side (muxplex-npg).

The bug these tests pin: `_sync_settings_with_remotes` adopted a peer's
`views` array WHOLESALE whenever that peer looked newer. A session pinned on
this device seconds earlier was silently replaced by the peer's older
definition of the same view — no error, no log, the pin simply gone on the
next render. `views_updated_at` narrowed the window; it did not close it,
because it still resolved the conflict by choosing one array over the other.

Every behavioural test here drives the REAL
`main._sync_settings_with_remotes()` against a stubbed peer, with the real
settings file (redirected to tmp) underneath — deliberately NOT
`apply_synced_settings()` in isolation. The timestamp comparison that decides
whether the merge runs at all lives in the caller, and that is where the bug
lived; a test of the inner function alone would sail straight past it.

Both directions are covered, because a merge that only unions is not a fix:
  * `test_pin_added_here_survives_a_newer_peer` — additions survive.
  * `test_deletion_here_is_not_resurrected_by_a_peer_that_still_lists_it`
  * `test_deletion_on_the_peer_is_honoured_here`
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

import muxplex.main as main_mod
import muxplex.settings as settings_mod
import muxplex.views as views_mod

# Fixed stamps: "old" is what both devices agreed on before the race, "new"
# is an edit made inside one sync window. Real code uses time.time(); these
# are only ever compared against each other.
OLD = 900.0
LOCAL_EDIT = 1000.0
PEER_EDIT = 1500.0
PEER_SETTINGS_TS = 2000.0

REMOTE_URL = "http://peer.invalid:8088"


@pytest.fixture(autouse=True)
def redirect_settings(tmp_path, monkeypatch):
    """Redirect SETTINGS_PATH to a temporary file for every test here.

    conftest.py already enforces this globally (see its module docstring —
    a test that writes the real ~/.config/muxplex/settings.json destroyed a
    production config once); this is the same belt-and-braces the sibling
    sync tests use, kept local so this file is safe read in isolation.
    """
    fake_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", fake_path)
    return fake_path


def _seed_local(
    views: list,
    views_changed_at: dict,
    *,
    settings_updated_at: float = LOCAL_EDIT,
    views_updated_at: float = LOCAL_EDIT,
    **extra,
) -> dict:
    """Write this device's starting settings and return them."""
    current = settings_mod.load_settings()
    current["views"] = views
    current["views_changed_at"] = views_changed_at
    current["settings_updated_at"] = settings_updated_at
    current["views_updated_at"] = views_updated_at
    current.update(extra)
    settings_mod.save_settings(current)
    return current


def _peer(
    views: list,
    views_changed_at: dict | None,
    *,
    settings_updated_at: float = PEER_SETTINGS_TS,
    views_updated_at: float | None = PEER_EDIT,
    settings_extra: dict | None = None,
):
    """A stubbed peer serving one GET /api/settings/sync response.

    `views_changed_at=None` models a peer that predates the merge entirely —
    the field is omitted from the wire, not sent as null.
    """
    payload: dict = {
        "settings": {"views": views, **(settings_extra or {})},
        "settings_updated_at": settings_updated_at,
    }
    if views_updated_at is not None:
        payload["views_updated_at"] = views_updated_at
    if views_changed_at is not None:
        payload["views_changed_at"] = views_changed_at

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


# ---------------------------------------------------------------------------
# The reported symptom: a pin that just landed, gone ~30s later
# ---------------------------------------------------------------------------


async def test_pin_added_here_survives_a_newer_peer():
    """A pin made on THIS device survives a sync from a peer that looks newer.

    The peer knows the same view but never heard about our pin, and its
    `settings_updated_at` is newer for an unrelated reason (a fontSize edit,
    carried in the payload so the test can prove the sync actually ran
    rather than passing because nothing happened).

    Against the pre-merge code this fails: `views` is replaced wholesale and
    `dev-a:alpha` is gone.
    """
    _seed_local(
        [{"name": "Work", "sessions": ["dev-a:alpha"]}],
        {"Work": {"at": None, "members": {"dev-a:alpha": LOCAL_EDIT}}},
        fontSize=14,
    )
    client = _peer(
        [{"name": "Work", "sessions": ["dev-b:beta"]}],
        {"Work": {"at": None, "members": {"dev-b:beta": OLD}}},
        settings_extra={"fontSize": 18},
    )

    await main_mod._sync_settings_with_remotes(_remote_config(), client)

    result = settings_mod.load_settings()
    assert "dev-a:alpha" in _members(result, "Work"), (
        "the pin made on this device was destroyed by the peer's older view"
    )
    assert "dev-b:beta" in _members(result, "Work"), "the peer's pin was dropped"
    assert result["fontSize"] == 18, "the sync did not actually run"


async def test_pin_added_here_survives_a_peer_that_has_never_seen_the_view():
    """A view the peer has never heard of is not deleted by syncing with it."""
    _seed_local(
        [
            {"name": "Work", "sessions": ["dev-a:alpha"]},
            {"name": "Personal", "sessions": ["dev-a:gamma"]},
        ],
        {
            "Work": {"at": None, "members": {"dev-a:alpha": OLD}},
            "Personal": {"at": LOCAL_EDIT, "members": {"dev-a:gamma": LOCAL_EDIT}},
        },
    )
    client = _peer(
        [{"name": "Work", "sessions": ["dev-a:alpha"]}],
        {"Work": {"at": None, "members": {"dev-a:alpha": OLD}}},
    )

    await main_mod._sync_settings_with_remotes(_remote_config(), client)

    result = settings_mod.load_settings()
    assert [v["name"] for v in result["views"]] == ["Work", "Personal"]
    assert _members(result, "Personal") == ["dev-a:gamma"]


# ---------------------------------------------------------------------------
# The other direction: a merge that only unions resurrects deletions
# ---------------------------------------------------------------------------


async def test_deletion_here_is_not_resurrected_by_a_peer_that_still_lists_it():
    """A member deleted HERE stays deleted, even though the peer still lists it.

    This is the case a naive union gets wrong, and the reason this item
    needed a representation for deletion at all: to the peer's array,
    "deleted here" and "never seen here" look identical.

    Against the pre-merge code this ALSO fails — for the opposite reason:
    the newer peer's array wins wholesale and drags `dev-b:beta` back.
    """
    _seed_local(
        [{"name": "Work", "sessions": ["dev-a:alpha"]}],
        {
            "Work": {
                "at": None,
                # beta was pinned long ago and unpinned here just now.
                "members": {"dev-a:alpha": OLD, "dev-b:beta": PEER_EDIT + 100.0},
            }
        },
    )
    client = _peer(
        [{"name": "Work", "sessions": ["dev-a:alpha", "dev-b:beta"]}],
        {"Work": {"at": None, "members": {"dev-a:alpha": OLD, "dev-b:beta": OLD}}},
    )

    await main_mod._sync_settings_with_remotes(_remote_config(), client)

    result = settings_mod.load_settings()
    assert "dev-b:beta" not in _members(result, "Work"), (
        "a member deleted on this device was resurrected by the peer"
    )
    assert "dev-a:alpha" in _members(result, "Work")


async def test_deletion_on_the_peer_is_honoured_here():
    """A member the PEER deleted is removed here, not kept by add-wins bias.

    Four members, not two: dropping one of two would trip the pre-existing
    destructive-write backstop (>= 50% of member entries removed), which
    runs on the merged array exactly as it used to run on the incoming one.
    """
    keys = ["dev-a:alpha", "dev-a:gamma", "dev-a:delta", "dev-b:beta"]
    _seed_local(
        [{"name": "Work", "sessions": list(keys)}],
        {"Work": {"at": None, "members": dict.fromkeys(keys, OLD)}},
    )
    client = _peer(
        [{"name": "Work", "sessions": keys[:-1]}],
        {
            "Work": {
                "at": None,
                "members": {**dict.fromkeys(keys, OLD), "dev-b:beta": PEER_EDIT},
            }
        },
    )

    await main_mod._sync_settings_with_remotes(_remote_config(), client)

    result = settings_mod.load_settings()
    assert _members(result, "Work") == keys[:-1]


async def test_repin_after_a_peer_deletion_wins():
    """Re-pinning here after the peer's deletion keeps the member.

    The deletion is not sticky forever — it loses to a later add, which is
    what makes "unpin on the phone, pin again on the laptop" behave.
    """
    _seed_local(
        [{"name": "Work", "sessions": ["dev-b:beta"]}],
        {"Work": {"at": None, "members": {"dev-b:beta": PEER_EDIT + 100.0}}},
    )
    client = _peer(
        [{"name": "Work", "sessions": []}],
        {"Work": {"at": None, "members": {"dev-b:beta": PEER_EDIT}}},
    )

    await main_mod._sync_settings_with_remotes(_remote_config(), client)

    assert _members(settings_mod.load_settings(), "Work") == ["dev-b:beta"]


async def test_view_deleted_here_is_not_resurrected_by_the_peer():
    """View-level deletion gets the same treatment as member-level."""
    _seed_local(
        [{"name": "Work", "sessions": ["dev-a:alpha"]}],
        {
            "Work": {"at": None, "members": {"dev-a:alpha": OLD}},
            "Retired": {"at": PEER_EDIT + 100.0, "members": {}},
        },
    )
    client = _peer(
        [
            {"name": "Work", "sessions": ["dev-a:alpha"]},
            {"name": "Retired", "sessions": ["dev-a:old"]},
        ],
        {
            "Work": {"at": None, "members": {"dev-a:alpha": OLD}},
            "Retired": {"at": OLD, "members": {"dev-a:old": OLD}},
        },
    )

    await main_mod._sync_settings_with_remotes(_remote_config(), client)

    result = settings_mod.load_settings()
    assert [v["name"] for v in result["views"]] == ["Work"]


# ---------------------------------------------------------------------------
# Convergence: the merge must not be a one-way trip
# ---------------------------------------------------------------------------


async def test_merge_that_contributed_makes_us_look_newer_so_we_push_back():
    """Holding membership the peer lacks must not leave both sides equal.

    Adopting the peer's `settings_updated_at` verbatim after a merge would
    park both devices on the same timestamp — "equal: no action" — and the
    peer would never learn about our pin.
    """
    _seed_local(
        [{"name": "Work", "sessions": ["dev-a:alpha"]}],
        {"Work": {"at": None, "members": {"dev-a:alpha": LOCAL_EDIT}}},
    )
    client = _peer(
        [{"name": "Work", "sessions": []}],
        {"Work": {"at": None, "members": {}}},
    )

    await main_mod._sync_settings_with_remotes(_remote_config(), client)

    assert settings_mod.load_settings()["settings_updated_at"] > PEER_SETTINGS_TS


async def test_merge_that_contributed_nothing_leaves_the_peer_timestamp_alone():
    """The converged case stays quiet — no bump, so no perpetual ping-pong."""
    _seed_local(
        [{"name": "Work", "sessions": ["dev-a:alpha"]}],
        {"Work": {"at": None, "members": {"dev-a:alpha": OLD}}},
    )
    client = _peer(
        [{"name": "Work", "sessions": ["dev-a:alpha"]}],
        {"Work": {"at": None, "members": {"dev-a:alpha": OLD}}},
    )

    await main_mod._sync_settings_with_remotes(_remote_config(), client)

    assert settings_mod.load_settings()["settings_updated_at"] == PEER_SETTINGS_TS


async def test_push_payload_carries_views_changed_at():
    """When we are the newer side, the stamps go out with the push.

    Without this the peer merges against no stamps at all and can never
    honour a deletion we made.
    """
    _seed_local(
        [{"name": "Work", "sessions": ["dev-a:alpha"]}],
        {"Work": {"at": None, "members": {"dev-a:alpha": LOCAL_EDIT}}},
        settings_updated_at=PEER_SETTINGS_TS + 100.0,
    )
    client = _peer(
        [{"name": "Work", "sessions": []}],
        {"Work": {"at": None, "members": {}}},
    )

    await main_mod._sync_settings_with_remotes(_remote_config(), client)

    client.put.assert_called_once()
    payload = client.put.call_args.kwargs["json"]
    assert payload["views_changed_at"] == {
        "Work": {"at": None, "members": {"dev-a:alpha": LOCAL_EDIT}}
    }
    # Metadata rides beside the settings blob, never inside it (same shape
    # as settings_updated_at/views_updated_at).
    assert "views_changed_at" not in payload["settings"]


# ---------------------------------------------------------------------------
# Backward compatibility: a peer that has never heard of any of this
# ---------------------------------------------------------------------------


async def test_legacy_peer_without_stamps_still_wins_wholesale():
    """A peer that omits `views_changed_at` keeps the pre-existing behavior.

    Not merely tolerated — UNCHANGED. Inventing stamps for a peer that
    cannot maintain them would let one legacy device's array masquerade as
    a set of deliberate decisions.
    """
    _seed_local(
        [{"name": "Work", "sessions": ["dev-a:alpha"]}],
        {"Work": {"at": None, "members": {"dev-a:alpha": LOCAL_EDIT}}},
    )
    client = _peer([{"name": "Work", "sessions": ["dev-b:beta"]}], None)

    await main_mod._sync_settings_with_remotes(_remote_config(), client)

    result = settings_mod.load_settings()
    assert _members(result, "Work") == ["dev-b:beta"]
    assert result["settings_updated_at"] == PEER_SETTINGS_TS


async def test_peer_with_empty_stamps_still_merges():
    """`{}` is a real signal ("I support this, nothing recorded yet"), not None.

    A peer freshly upgraded has an empty map; treating that as "legacy" would
    silently leave the fleet on wholesale replace for as long as nobody
    touched a view.
    """
    _seed_local(
        [{"name": "Work", "sessions": ["dev-a:alpha"]}],
        {"Work": {"at": None, "members": {"dev-a:alpha": LOCAL_EDIT}}},
    )
    client = _peer([{"name": "Work", "sessions": ["dev-b:beta"]}], {})

    await main_mod._sync_settings_with_remotes(_remote_config(), client)

    assert set(_members(settings_mod.load_settings(), "Work")) == {
        "dev-a:alpha",
        "dev-b:beta",
    }


async def test_malformed_peer_stamps_do_not_break_the_sync():
    """A garbage `views_changed_at` degrades to "no stamps", never a 500.

    Same defensive posture as the rest of views.py: one bad peer must not
    wedge fleet-wide settings sync.
    """
    _seed_local(
        [{"name": "Work", "sessions": ["dev-a:alpha"]}],
        {"Work": {"at": None, "members": {"dev-a:alpha": LOCAL_EDIT}}},
    )
    client = _peer(
        [{"name": "Work", "sessions": ["dev-b:beta"]}],
        {"Work": {"at": "yesterday", "members": {"dev-b:beta": True, 7: 1.0}}},
    )

    await main_mod._sync_settings_with_remotes(_remote_config(), client)

    assert set(_members(settings_mod.load_settings(), "Work")) == {
        "dev-a:alpha",
        "dev-b:beta",
    }


# ---------------------------------------------------------------------------
# The stamps themselves: derived server-side, never client-supplied
# ---------------------------------------------------------------------------


def test_patch_settings_stamps_only_what_moved():
    """`PATCH /api/settings` records the presence change, and only that."""
    _seed_local(
        [{"name": "Work", "sessions": ["dev-a:alpha"]}],
        {},
    )
    settings_mod.patch_settings(
        {"views": [{"name": "Work", "sessions": ["dev-a:alpha", "dev-b:beta"]}]}
    )

    stamps = settings_mod.load_settings()["views_changed_at"]
    assert set(stamps["Work"]["members"]) == {"dev-b:beta"}, (
        "an unchanged member was back-filled with a fresh stamp, which would "
        "claim it had just been pinned"
    )
    assert stamps["Work"]["at"] is None, "an unchanged view was stamped as new"


def test_patch_settings_stamps_a_removal_as_a_tombstone():
    # Three members, not two: removing one of two is a >= 50% member drop and
    # the pre-existing destructive-write backstop refuses the whole PATCH.
    keys = ["dev-a:alpha", "dev-a:gamma", "dev-b:beta"]
    _seed_local([{"name": "Work", "sessions": list(keys)}], {})
    settings_mod.patch_settings({"views": [{"name": "Work", "sessions": keys[:-1]}]})

    result = settings_mod.load_settings()
    assert "dev-b:beta" in result["views_changed_at"]["Work"]["members"]
    assert _members(result, "Work") == keys[:-1]


def test_patch_settings_refuses_client_supplied_stamps():
    """A client cannot forge a tombstone (or erase one) through PATCH.

    Forging one deletes a peer's pin fleet-wide; erasing one resurrects a
    deletion. No legitimate client has any reason to send this key.
    """
    _seed_local(
        [{"name": "Work", "sessions": ["dev-a:alpha"]}],
        {"Work": {"at": None, "members": {"dev-a:alpha": OLD}}},
    )
    settings_mod.patch_settings(
        {"views_changed_at": {"Work": {"at": None, "members": {"dev-a:alpha": 9e9}}}}
    )

    stamps = settings_mod.load_settings()["views_changed_at"]
    assert stamps["Work"]["members"]["dev-a:alpha"] == OLD


def test_prune_stamps_the_members_it_removes():
    """A stale-key prune must read as a deletion, not as ignorance.

    Otherwise the next merge resurrects every pruned key from a peer that
    hasn't pruned yet, and re-undoes the prune on every cycle.
    """
    settings = {
        "views": [{"name": "Work", "sessions": ["dev-a:alpha", "dev-a:dead"]}],
        "hidden_sessions": [],
        "views_changed_at": {},
    }
    state = {"first_missed_at": {"dev-a:dead": 0.0}}

    settings, _, changed = views_mod.prune_stale_keys(
        settings, {"dev-a:alpha"}, pruning_state=state, grace_seconds=1.0, now=5000.0
    )

    assert changed
    assert settings["views"][0]["sessions"] == ["dev-a:alpha"]
    assert settings["views_changed_at"]["Work"]["members"]["dev-a:dead"] == 5000.0


# ---------------------------------------------------------------------------
# merge_views itself: the properties the sync path relies on
# ---------------------------------------------------------------------------


def test_merge_is_commutative_on_membership():
    """Both devices reach the same membership independently.

    Order and attributes follow the authoritative side rather than being
    merged, so only membership is asserted here — that is exactly what
    `views_membership_signature` compares, and what convergence needs.
    """
    # A pinned alpha and has since unpinned `gone`; B never heard about
    # either, still lists `gone`, and has pinned beta of its own.
    a_views = [{"name": "Work", "sessions": ["alpha"]}]
    a_stamps = {"Work": {"at": None, "members": {"alpha": OLD, "gone": PEER_EDIT}}}
    b_views = [{"name": "Work", "sessions": ["gone", "beta"]}]
    b_stamps = {"Work": {"at": None, "members": {"gone": OLD, "beta": LOCAL_EDIT}}}

    ab, _ = views_mod.merge_views(a_views, a_stamps, b_views, b_stamps, now=5000.0)
    ba, _ = views_mod.merge_views(b_views, b_stamps, a_views, a_stamps, now=5000.0)

    assert views_mod.views_membership_signature(
        ab
    ) == views_mod.views_membership_signature(ba)
    assert set(ab[0]["sessions"]) == {"alpha", "beta"}


def test_merge_is_idempotent():
    """Re-merging a converged state changes nothing — no perpetual churn."""
    views = [{"name": "Work", "sessions": ["alpha"]}]
    stamps = {"Work": {"at": None, "members": {"alpha": OLD}}}

    once, once_stamps = views_mod.merge_views(views, stamps, views, stamps, now=5000.0)
    twice, twice_stamps = views_mod.merge_views(
        once, once_stamps, once, once_stamps, now=5000.0
    )

    assert once == twice
    assert once_stamps == twice_stamps


def test_merge_agrees_on_order_from_the_authoritative_side():
    """Both devices order the result the same way.

    If each kept its own order, every sync would see a difference, conclude
    it had something to contribute, and push — forever, on both devices.
    """
    a_views = [{"name": "Work", "sessions": []}, {"name": "Personal", "sessions": []}]
    b_views = [{"name": "Personal", "sessions": []}, {"name": "Work", "sessions": []}]

    a_result, _ = views_mod.merge_views(
        a_views,
        {},
        b_views,
        {},
        local_views_updated_at=OLD,
        incoming_views_updated_at=PEER_EDIT,
        now=5000.0,
    )
    b_result, _ = views_mod.merge_views(
        b_views,
        {},
        a_views,
        {},
        local_views_updated_at=PEER_EDIT,
        incoming_views_updated_at=OLD,
        now=5000.0,
    )

    assert (
        [v["name"] for v in a_result]
        == [v["name"] for v in b_result]
        == [
            "Personal",
            "Work",
        ]
    )


def test_merge_preserves_an_unmergeable_local_entry():
    """A malformed local entry has no identity to merge on — and is kept.

    Dropping it would be the exact silent destruction this code exists to
    stop, so it survives even though it cannot participate.
    """
    local = [{"name": "Work", "sessions": []}, {"sessions": ["orphan"]}]
    merged, _ = views_mod.merge_views(
        local, {}, [{"name": "Work", "sessions": []}], {}, now=5000.0
    )

    assert {"sessions": ["orphan"]} in merged


def test_merge_does_not_mutate_its_inputs():
    local = [{"name": "Work", "sessions": ["alpha"]}]
    incoming = [{"name": "Work", "sessions": ["beta"]}]

    views_mod.merge_views(local, {}, incoming, {}, now=5000.0)

    assert local == [{"name": "Work", "sessions": ["alpha"]}]
    assert incoming == [{"name": "Work", "sessions": ["beta"]}]


def test_tombstone_is_forgotten_after_the_ttl():
    """Bounded growth, with the cost stated: a device offline longer than the
    TTL, still holding a since-deleted pin, resurrects that one pin."""
    stamps = views_mod.record_views_change(
        [{"name": "Work", "sessions": ["alpha", "beta"]}],
        [{"name": "Work", "sessions": ["alpha"]}],
        {},
        now=1000.0,
    )
    assert "beta" in stamps["Work"]["members"]

    aged = views_mod.record_views_change(
        [{"name": "Work", "sessions": ["alpha"]}],
        [{"name": "Work", "sessions": ["alpha"]}],
        stamps,
        now=1000.0 + views_mod.VIEW_TOMBSTONE_TTL_SECONDS + 1.0,
    )
    # The whole entry may disappear: `alpha` never changed presence, so it
    # was never stamped, and an entry with no stamps at all says nothing.
    assert "beta" not in aged.get("Work", {}).get("members", {})


def test_legacy_local_member_loses_to_a_dated_peer_deletion():
    """An unstamped local member reads as "added long ago", not "added now".

    Without this a deletion could never reach a device still holding
    pre-feature data — the tombstone would lose to an unknown add forever.
    """
    merged, _ = views_mod.merge_views(
        [{"name": "Work", "sessions": ["alpha"]}],
        {},
        [{"name": "Work", "sessions": []}],
        {"Work": {"at": None, "members": {"alpha": PEER_EDIT}}},
        now=5000.0,
    )

    assert merged[0]["sessions"] == []


def test_unstamped_peer_absence_never_deletes():
    """The mirror of the case above: no stamp at all means "never knew"."""
    merged, _ = views_mod.merge_views(
        [{"name": "Work", "sessions": ["alpha"]}],
        {"Work": {"at": None, "members": {"alpha": LOCAL_EDIT}}},
        [{"name": "Work", "sessions": []}],
        {},
        now=5000.0,
    )

    assert merged[0]["sessions"] == ["alpha"]
