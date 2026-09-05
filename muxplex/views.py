"""
Views invariant enforcement, visibility filtering, validation, and stale-key
pruning for muxplex.

Schema v2 semantics (see docs/plans/2026-05-17-hidden-state-redesign-design.md):
- "hidden" is a property of a session, determined by membership in
  hidden_sessions. View membership and hidden state are orthogonal.
- A session key MAY appear in both hidden_sessions and one or more
  view.sessions. Lists are filtered at read time via `filter_visible`.
- The legacy mutual-exclusion invariant (`enforce_mutual_exclusion`) is
  retained as a backstop in v1 for mixed-version federation compatibility.
  It will be removed in Phase 3 once all peers report _schema_version >= 2.

Other invariants:
- View names are non-empty, max 30 chars, trimmed, unique, not reserved.
- Duplicate session keys within a view are deduplicated by
  `enforce_mutual_exclusion`.
"""

import fnmatch
import time
from typing import NamedTuple

RESERVED_VIEW_NAMES = frozenset({"all", "hidden"})
MAX_VIEW_NAME_LENGTH = 30

# Settings key for a view's auto-updating glob rules (docs/plans/2026-08-04-auto-views-plan.md §3.1).
# A view is optionally extended with {"match_names": [<glob>, ...]} -- patterns
# matched against the bare tmux session name (never a device-qualified key;
# see matches_name_pattern's docstring for why). Membership is the UNION of
# `sessions` (manual pins) and match_names (resolved live, every read) --
# never materialized back into `sessions` (see filter_visible/§2.5).
VIEW_RULE_KEY: str = "match_names"


# ---------------------------------------------------------------------------
# Destructive-write backstop (settings clobber incident, 2026-07)
#
# Real incident: `views` is replaced WHOLESALE by both settings write paths
# (PATCH /api/settings and federation sync). A client holding a stale
# in-memory copy of `views` -- a browser tab, a phone PWA, or a federation
# peer with a momentarily-fresher timestamp -- can PATCH/sync that stale
# copy back over the server's current state, destroying view definitions in
# one request. This happened twice: 7 of 8 views destroyed by a stale PWA
# tab, then a fleet-wide collapse to 1 view replicated via federation LWW.
#
# This is a single-sided backstop: it inspects what's ABOUT to be written
# and refuses catastrophic shrinkage, regardless of which writer (API PATCH,
# federation sync, internal code) is responsible and regardless of whether
# that writer's timestamp/precondition logic says the write should proceed.
# ---------------------------------------------------------------------------

# A write is catastrophic if ANY of these hold. Thresholds are deliberately
# conservative -- a real, intentional bulk deletion (e.g. an operator
# clearing out a handful of views) should also be rare enough that requiring
# `allow_destructive: true` (PATCH-path only; see settings.py) is a
# reasonable speed bump, not a workflow blocker.
DESTRUCTIVE_VIEW_DROP_RATIO = 0.5  # >= 50% of existing views removed
DESTRUCTIVE_VIEW_COLLAPSE_THRESHOLD = 1  # >1 views -> <= this many is destructive
DESTRUCTIVE_MEMBER_DROP_RATIO = 0.5  # >= 50% of total session-member entries removed


class ViewsDestructionAssessment(NamedTuple):
    """Result of assess_views_destruction(). `reason` is human-readable and
    safe to log or return in an API error body; empty when not destructive.
    """

    destructive: bool
    reason: str
    before_views: int
    after_views: int
    before_members: int
    after_members: int

    def as_counts_dict(self) -> dict:
        """Compact dict form for logging/API responses."""
        return {
            "before_views": self.before_views,
            "after_views": self.after_views,
            "before_members": self.before_members,
            "after_members": self.after_members,
        }


def _view_member_count(views: list) -> int:
    total = 0
    for v in views:
        if isinstance(v, dict):
            sessions = v.get("sessions")
            if isinstance(sessions, list):
                total += len(sessions)
            # Rules count as members too (docs/plans/2026-08-04-auto-views-plan.md §3.4): a
            # rule-bearing view with zero pins must not contribute 0 to the
            # backstop's total, or the DESTRUCTIVE_MEMBER_DROP_RATIO
            # protection weakens exactly as views migrate to rules -- a
            # stale client that PATCHes back `views` with every
            # match_names stripped would otherwise sail through. Count RAW
            # entries (including structurally invalid ones): the backstop
            # measures how much configuration is about to disappear, not
            # how much of it is valid.
            patterns = v.get(VIEW_RULE_KEY)
            if isinstance(patterns, list):
                total += len(patterns)
    return total


def assess_views_destruction(
    current_views: object, incoming_views: object
) -> ViewsDestructionAssessment:
    """Assess whether replacing `current_views` with `incoming_views` would be
    a catastrophic, likely-unintended shrinkage of view definitions.

    Pure and side-effect-free -- callers (settings.patch_settings,
    settings.apply_synced_settings) decide what to do with the result
    (reject the write, log, etc.).

    Robust to malformed input on EITHER side: a non-list `current_views` is
    treated as an empty starting point (nothing to lose -> never
    destructive); a non-list `incoming_views` (including None -- i.e. the
    key is absent or explicitly null) is treated as "not changing views",
    NEVER as "delete all", and is therefore never destructive. This means
    callers may invoke this function unconditionally without first checking
    whether the incoming payload actually intends to touch `views`.

    Catastrophic (destructive=True) when ANY of:
      1. Collapse: more than DESTRUCTIVE_VIEW_COLLAPSE_THRESHOLD views exist
         and the incoming count would drop to that threshold or below.
      2. Bulk view removal: the incoming view count is <= (1 -
         DESTRUCTIVE_VIEW_DROP_RATIO) of the current count.
      3. Bulk member removal: the incoming total session-member count
         (summed across all views) is <= (1 - DESTRUCTIVE_MEMBER_DROP_RATIO)
         of the current total.

    A single view deletion, or removing a handful of members from one view,
    stays well under all three thresholds and is never flagged.
    """
    current = current_views if isinstance(current_views, list) else []
    before_views = len(current)
    before_members = _view_member_count(current)

    if not isinstance(incoming_views, list):
        # Absent, None, or a garbage type: not a views-changing write at all.
        return ViewsDestructionAssessment(False, "", before_views, 0, before_members, 0)

    after_views = len(incoming_views)
    after_members = _view_member_count(incoming_views)

    if before_views == 0:
        # Nothing to lose.
        return ViewsDestructionAssessment(
            False, "", before_views, after_views, before_members, after_members
        )

    if (
        before_views > DESTRUCTIVE_VIEW_COLLAPSE_THRESHOLD
        and after_views <= DESTRUCTIVE_VIEW_COLLAPSE_THRESHOLD
    ):
        reason = (
            f"views would collapse from {before_views} to {after_views} "
            f"(<= collapse threshold {DESTRUCTIVE_VIEW_COLLAPSE_THRESHOLD})"
        )
        return ViewsDestructionAssessment(
            True, reason, before_views, after_views, before_members, after_members
        )

    if after_views <= before_views * (1 - DESTRUCTIVE_VIEW_DROP_RATIO):
        reason = (
            f"views would drop from {before_views} to {after_views} "
            f"(>= {DESTRUCTIVE_VIEW_DROP_RATIO:.0%} removed)"
        )
        return ViewsDestructionAssessment(
            True, reason, before_views, after_views, before_members, after_members
        )

    if before_members > 0 and after_members <= before_members * (
        1 - DESTRUCTIVE_MEMBER_DROP_RATIO
    ):
        reason = (
            f"view session-member entries would drop from {before_members} to "
            f"{after_members} (>= {DESTRUCTIVE_MEMBER_DROP_RATIO:.0%} removed)"
        )
        return ViewsDestructionAssessment(
            True, reason, before_views, after_views, before_members, after_members
        )

    return ViewsDestructionAssessment(
        False, "", before_views, after_views, before_members, after_members
    )


