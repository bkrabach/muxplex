"""
Per-session follow-up queue: storage shape, item lifecycle, and pure,
no-I/O helpers over the ``state["followups"]`` dict. See
docs/plans/2026-08-05-per-session-followup-queue-plan.md for the full design; this module mirrors bells.py's
split (pure logic here, tmux/settings I/O orchestration in main.py's
``_advance_followup_queue()``).

Storage shape (state.json, top-level key "followups" -- see state.py's
module docstring for the schema comment, and §3.2 of the spec for why this
is top-level rather than nested under sessions[name]):

    {
      "<session name>": {
        "revision": int,
        "items": [{"id": str, "text": str, "enter": bool, "created_at": float}],
        "halted": {"reason": str, "detail": str, "at": float, "item_id": str} | None,
      }
    }

Absence of a session name means "no queue." An entry is deleted entirely
once its ``items`` list is empty AND ``halted`` is None -- this keeps
state.json from accumulating one empty object per session forever.

In-memory bookkeeping (module-level, NEVER persisted -- the direct analogue
of bells._bell_seen: after a restart there is no in-flight send and no
settle window to honor, so there is nothing to reconstruct):

    _followup_sending      -- set[str] of session names with a send in
                               flight right now (peek-send-remove's
                               in-flight marker, spec §5.2/§5.3).
    _followup_last_send_at -- dict[str, float] of the last time THIS
                               queue sent something to that session
                               (the settle-window guard, spec §5.3).
"""

import time
import uuid

FOLLOWUP_SETTLE_SECONDS: float = 2.0
MAX_FOLLOWUPS: int = 16

# In-memory only -- see module docstring. A bare module-level set/dict
# (not per-app-instance state) is acceptable here for the same reason
# bells._bell_seen is: this process owns exactly one tmux server's worth of
# sessions, and tests reset these between runs (see conftest-style fixtures
# in test_followups.py).
_followup_sending: set[str] = set()
_followup_last_send_at: dict[str, float] = {}


def empty_queue() -> dict:
    """Return a fresh, empty queue entry. Every call is independent."""
    return {"revision": 0, "items": [], "halted": None}


def get_queue(state: dict, name: str) -> dict:
    """Return session *name*'s queue entry, or a fresh empty one if absent.

    NOTE: when the entry exists, this returns the LIVE dict from *state*
    (not a copy) -- callers that mutate it are expected to be inside
    state_lock, matching every other read/mutate helper in this codebase
    (e.g. bells.process_bell_flags's `bell = state["sessions"][name]["bell"]`).
    When absent, a fresh throwaway dict is returned (mutating it does
    nothing to *state* -- callers that want a real entry must use
    ``state.setdefault("followups", {}).setdefault(name, empty_queue())``,
    which append_item()/set_halted() below do internally).
    """
    return state.get("followups", {}).get(name, empty_queue())


def summary(state: dict, name: str) -> dict:
    """Return ``{"pending": int, "halted": bool}`` for *name*.

    Used by GET /api/sessions and GET /api/view to surface a queue badge
    without shipping the full item list on every poll (spec §7.2).
    """
    entry = state.get("followups", {}).get(name)
    if entry is None:
        return {"pending": 0, "halted": False}
    return {
        "pending": len(entry.get("items", [])),
        "halted": entry.get("halted") is not None,
    }


def _new_item(text: str, enter: bool) -> dict:
    return {
        "id": uuid.uuid4().hex,
        "text": text,
        "enter": enter,
        "created_at": time.time(),
    }


def append_item(state: dict, name: str, text: str, enter: bool) -> dict:
    """Append one item to *name*'s queue (creating the entry if absent).

    No precondition -- appending is commutative and cannot clobber (spec
    §7.1). Returns the created item. Mutates *state* in place; caller must
    hold state_lock and call save_state() afterward.
    """
    followups = state.setdefault("followups", {})
    entry = followups.setdefault(name, empty_queue())
    item = _new_item(text, enter)
    entry["items"].append(item)
    entry["revision"] += 1
    return item


def replace_items(
    state: dict, name: str, expected_revision: int, raw_items: list[dict]
) -> tuple[bool, dict | None]:
    """Whole-list replace with a REQUIRED precondition (spec §7.1).

    *raw_items* entries: ``{"id"?: str, "text": str, "enter"?: bool}``. An
    entry carrying a known ``id`` keeps that id and its original
    ``created_at``; an entry with no id (or an unknown one) is treated as
    new -- this is what makes reorder-and-edit expressible without the
    client inventing ids.

    Returns ``(True, None)`` on success (mutates *state* in place; caller
    must save_state()), or ``(False, error_dict)`` on a revision mismatch --
    *state* is left untouched in that case. *error_dict* carries the
    CURRENT revision/items so the client's guarded-retry pattern
    (patchSettingsGuarded's analogue) can re-fetch and re-apply.
    """
    followups = state.setdefault("followups", {})
    entry = followups.get(name, empty_queue())
    if entry["revision"] != expected_revision:
        return False, {
            "revision_mismatch": True,
            "revision": entry["revision"],
            "items": entry["items"],
        }

    existing_by_id = {it["id"]: it for it in entry.get("items", [])}
    new_items = []
    for raw in raw_items:
        item_id = raw.get("id")
        if item_id and item_id in existing_by_id:
            old = existing_by_id[item_id]
            new_items.append(
                {
                    "id": item_id,
                    "text": raw["text"],
                    "enter": raw.get("enter", True),
                    "created_at": old["created_at"],
                }
            )
        else:
            new_items.append(_new_item(raw["text"], raw.get("enter", True)))

    new_entry = {
        "revision": entry["revision"] + 1,
        "items": new_items,
        "halted": entry.get("halted"),
    }
    followups[name] = new_entry
    _prune_if_empty(followups, name)
    return True, None


