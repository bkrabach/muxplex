"""Cross-implementation agreement: `views.matches_name_pattern()` vs. the
shared fixture (muxplex-4h9).

This is the canonical copy of `tests/fixtures/session_filter_cases.json`.
The SAME cases are consumed by:
- `frontend/tests/test_session_filter.mjs` (`matchesNamePattern()`), same repo

docs/API_SEMANTICS.md's `settings.session_filter` entry requires the
frontend's client-side-only glob mirror to agree with the server's real
`fnmatch`-backed matcher for every case here. This fixture is the mechanism
that makes a drift between the two a test FAILURE rather than a silent
divergence discovered later as "the filter behaves differently than a view
rule with the same pattern would."
"""

from __future__ import annotations

import json
from pathlib import Path

from muxplex.views import matches_name_pattern

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "session_filter_cases.json"


def _load_cases() -> list[dict]:
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return data["cases"]


def test_session_filter_fixture_has_at_least_22_cases() -> None:
    cases = _load_cases()
    assert len(cases) >= 22, (
        f"fixture must have at least 22 cases, found {len(cases)} (see {FIXTURE_PATH})"
    )


def test_matches_name_pattern_matches_fixture_for_every_case() -> None:
    cases = _load_cases()
    assert cases, "fixture must not be empty -- an empty fixture would pass vacuously"

    for case in cases:
        result = matches_name_pattern(case["name"], case["pattern"])
        assert result == case["expected"], (
            f"case name={case['name']!r} pattern={case['pattern']!r}: "
            f"matches_name_pattern() returned {result}, expected {case['expected']} "
            f"({case.get('why', 'no reason given')}) (see {FIXTURE_PATH})"
        )