# ---------------------------------------------------------------------------
# Federation merge of `views` (LWW-Element-Set with derived tombstones)
#
# THE BUG THIS CLOSES. Federation sync used to resolve a `views` conflict by
# PICKING ONE SIDE: `apply_synced_settings()` replaced the whole array with
# the peer's. `views_updated_at` narrowed the window (an unrelated `fontSize`
# edit no longer wins a views race) but did NOT close it -- two devices that
# both edit views inside one ~30s sync window still race, and the loser's
# edit is silently destroyed. User-visible symptom: pin a session to a view,
# the CAS accepts it, the server reports success, and up to ~30s later a
# peer's older view definition replaces it. Nothing errors. The pin is
# simply gone on the next render.
#
# WHY A PLAIN MERGE IS NOT ENOUGH. Union the two arrays and additions
# survive -- but a member GENUINELY DELETED on device A is resurrected by
# device B, which still lists it. Under a set union, "absent because
# deleted" and "absent because never seen" are the same observation. No
# amount of arithmetic over the two arrays can tell them apart.
#
# WHY TIMESTAMPS ALONE ARE NOT ENOUGH EITHER (the tempting wrong answer):
# "keep the member if it was added after the other side's views_updated_at,
# drop it otherwise." That reads a timestamp as knowledge. B's array being
# written at T2 > T1 does not mean B had SEEN A's T1 addition -- if the two
# devices had not synced in between, B simply never knew. That is precisely
# the concurrent-edit case this code exists for, so timestamp inference is
# unsound exactly where it matters.
#
# WHAT WE DO INSTEAD -- an LWW-Element-Set whose tombstones are DERIVED:
#   * `views[i].sessions` stays the authoritative present-set. Unchanged
#     wire shape; the frontend needs no changes and never sends metadata.
#   * `settings["views_changed_at"]` records, per view and per member, the
#     moment its PRESENCE last changed (added OR removed):
#         {"<view name>": {"at": <float|None>,
#                          "members": {"<session key>": <float>}}}
#   * Presence is DERIVED, never stored: a key listed in `sessions` is
#     present; a key with a stamp but NOT in `sessions` is a tombstone; a
#     key with no stamp at all is "never seen / predates this feature".
#     One float per element, and the tombstone falls out for free.
#
# The server is the only writer of `views_changed_at`, and it derives every
# stamp by DIFFING what a write changed (see `record_views_change`). That is
# what makes this safe against a client that knows nothing about it: a stale
# PWA tab PATCHing an old array cannot drop the metadata, because it never
# sends it.
#
# COSTS, stated plainly (see also docs/API_SEMANTICS.md):
#   * Payload growth: one `"<key>": <float>` per pinned member (~35 bytes)
#     plus one per un-GC'd tombstone. A 10-view/100-member configuration
#     costs a few KB in settings.json and in every sync payload.
#   * Tombstone GC is time-based (VIEW_TOMBSTONE_TTL_SECONDS). A device
#     offline LONGER than the TTL, still holding a since-deleted pin, will
#     resurrect that one pin when it returns. Bounded and self-limiting: if
#     the underlying session no longer exists, `prune_stale_keys` removes
#     it again within `stale_key_grace_hours`. The alternative -- keeping
#     every deletion forever -- grows without bound, which is worse.
#   * Cross-device clock skew matters at member granularity now. This is
#     NOT a new trust requirement: `settings_updated_at`/`views_updated_at`
#     already arbitrate by wall clock. It is strictly finer-grained, so a
#     skewed peer now loses/wins ONE member instead of the whole array.
#   * `hidden_sessions` is deliberately NOT merged here -- it keeps the
#     existing `views_updated_at` LWW. Same bug class, separate item.
# ---------------------------------------------------------------------------

# How long a tombstone (a stamp for a key that is no longer present) is kept
# before garbage collection. Answers "when is it safe to forget a deletion?"
# -- strictly speaking never, without knowing every peer has seen it, which
# this design deliberately does not track. 30 days is chosen to be far longer
# than any plausible sync outage while still bounding growth; see the cost
# note above for exactly what a longer-than-TTL outage costs.
VIEW_TOMBSTONE_TTL_SECONDS: float = 30 * 24 * 60 * 60


