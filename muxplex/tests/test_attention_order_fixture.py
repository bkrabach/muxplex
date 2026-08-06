"""Cross-implementation agreement: `_attention_order()` vs. the shared fixture.

This is the canonical copy of `tests/fixtures/attention_sort_cases.json`.
The SAME cases are consumed by:
- `frontend/tests/test_app.mjs` (`sortByAttention()`), same repo
- `muxplex-deck/tests/test_attention_fixture.py` (`apply_attention_sort()`),
  a byte-for-byte duplicate of the fixture in that separate repo

docs/API_SEMANTICS.md's "?sort=attention" entry requires all three
implementations to move together. This fixture is the mechanism that makes
a drift in any one of them a test FAILURE rather than a silent divergence
discovered later in production -- see AGENTS.md's history of exactly that
kind of incident (v0.38.1's tier-2 addition, and its removal here).
"""

from __future__ import annotations

import json
from pathlib import Path

from muxplex.bells import needs_attention
from muxplex.main import _attention_order

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "attention_sort_cases.json"


def _load_cases() -> list[dict]:
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return data["cases"]


def _to_resolved_session(raw: dict, active_session: str | None) -> dict:
    """Build the shape `_attention_order()` expects: the same dict shape
    `get_view()` constructs (name, active, needs_attention, bell,
    last_activity_at) -- see main.py's `get_view()`."""
    bell = raw["bell"]
    return {
        "name": raw["name"],
        "active": raw["name"] == active_session,
        "needs_attention": needs_attention(bell),
        "bell": bell,
        "last_activity_at": raw.get("last_activity_at"),
    }


def test_attention_order_matches_fixture_for_every_case() -> None:
    cases = _load_cases()
    assert cases, "fixture must not be empty -- an empty fixture would pass vacuously"

    for case in cases:
        active_session = case.get("active_session")
        sessions = [
            _to_resolved_session(raw, active_session) for raw in case["sessions"]
        ]
        ordered = _attention_order(sessions)
        names = [s["name"] for s in ordered]
        assert names == case["expected_order"], (
            f"case {case['name']!r}: _attention_order() produced {names}, "
            f"expected {case['expected_order']} (see {FIXTURE_PATH})"
        )