def clear_queue(state: dict, name: str) -> None:
    """Delete *name*'s queue entirely -- items AND any halt (spec §7.1's
    DELETE semantics). No-op if there was no entry."""
    followups = state.setdefault("followups", {})
    followups.pop(name, None)


def resume_queue(state: dict, name: str) -> bool:
    """Clear *name*'s halt only, keeping items and revision (spec §7.1's
    resume semantics -- deliberately separate from clear_queue()). Returns
    True if a halt was actually cleared, False if there was no entry or no
    halt to clear."""
    followups = state.setdefault("followups", {})
    entry = followups.get(name)
    if entry is None or entry.get("halted") is None:
        return False
    entry["halted"] = None
    return True


def set_halted(state: dict, name: str, reason: str, detail: str, item_id: str) -> None:
    """Mark *name*'s queue halted, retaining every item exactly where it
    was (spec §5.2 step 3's failure branch: a failed send never loses the
    item that was being sent)."""
    followups = state.setdefault("followups", {})
    entry = followups.setdefault(name, empty_queue())
    entry["halted"] = {
        "reason": reason,
        "detail": detail,
        "at": time.time(),
        "item_id": item_id,
    }


def remove_item_by_id(state: dict, name: str, item_id: str) -> bool:
    """Remove one item from *name*'s queue by id (never by index -- a
    concurrent PUT may have reordered the list mid-flight, spec §5.2).
    Bumps revision and prunes an emptied, unhalted entry. Returns True if
    an item was actually removed."""
    followups = state.get("followups", {})
    entry = followups.get(name)
    if entry is None:
        return False
    before = len(entry["items"])
    entry["items"] = [it for it in entry["items"] if it["id"] != item_id]
    removed = len(entry["items"]) != before
    if removed:
        entry["revision"] += 1
        _prune_if_empty(followups, name)
    return removed


def _prune_if_empty(followups: dict, name: str) -> None:
    entry = followups.get(name)
    if entry is not None and not entry["items"] and entry.get("halted") is None:
        del followups[name]


def reap_stale_queues(state: dict, name_set: set[str]) -> list[tuple[str, int]]:
    """Delete every queue entry whose session is absent from *name_set*.

    Caller is responsible for only calling this when tmux is CONFIRMED
    alive this cycle (``probe_tmux_epoch()`` returned non-None) -- see
    spec §3.4; this function has no way to tell "tmux is down" from "this
    session genuinely doesn't exist" and must not be called during the
    former.

    Returns ``[(name, item_count), ...]`` for every dropped queue so the
    caller can log one warning per drop -- dropping user-authored queued
    text must never be silent.
    """
    followups = state.get("followups", {})
    dropped: list[tuple[str, int]] = []
    for name in list(followups):
        if name not in name_set:
            dropped.append((name, len(followups[name].get("items", []))))
            del followups[name]
    return dropped


def is_sending(name: str) -> bool:
    """True if a send is currently in flight for *name* (spec §7.1's
    PUT ... -> 409 {"send_in_flight": true} check)."""
    return name in _followup_sending


def acceptance_ok(state: dict, name: str, now: float | None = None) -> bool:
    """Should a bell for *name* advance its queue right now?

    This is the peek-eligibility check ONLY (spec §5.1) -- the input fence
    (settings.input_enabled / input_allowed_sessions) is a SEPARATE
    re-evaluation performed outside state_lock at fire time (spec §6.3);
    this function knows nothing about it.

    True iff ALL of:
      - a queue entry exists for *name* with a non-empty items list, and
      - halted is None, and
      - *name* is not currently in ``_followup_sending``, and
      - at least FOLLOWUP_SETTLE_SECONDS have passed since this queue last
        sent something to *name*.
    """
    now = time.time() if now is None else now
    entry = state.get("followups", {}).get(name)
    if entry is None or not entry.get("items") or entry.get("halted") is not None:
        return False
    if name in _followup_sending:
        return False
    return now - _followup_last_send_at.get(name, 0.0) >= FOLLOWUP_SETTLE_SECONDS