def _as_timestamp(value: object) -> float | None:
    """Coerce a settings-file value to a float timestamp, or None.

    `bool` is excluded explicitly: it is a subclass of `int`, and a stray
    `true` in settings.json must read as "no stamp", never as `1.0` (which
    would be a timestamp in 1970 and would lose every comparison).
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _max_stamp(a: float | None, b: float | None) -> float | None:
    """Later of two optional stamps; None only when both are None."""
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def normalize_views_changed_at(raw: object) -> dict[str, dict]:
    """Return a well-formed `views_changed_at` map from arbitrary input.

    Shape: ``{view_name: {"at": float|None, "members": {key: float}}}``.

    Every entry that isn't structurally usable is dropped rather than
    raising -- this map is read on the poll cycle and on every sync, and a
    hand-edited or peer-supplied malformation must never 500 either path
    (same defensive posture as `view_patterns`/`matches_name_pattern`).
    Dropping a stamp is safe by construction: a missing stamp reads as
    "never seen", and the merge's add-wins bias then keeps the member.
    """
    out: dict[str, dict] = {}
    if not isinstance(raw, dict):
        return out
    for name, entry in raw.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        members: dict[str, float] = {}
        raw_members = entry.get("members")
        if isinstance(raw_members, dict):
            for key, stamp in raw_members.items():
                value = _as_timestamp(stamp)
                if isinstance(key, str) and value is not None:
                    members[key] = value
        out[name] = {"at": _as_timestamp(entry.get("at")), "members": members}
    return out


def _index_views(views: object) -> tuple[list[str], dict[str, dict]]:
    """Return (name order, name -> view dict) for MERGEABLE view entries.

    A view is mergeable iff it is a dict whose `name` is a str and is the
    first entry with that name. Everything else (a non-dict entry, a missing
    or non-str name, a duplicate name) has no identity to merge ON, so it is
    excluded here and preserved verbatim from the LOCAL side by
    `merge_views` -- never silently dropped, and never merged against the
    wrong peer entry.
    """
    order: list[str] = []
    index: dict[str, dict] = {}
    if not isinstance(views, list):
        return order, index
    for view in views:
        if not isinstance(view, dict):
            continue
        name = view.get("name")
        if not isinstance(name, str) or name in index:
            continue
        order.append(name)
        index[name] = view
    return order, index


def _view_members(view: dict | None) -> list[str]:
    """Ordered, de-duplicated `sessions` entries of one view (str only)."""
    if view is None:
        return []
    sessions = view.get("sessions")
    if not isinstance(sessions, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for key in sessions:
        if isinstance(key, str) and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _survives(
    local_present: bool,
    local_at: float | None,
    incoming_present: bool,
    incoming_at: float | None,
) -> bool:
    """Resolve one element's presence across the two sides.

    Present on both, or absent on both: no conflict. Otherwise exactly one
    side lists it, and the question is whether the OTHER side deleted it or
    never knew about it -- which is exactly what the stamps answer:

      * absent side has NO stamp -> it never knew -> KEEP (add-wins over
        ignorance; this is the pin-just-landed case).
      * present side has no stamp but the absent side has one -> our copy
        predates this feature and theirs is a real, dated deletion -> DROP
        (an unknown add time is "long ago"; otherwise a deletion could
        never propagate to a device holding legacy data).
      * both stamped -> later wins; a tie KEEPS. The tie-break is
        deliberate: a resurrected pin is a visible annoyance the user can
        undo, a lost pin is the silent data loss this whole mechanism
        exists to stop.
    """
    if local_present and incoming_present:
        return True
    if not local_present and not incoming_present:
        return False
    present_at = local_at if local_present else incoming_at
    absent_at = incoming_at if local_present else local_at
    if absent_at is None:
        return True
    if present_at is None:
        return False
    return present_at >= absent_at


def _merge_members(
    local_view: dict | None,
    incoming_view: dict | None,
    local_stamps: dict[str, float],
    incoming_stamps: dict[str, float],
    incoming_authoritative: bool,
) -> tuple[list[str], dict[str, float]]:
    """Merge one view's member list. Returns (members, member stamps).

    Order follows the authoritative side (see `merge_views`), then appends
    the other side's extras -- so both devices compute the SAME order and
    stop trading it back and forth forever. Stamps are carried for absent
    keys too -- that IS the tombstone -- and garbage-collected later by
    `_gc_views_changed_at`.
    """
    local_members = _view_members(local_view)
    incoming_members = _view_members(incoming_view)
    local_set = set(local_members)
    incoming_set = set(incoming_members)
    if incoming_authoritative:
        ordered = incoming_members + [k for k in local_members if k not in incoming_set]
    else:
        ordered = local_members + [k for k in incoming_members if k not in local_set]

    merged: list[str] = []
    for key in ordered:
        if _survives(
            key in local_set,
            local_stamps.get(key),
            key in incoming_set,
            incoming_stamps.get(key),
        ):
            merged.append(key)

    stamps: dict[str, float] = {}
    for key in set(local_stamps) | set(incoming_stamps) | local_set | incoming_set:
        stamp = _max_stamp(local_stamps.get(key), incoming_stamps.get(key))
        if stamp is not None:
            stamps[key] = stamp
    return merged, stamps


def _gc_views_changed_at(
    changed_at: dict[str, dict], views: object, now: float, ttl: float
) -> dict[str, dict]:
    """Drop expired tombstones and stamps that carry no information.

    A stamp for a still-present view/member is kept indefinitely (bounded by
    how much configuration actually exists). A stamp for an ABSENT one is a
    tombstone and is dropped once it is older than *ttl*. A view entry that
    is absent AND unstamped says nothing at all and is dropped immediately.
    """
    _, index = _index_views(views)
    out: dict[str, dict] = {}
    for name, entry in changed_at.items():
        at = entry.get("at")
        view = index.get(name)
        if view is None:
            # View tombstone: keep only while it can still out-vote a peer
            # that has not yet heard about the deletion.
            if at is not None and now - at <= ttl:
                out[name] = {"at": at, "members": {}}
            continue
        present = set(_view_members(view))
        members = {
            key: stamp
            for key, stamp in entry.get("members", {}).items()
            if key in present or now - stamp <= ttl
        }
        if at is None and not members:
            continue
        out[name] = {"at": at, "members": members}
    return out


def record_views_change(
    previous_views: object,
    current_views: object,
    changed_at: object,
    *,
    now: float | None = None,
    tombstone_ttl: float = VIEW_TOMBSTONE_TTL_SECONDS,
) -> dict[str, dict]:
    """Stamp every view/member whose PRESENCE changed between the two arrays.

    This is how `views_changed_at` is maintained: derived server-side by
    diffing what a write actually changed, never supplied by a client. A
    caller that replaces `views` wholesale (`PATCH /api/settings`, the
    stale-key prune) calls this with the array as it stood BEFORE the write
    and the array as it stands after.

    Elements that did not change presence keep whatever stamp they had --
    including none. An unstamped element is legitimate and means exactly
    "no presence change has been observed since this feature shipped"; it is
    never back-filled with `now`, which would falsely claim every existing
    view was created at this instant.

    Returns a NEW map (garbage-collected); does not mutate its arguments.
    """
    if now is None:
        now = time.time()
    _, previous_index = _index_views(previous_views)
    _, current_index = _index_views(current_views)
    meta = normalize_views_changed_at(changed_at)

    for name in set(previous_index) | set(current_index):
        entry = meta.setdefault(name, {"at": None, "members": {}})
        was_present = name in previous_index
        is_present = name in current_index
        if was_present != is_present:
            entry["at"] = now
        if not is_present:
            # The view's own tombstone covers its members; per-member stamps
            # for a deleted view would be dropped by GC anyway.
            continue
        before = set(_view_members(previous_index.get(name)))
        after = set(_view_members(current_index[name]))
        for key in before ^ after:
            entry["members"][key] = now

    return _gc_views_changed_at(meta, current_views, now, tombstone_ttl)


def merge_views(
    local_views: object,
    local_changed_at: object,
    incoming_views: object,
    incoming_changed_at: object,
    *,
    local_views_updated_at: float = 0.0,
    incoming_views_updated_at: float = 0.0,
    now: float | None = None,
    tombstone_ttl: float = VIEW_TOMBSTONE_TTL_SECONDS,
) -> tuple[list, dict[str, dict]]:
    """Merge a peer's `views` with ours. Returns (merged views, stamps).

    Pure and side-effect-free; neither input is mutated (view dicts are
    shallow-copied before `sessions` is replaced). Commutative and
    idempotent on the presence decision, so both devices reach the same
    answer independently and re-merging a converged state changes nothing.

    Presence of every view and every member is resolved by `_survives` --
    read that docstring; it is the entire semantic core. What is NOT
    resolved per-element, and deliberately so:

      * A view's OTHER attributes (`match_names`, and any field a future
        version adds) come wholesale from whichever side has the larger
        `views_updated_at` -- the "authoritative" side; tie -> local. Rule
        edits are not the silent data-loss case this exists to fix, and
        per-attribute stamps would multiply the payload cost for no
        reported symptom.
      * ORDER follows that same authoritative side, then appends the other
        side's extras. Order must not be merged element-wise, and it must
        not simply be "ours": if each device kept its own order, every sync
        would see the peer's array as different from its own merge result,
        conclude it had something to contribute, and push -- forever, every
        ~30s, on both devices. Both sides pick the same authority, so both
        compute the same order and the exchange settles.
      * A local entry with no mergeable identity (not a dict, no str name,
        or a duplicate name) is APPENDED verbatim at the end rather than
        dropped. There is nothing to merge it against and no meaningful
        position for it once the order comes from the peer, but dropping it
        would be the exact silent destruction this function exists to stop.
    """
    if now is None:
        now = time.time()
    local_order, local_index = _index_views(local_views)
    incoming_order, incoming_index = _index_views(incoming_views)
    local_meta = normalize_views_changed_at(local_changed_at)
    incoming_meta = normalize_views_changed_at(incoming_changed_at)
    incoming_authoritative = incoming_views_updated_at > local_views_updated_at

    merged_meta: dict[str, dict] = {}
    merged_views: dict[str, dict] = {}

    if incoming_authoritative:
        names = incoming_order + [n for n in local_order if n not in incoming_index]
    else:
        names = local_order + [n for n in incoming_order if n not in local_index]
    for name in names:
        local_view = local_index.get(name)
        incoming_view = incoming_index.get(name)
        local_entry = local_meta.get(name, {})
        incoming_entry = incoming_meta.get(name, {})
        local_at = local_entry.get("at")
        incoming_at = incoming_entry.get("at")

        members, member_stamps = _merge_members(
            local_view,
            incoming_view,
            local_entry.get("members", {}),
            incoming_entry.get("members", {}),
            incoming_authoritative,
        )

        if _survives(
            local_view is not None, local_at, incoming_view is not None, incoming_at
        ):
            base = incoming_view if incoming_authoritative and incoming_view else None
            if base is None:
                base = local_view if local_view is not None else incoming_view
            merged = dict(base or {})
            merged["name"] = name
            merged["sessions"] = members
            merged_views[name] = merged
            merged_meta[name] = {
                "at": _max_stamp(local_at, incoming_at),
                "members": member_stamps,
            }
        else:
            # Deleted. The view's tombstone is the whole record; per-member
            # stamps under it are meaningless and would only grow the payload.
            merged_meta[name] = {"at": _max_stamp(local_at, incoming_at), "members": {}}

    result: list = [merged_views[n] for n in names if n in merged_views]

    # Local entries with no mergeable identity survive at the end -- see the
    # docstring. Identified by object identity against what _index_views
    # accepted, so a duplicate-name entry is caught too.
    kept = {id(v) for v in local_index.values()}
    for view in local_views if isinstance(local_views, list) else []:
        if id(view) not in kept:
            result.append(view)

    return result, _gc_views_changed_at(merged_meta, result, now, tombstone_ttl)


def views_membership_signature(views: object) -> dict[str, frozenset]:
    """Return `{view name: frozenset(member keys)}` -- the part of `views`
    that `merge_views` actually decides.

    Used by `settings.apply_synced_settings()` to answer one question after a
    merge: does the merged state differ from what the PEER sent? If it does,
    this device holds membership information the peer does not, and must make
    itself look newer so the next cycle pushes it back -- otherwise the merge
    is a one-way trip, the peer never learns about our pin, and adopting the
    peer's `settings_updated_at` leaves the two stuck at equal timestamps
    ("no action") forever.

    Deliberately ignores order and every non-membership attribute, so a
    cosmetic difference never triggers a push that would bounce back.
    """
    _, index = _index_views(views)
    return {name: frozenset(_view_members(view)) for name, view in index.items()}


# ---------------------------------------------------------------------------
# Schema v2: visibility filtering (read-time)
# ---------------------------------------------------------------------------


def _key_of(session: dict) -> str:
    """Canonical key for a session dict: prefer `sessionKey`, fall back to `name`."""
    return session.get("sessionKey") or session.get("name") or ""


def is_hidden(key: str, settings: dict) -> bool:
    """Return True if the given key is in settings['hidden_sessions']."""
    return key in (settings.get("hidden_sessions") or [])


# ---------------------------------------------------------------------------
# Auto-updating views: glob rule matching (docs/plans/2026-08-04-auto-views-plan.md §2.1, §4, §5.1)
#
# Rules are matched against the bare tmux session name only -- never a
# device-qualified "<device_id>:<name>" key. The qualifier is a UUID
# (identity.load_device_id()), never something a user would type, and
# GET /api/sessions -- the payload most clients poll for membership -- never
# carries it in single-device mode. `amplifier-*` already means "on any
# device", which is the case that matters; a pattern containing ":" is
# rejected at validation time (validate_view_rules, rule R4) rather than
# silently matching nothing forever.
# ---------------------------------------------------------------------------


def matches_name_pattern(name: object, pattern: object) -> bool:
    """Return True if *name* matches the glob *pattern*.

    Matching is deliberately case-INSENSITIVE via explicit `.casefold()` on
    both sides followed by `fnmatch.fnmatchcase` -- NOT plain
    `fnmatch.fnmatch`, whose case-folding is a side effect of
    `os.path.normcase` and is therefore platform-dependent (a no-op on
    Linux, case-folding on macOS/Windows). Explicit casefold + fnmatchcase
    gives the same deterministic result on every platform muxplex runs on.

    This is the SAME technique as
    `terminal_input.session_matches_allowlist`, and is DELIBERATELY a
    separate implementation (docs/plans/2026-08-04-auto-views-plan.md §2.1): that function is the
    entire security boundary for the RCE-by-design `/input` endpoint;
    this one is a display filter. Two consumers with opposite failure
    requirements (fail-closed security vs. fail-loud display) must not
    share a mutable implementation -- a future tightening of the input
    fence must not silently change which sessions a view contains, and a
    future loosening for views must not silently widen an RCE fence.

    Non-`str` *name* or *pattern* returns False rather than raising -- a
    malformed settings.json must never 500 a poll cycle. Validity (as
    opposed to matchability) is reported separately by
    `validate_view_rules`, never inferred from a silent False here.
    """
    if not isinstance(name, str) or not isinstance(pattern, str):
        return False
    return fnmatch.fnmatchcase(name.casefold(), pattern.casefold())


def view_patterns(view: object) -> list[str]:
    """Return the structurally-valid `match_names` patterns of one view entry.

    `[]` unless *view* is a dict whose `match_names` is a list. From that
    list, keeps an entry iff it is a non-empty `str` containing no `":"`
    (see `validate_view_rules` rules R2-R4). Patterns are used VERBATIM --
    no trimming, no normalization (a leading space is a legitimate, if
    unusual, part of a session name).

    Order is preserved (file order). Invalid patterns are excluded from
    matching -- never silently widened to match everything, and never
    fatal. Mirrors `resolve_session_commands`'s "invalid entry is
    EXCLUDED, never silently degrades into the default." Everything
    dropped here is reported by `validate_view_rules`; nothing is dropped
    silently.
    """
    if not isinstance(view, dict):
        return []
    patterns = view.get(VIEW_RULE_KEY)
    if not isinstance(patterns, list):
        return []
    return [p for p in patterns if isinstance(p, str) and p and ":" not in p]


def view_names_for_session(session: dict, settings: dict) -> list[str]:
    """Return the resolved list of user-view names *session* belongs to.

    Order follows `settings["views"]`. Pure; no I/O.

    A session is a member of a view iff it is pinned (the existing
    dual-lookup against `sessionKey`/`name`, unchanged) OR its bare `name`
    matches one of the view's structurally-valid `match_names` patterns
    (see `view_patterns`/`matches_name_pattern`) -- a strict UNION, never a
    replacement (docs/plans/2026-08-04-auto-views-plan.md §2.2).

    Returns `[]` for an entry with a truthy `status` (federation status
    tiles are not sessions). Never returns "all" or "hidden" -- those are
    reserved pseudo-views, not entries in `settings["views"]`.

    Does NOT consider `hidden_sessions`: hidden is orthogonal (schema v2)
    and stays a separate, rule-free membership test every client already
    performs on its own.
    """
    if session.get("status"):
        return []
    name = session.get("name", "")
    names: list[str] = []
    for view in settings.get("views") or []:
        if not isinstance(view, dict):
            continue
        members = set(view.get("sessions") or [])
        pinned = _key_of(session) in members or name in members
        matched = pinned or any(
            matches_name_pattern(name, p) for p in view_patterns(view)
        )
        if matched:
            view_name = view.get("name")
            if isinstance(view_name, str):
                names.append(view_name)
    return names


def annotate_view_membership(sessions: list[dict], settings: dict) -> list[dict]:
    """Return a NEW list of NEW dicts, each with a `views` key added.

    `{**s, "views": view_names_for_session(s, settings)}` for every entry;
    status/tile entries get `"views": []` so no client has to null-check.

    Must NOT mutate its input, and this is not merely stylistic:
    `GET /api/federation/sessions` stores its tagged remote session dicts
    in `_federation_cache` (main.py) and re-serves those same objects on
    later cycles. In-place annotation would bake a point-in-time
    membership answer into the cache and serve it after the settings that
    produced it had changed. Building new dicts keeps the cache
    un-annotated so every read re-resolves membership against current
    settings.
    """
    return [{**s, "views": view_names_for_session(s, settings)} for s in sessions]


def validate_view_rules(views: object) -> list[str]:
    """Validate every view entry's `match_names`. Returns human-readable
    error strings (empty list = clean).

    Only `match_names` is inspected here -- nothing else about a `views`
    entry gains validation (docs/plans/2026-08-04-auto-views-plan.md §6.3): a non-dict entry, a
    missing `name`, a non-list `sessions`, etc. are all tolerated exactly
    as they are today, by the existing defensive `isinstance`/`.get()`
    reads throughout this module. `PATCH /api/settings` accepts those
    payloads today; rejecting them now would be a behavior change to
    previously-valid requests.

    Rules (each producing one error string and excluding exactly what it
    names -- R1 excludes the whole rule, R2-R4 exclude one pattern):
        R1: `match_names` must be a list.
        R2: each entry must be a string.
        R3: each entry must be non-empty.
        R4: each entry must not contain ':' -- tmux forbids ':' in session
            names, so such a pattern can never match anything; device-
            scoped rules are a distinct, not-yet-built feature (§2.8).

    Non-dict entries in *views* are skipped without error (§6.3); entries
    with no `match_names` key produce no error either (the key is
    optional).
    """
    errors: list[str] = []
    if not isinstance(views, list):
        return errors
    for i, view in enumerate(views):
        if not isinstance(view, dict):
            continue
        if VIEW_RULE_KEY not in view:
            continue
        name = view.get("name", "")
        patterns = view.get(VIEW_RULE_KEY)
        if not isinstance(patterns, list):
            errors.append(
                f"views[{i}] '{name}': {VIEW_RULE_KEY} must be a list of "
                f"strings (got {type(patterns).__name__})"
            )
            continue
        for j, p in enumerate(patterns):
            if not isinstance(p, str):
                errors.append(
                    f"views[{i}] '{name}': {VIEW_RULE_KEY}[{j}] must be a "
                    f"string (got {type(p).__name__})"
                )
            elif not p:
                errors.append(
                    f"views[{i}] '{name}': {VIEW_RULE_KEY}[{j}] may not be empty"
                )
            elif ":" in p:
                errors.append(
                    f"views[{i}] '{name}': {VIEW_RULE_KEY}[{j}] may not contain "
                    f"':' -- tmux session names cannot contain ':', so this "
                    f"pattern can never match. Patterns match the bare session "
                    f"name only; device-scoped rules are not supported."
                )
    return errors


def filter_visible(
    sessions: list[dict],
    settings: dict,
    view: str,
    *,
    include_hidden: bool = False,
) -> list[dict]:
    """Return the canonical visible session list for the given view.

    This is the single source of truth for "what is in this view right now."
    Every count display and every list render must go through this function
    (or the frontend equivalent) — never read raw lengths off stored arrays.

    Parameters:
        sessions: live session dicts (from sessions.list_sessions or similar).
            Each should have `sessionKey` and/or `name`; entries with a truthy
            `status` field are treated as non-session tiles and excluded.
        settings: dict containing `views` and `hidden_sessions`.
        view: "all", "hidden", or a user view name.
        include_hidden: when True, hidden sessions are NOT filtered out of
            "all" or user views. Ignored for "hidden" (which always shows
            only hidden sessions).

    Behavior:
        - Unknown view name → empty list (callers can detect missing views
          by comparing to the user's view list, not via this function).
        - "hidden" view → only sessions whose key (or bare name) appears in
          hidden_sessions. include_hidden is meaningless here.
        - "all" view → all live sessions; exclude hidden unless include_hidden.
        - User view → sessions whose key (or bare name) is in view.sessions;
          exclude hidden unless include_hidden.

    Dual-lookup against `sessionKey` and `name` handles legacy bare-name
    entries in stored data. Once `normalize_session_keys` has run on the
    install, all stored entries should be in `device_id:name` form and the
    fallback is harmless.
    """
    hidden = set(settings.get("hidden_sessions") or [])
    live = [s for s in (sessions or []) if not s.get("status")]

    def is_session_hidden(s: dict) -> bool:
        return _key_of(s) in hidden or s.get("name", "") in hidden

    if view == "hidden":
        return [s for s in live if is_session_hidden(s)]

    if view == "all":
        if include_hidden:
            return list(live)
        return [s for s in live if not is_session_hidden(s)]

    # User view
    user_view = next(
        (v for v in (settings.get("views") or []) if v.get("name") == view),
        None,
    )
    if user_view is None:
        return []
    members = set(user_view.get("sessions") or [])
    patterns = view_patterns(user_view)

    def in_view(s: dict) -> bool:
        return (
            _key_of(s) in members
            or s.get("name", "") in members
            or any(matches_name_pattern(s.get("name", ""), p) for p in patterns)
        )

    if include_hidden:
        return [s for s in live if in_view(s)]
    return [s for s in live if in_view(s) and not is_session_hidden(s)]


def visible_count(
    sessions: list[dict],
    settings: dict,
    view: str,
    *,
    include_hidden: bool = False,
) -> int:
    """Length of `filter_visible(...)`. Use this for every count display."""
    return len(filter_visible(sessions, settings, view, include_hidden=include_hidden))


# ---------------------------------------------------------------------------
# Key normalization (one-shot or idempotent, run after fetching live sessions)
# ---------------------------------------------------------------------------


def normalize_session_keys(settings: dict, sessions: list[dict]) -> dict:
    """Upgrade bare-name entries in stored keys to `device_id:name` form.

    Pre-v2 stored entries used bare `name` strings. v2 stores
    `device_id:name`. This function walks `hidden_sessions` and each
    `view.sessions`, and for any bare-name entry that has a matching live
    session with a `sessionKey`, replaces the entry in place with the
    canonical form.

    Idempotent: entries already in canonical form are left untouched.
    Entries that have no matching live session are also left untouched —
    they may match in the future, or they may be pruned by
    `prune_stale_keys` (Phase 4).

    Mutates and returns *settings*.
    """
    # Build a name → sessionKey map from live sessions. Only sessions that
    # actually have a sessionKey contribute; bare-name live sessions are
    # never the target of an upgrade.
    name_to_key: dict[str, str] = {}
    for s in sessions or []:
        name = s.get("name")
        key = s.get("sessionKey")
        if name and key and name != key:
            # Prefer the first sessionKey we see for a given name. If two
            # live sessions share a name across devices, we cannot pick a
            # single canonical form anyway; leave the bare-name entry alone.
            name_to_key.setdefault(name, key)

    def upgrade(entries: list[str]) -> list[str]:
        result: list[str] = []
        for entry in entries:
            if entry in name_to_key:
                result.append(name_to_key[entry])
            else:
                result.append(entry)
        return result

    if isinstance(settings.get("hidden_sessions"), list):
        settings["hidden_sessions"] = upgrade(settings["hidden_sessions"])

    for view in settings.get("views") or []:
        if isinstance(view.get("sessions"), list):
            view["sessions"] = upgrade(view["sessions"])

    return settings


def enforce_mutual_exclusion(settings: dict) -> dict:
    """Enforce that hidden_sessions and view sessions are disjoint.

    If a session key appears in both hidden_sessions and any view,
    it is removed from hidden_sessions (favor visibility over hiding).

    Also deduplicates session keys within each view.

    Mutates and returns the settings dict.
    """
    views = settings.get("views", [])
    hidden = settings.get("hidden_sessions", [])

    # Collect all session keys across all views
    all_view_sessions: set[str] = set()
    for view in views:
        all_view_sessions.update(view.get("sessions", []))

    # Remove overlap from hidden (favor visibility)
    if all_view_sessions and hidden:
        settings["hidden_sessions"] = [s for s in hidden if s not in all_view_sessions]

    # Deduplicate session keys within each view (preserve order)
    for view in views:
        sessions = view.get("sessions", [])
        seen: set[str] = set()
        deduped: list[str] = []
        for s in sessions:
            if s not in seen:
                seen.add(s)
                deduped.append(s)
        view["sessions"] = deduped

    return settings


# ---------------------------------------------------------------------------
# Pure data ops (Phase 2)
#
# Pure data ops. Composable. No tangling of concerns. User-intent ops live on
# the frontend (where the PATCH boundary is) and call these to build the final
# state.
#
# Each mutates the settings dict in place and returns it. No side effects
# beyond the named operation.
# ---------------------------------------------------------------------------


def add_membership(settings: dict, view_name: str, key: str) -> dict:
    """Add `key` to view's session list if absent. No-op if view doesn't exist."""
    for view in settings.get("views") or []:
        if view.get("name") == view_name:
            sessions = view.setdefault("sessions", [])
            if key not in sessions:
                sessions.append(key)
            break
    return settings


def remove_membership(settings: dict, view_name: str, key: str) -> dict:
    """Remove `key` from view's session list. No-op if view or key absent."""
    for view in settings.get("views") or []:
        if view.get("name") == view_name:
            sessions = view.get("sessions") or []
            if key in sessions:
                sessions.remove(key)
            break
    return settings


def remove_from_all_views(settings: dict, key: str) -> dict:
    """Remove `key` from every view's session list."""
    for view in settings.get("views") or []:
        sessions = view.get("sessions") or []
        if key in sessions:
            sessions.remove(key)
    return settings


def hide(settings: dict, key: str) -> dict:
    """Append `key` to hidden_sessions if absent."""
    hidden = settings.setdefault("hidden_sessions", [])
    if key not in hidden:
        hidden.append(key)
    return settings


def unhide(settings: dict, key: str) -> dict:
    """Remove `key` from hidden_sessions. No-op if absent."""
    hidden = settings.get("hidden_sessions") or []
    if key in hidden:
        hidden.remove(key)
    return settings


def validate_view_name(name: str, existing_views: list[dict]) -> str | None:
    """Validate a view name. Returns an error message string, or None if valid.

    Rules:
    - Non-empty after trimming
    - Max 30 characters after trimming
    - Not a reserved name ("all", "hidden") case-insensitive
    - Unique among existing views (case-sensitive match)
    """
    trimmed = name.strip()
    if not trimmed:
        return "View name cannot be empty"
    if len(trimmed) > MAX_VIEW_NAME_LENGTH:
        return f"View name must be {MAX_VIEW_NAME_LENGTH} characters or fewer"
    if trimmed.lower() in RESERVED_VIEW_NAMES:
        return f"'{trimmed}' is a reserved name"
    existing_names = {v.get("name", "") for v in existing_views}
    if trimmed in existing_names:
        return f"A view named '{trimmed}' already exists"
    return None


# ---------------------------------------------------------------------------
# Stale key pruning (Phase 4)
#
# Each device independently tracks which session keys it has failed to observe,
# and prunes them from its own settings once the grace period expires.
#
# CRITICAL: pruning bookkeeping (first-missed-at timestamps) does NOT sync.
# The bookkeeping lives in ~/.config/muxplex/pruning.json (see pruning.py).
# The prune ACTION (removing keys from views/hidden_sessions) IS a normal
# settings write and syncs via the existing LWW mechanism.
# ---------------------------------------------------------------------------


def _key_owner_device_id(key: str) -> str | None:
    """Return the device_id prefix of a canonical `device_id:name` key, or
    None for a legacy bare-name entry (no determinable owner).
    """
    if ":" not in key:
        return None
    return key.split(":", 1)[0]


def prune_stale_keys(
    settings: dict,
    live_keys: set[str],
    *,
    pruning_state: dict | None = None,
    grace_seconds: float = 86400.0,  # 24 hours
    now: float | None = None,
    local_device_id: str | None = None,
    known_remote_device_ids: set[str] | None = None,
    local_evaluable: bool = True,
) -> tuple[dict, dict, bool]:
    """Drop session keys that have been missing past the grace period.

    Args:
        settings: settings dict (will be mutated if pruning happens).
        live_keys: the set of session keys that currently exist (live). For
            federation-aware pruning, this must include not only this
            device's own live keys but also the live keys of every device
            in `known_remote_device_ids` (see below) — callers assemble
            this union before calling.
        pruning_state: local bookkeeping dict of the form
            {"first_missed_at": {key: timestamp}}. Defaults to an empty
            structure if None. Mutated in place.
        grace_seconds: how long a key may be missing before it's pruned.
        now: current time (for testing). Defaults to time.time().
        local_device_id: this instance's own device_id. When provided
            (together to opt in to the positive-knowledge rule below),
            keys owned by this device are always evaluable. When None
            (the default), device ownership is ignored entirely and every
            key is evaluated directly against `live_keys` — this is the
            pre-federation-aware behavior, preserved for callers that
            don't (or can't) supply device/reachability info.
        known_remote_device_ids: device_ids of remote peers whose session
            list is CURRENTLY KNOWN to this instance (e.g. has a fresh
            entry in the federation session cache backing
            `/api/federation/sessions`). Only meaningful when
            `local_device_id` is also provided. Defaults to empty set.
        local_evaluable: whether THIS device's own keys may be evaluated
            for pruning. Defaults to True (today's behavior). Set False
            while a session-presence-manifest restore is pending
            (SESSION_PERSISTENCE_DESIGN.md section 7.4): right after a cold
            start, `enumerate_sessions()` sees zero local sessions -- not
            because they're gone, but because our own knowledge just
            became unavailable -- and treating that as "unknown, not dead"
            (the same treatment already given to an unreachable remote
            device) stops it from starting a real prune countdown on view
            membership before the user has had a chance to run
            `muxplex restore`. Only affects keys owned by `local_device_id`;
            remote-owned and legacy bare-name keys are unaffected.

    Returns:
        (settings, pruning_state, settings_changed) — settings_changed is True
        iff any key was actually removed from view.sessions or hidden_sessions.

    Behavior:
      1. For each key in settings.hidden_sessions or any view.sessions,
         first determine whether it is EVALUABLE (see "Positive-knowledge
         rule" below). A key that is not evaluable is treated exactly like
         a live key for bookkeeping purposes — any stale first_missed_at
         clock is cleared — but it is never pruned.
      2. For an evaluable key:
         - If key in live_keys: drop the key from pruning_state["first_missed_at"]
           (it's alive, nothing to prune).
         - If key not in live_keys:
             - If first_missed_at[key] is absent, record now.
             - Else if now - first_missed_at[key] >= grace_seconds, remove the
               key from hidden_sessions and from every view's sessions list;
               drop the bookkeeping entry for it.
             - Else: leave both alone (within grace).
      3. The pruning_state dict is the source of truth for "when did we first
         miss this key" — never check live_keys against pruning_state's keys
         that aren't actually in stored settings (clean up bookkeeping for
         keys that are no longer referenced anywhere).
      4. Every member this prune removes from a view is stamped into
         `settings["views_changed_at"]` (see `record_views_change` /
         `merge_views`), so a federation merge reads it as a real deletion
         rather than resurrecting it from a peer that hasn't pruned yet.
         `hidden_sessions` removals are NOT stamped: that key is still
         resolved by whole-set LWW, not merged.

    Positive-knowledge rule (federation-aware pruning):
      A remote-owned key (`"<device_id>:<name>"` where device_id != our own)
      may only be evaluated for pruning when that owning device's session
      list is CURRENTLY KNOWN to us — i.e. `local_device_id` was supplied
      AND the key's device_id is either `local_device_id` itself or present
      in `known_remote_device_ids`. If the owning device is unreachable, or
      we simply have no current data for it, the key is treated as "unknown,
      not dead": pruning never fires and the first_missed_at clock never
      starts or advances for it — the clock is actively cleared instead, so
      a device that goes offline for a week and then comes back online with
      its sessions intact does not resume a partially-elapsed countdown
      (or worse, get pruned instantly) the moment we regain knowledge of it.
      Legacy bare-name entries (no `device_id:` prefix, `_key_owner_device_id`
      returns None) have no determinable owner at all — they preserve
      today's behavior unconditionally and are always evaluated directly
      against `live_keys`, regardless of `local_device_id`.
      When `local_device_id` is None, this whole rule is bypassed and every
      key is evaluated directly against `live_keys` (full backward
      compatibility with pre-federation-aware callers/tests).
    """
    if pruning_state is None:
        pruning_state = {}
    if "first_missed_at" not in pruning_state:
        pruning_state["first_missed_at"] = {}

    first_missed: dict[str, float] = pruning_state["first_missed_at"]

    if now is None:
        now = time.time()

    known_remote_device_ids = known_remote_device_ids or set()

    # Members this prune removes, as (view name, session key). Stamped into
    # `views_changed_at` at the end so the removal reads as a real DELETION
    # to every peer, not as "this device never knew about it" -- without
    # this, the next federation merge would resurrect every pruned key from
    # a peer that has not pruned it yet, and undo this prune on every cycle
    # until it did. See merge_views/_survives.
    pruned_members: list[tuple[str, str]] = []

    # Collect all session keys currently referenced in settings.
    all_settings_keys: set[str] = set()
    for key in settings.get("hidden_sessions") or []:
        all_settings_keys.add(key)
    for view in settings.get("views") or []:
        for key in view.get("sessions") or []:
            all_settings_keys.add(key)

    settings_changed = False

    # Evaluate each referenced key.
    for key in all_settings_keys:
        owner = _key_owner_device_id(key)
        # Positive-knowledge gate: only meaningful for device-owned keys
        # (owner is not None) when the caller opted in by supplying
        # local_device_id. Bare-name keys (owner is None) and callers that
        # don't supply local_device_id always fall through as "evaluable",
        # preserving today's behavior exactly.
        evaluable = (
            local_device_id is None
            or owner is None
            or (owner == local_device_id and local_evaluable)
            or owner in known_remote_device_ids
        )

        if not evaluable:
            # Owning device's session list is not currently known to us.
            # Treat as "unknown, not dead": never prune, and clear (rather
            # than advance) any stale bookkeeping clock so a later cycle
            # where the device IS known starts a fresh grace window instead
            # of inheriting a stale partial count.
            first_missed.pop(key, None)
            continue

        if key in live_keys:
            # Session is alive — clear any stale bookkeeping for it.
            first_missed.pop(key, None)
        else:
            # Session is currently missing from live_keys.
            if key not in first_missed:
                # First time we notice it's missing — start the clock.
                first_missed[key] = now
            elif now - first_missed[key] >= grace_seconds:
                # Grace period expired — remove the key from settings.
                hidden = settings.get("hidden_sessions") or []
                if key in hidden:
                    settings["hidden_sessions"] = [k for k in hidden if k != key]
                    settings_changed = True
                for view in settings.get("views") or []:
                    view_sessions = view.get("sessions") or []
                    if key in view_sessions:
                        view["sessions"] = [k for k in view_sessions if k != key]
                        settings_changed = True
                        view_name = view.get("name")
                        if isinstance(view_name, str):
                            pruned_members.append((view_name, key))
                # Drop the bookkeeping entry now that the key is gone.
                del first_missed[key]
            # else: still within grace — leave settings and bookkeeping alone.

    # Garbage-collect bookkeeping entries that no longer correspond to any key
    # in settings (they may have been removed by an external edit, a peer sync,
    # or a previous prune cycle that ran on another device).  This prevents
    # first_missed_at from accumulating forever.
    #
    # Recompute the live settings key set AFTER the pruning loop above, so
    # keys that were just pruned don't count as "referenced".
    current_settings_keys: set[str] = set()
    for key in settings.get("hidden_sessions") or []:
        current_settings_keys.add(key)
    for view in settings.get("views") or []:
        for key in view.get("sessions") or []:
            current_settings_keys.add(key)

    for key in list(first_missed):
        if key not in current_settings_keys:
            del first_missed[key]

    if pruned_members:
        meta = normalize_views_changed_at(settings.get("views_changed_at"))
        for view_name, key in pruned_members:
            entry = meta.setdefault(view_name, {"at": None, "members": {}})
            entry["members"][key] = now
        settings["views_changed_at"] = _gc_views_changed_at(
            meta, settings.get("views"), now, VIEW_TOMBSTONE_TTL_SECONDS
        )

    return settings, pruning_state, settings_changed
