#!/usr/bin/env python
"""Reconciler-level probe for muxplex-3aw / muxplex-c1x.

Exercises ``amplifier_agent_http._reconciler.reconcile_client_history`` directly,
with a stub store, and reports for each case:

  * whether the "Client-sent transcript was broken" WARNING fired,
  * what ``store.save()`` actually received (the durable record),
  * whether the fabricated "previous tool calls were interrupted" text landed.

Run it BEFORE and AFTER applying the trailing-exemption patch. The two
continuation rows must flip from ``repaired`` to ``passthrough``; every control
row must be byte-identical across both runs.

Run with the sidecar's own interpreter, inside the DTU:

    /home/aa-svc/.local/share/uv/tools/amplifier-agent/bin/python probe_reconciler.py

Note on scope: this probes the RECONCILER, not ``diagnose_transcript``. The
recommended fix post-filters the diagnosis inside the HTTP face, so
``diagnose_transcript`` keeps returning ``broken`` for a continuation either
way -- see probe_diagnose_transcript.py for that layer. Only this probe can
tell you whether the sidecar still *acts* on it.
"""

from __future__ import annotations

import json
import logging

from amplifier_agent_http._reconciler import reconcile_client_history

FABRICATION = "previous tool calls were interrupted"


class StubStore:
    """Captures what the real SessionStore would have written to disk."""

    def __init__(self) -> None:
        self.saved: list[dict] | None = None

    def save(self, session_id, messages, metadata=None):
        self.saved = messages


class WarningCatcher(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[str] = []

    def emit(self, record):
        self.records.append(record.getMessage())


def A(*tool_calls):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": tid, "type": "function", "function": {"name": "get_muxplex_sessions", "arguments": "{}"}}
            for tid in tool_calls
        ],
    }


def T(tid, body='[{"name":"counter"}]'):
    return {"role": "tool", "tool_call_id": tid, "content": body}


SYS = {"role": "system", "content": "You are a small assistant embedded in a muxplex dashboard."}
USR = {"role": "user", "content": "Which muxplex sessions are running right now?"}
USR2 = {"role": "user", "content": "and now list them again"}

CASES: list[tuple[str, str, list[dict]]] = [
    # name, expectation-after-patch, messages
    ("C1 continuation (single tool call)", "passthrough", [SYS, USR, A("toolu_1"), T("toolu_1")]),
    (
        "C2 continuation (two parallel calls)",
        "passthrough",
        [SYS, USR, A("toolu_1", "toolu_2"), T("toolu_1"), T("toolu_2")],
    ),
    (
        "C3 continuation (two sequential turns)",
        "passthrough",
        [SYS, USR, A("toolu_1"), T("toolu_1"), A("toolu_2"), T("toolu_2")],
    ),
    ("C4 control: healthy opening turn", "passthrough", [SYS, USR]),
    (
        "C5 control: closing answer present",
        "passthrough",
        [SYS, USR, A("toolu_1"), T("toolu_1"), {"role": "assistant", "content": "Three sessions."}],
    ),
    ("C6 control: ORPHANED tool call (genuine breakage)", "repaired", [SYS, USR, A("toolu_1")]),
    (
        "C7 control: NON-TRAILING incomplete turn (genuine breakage)",
        "repaired",
        [SYS, USR, A("toolu_1"), T("toolu_1"), USR2],
    ),
]


def main() -> None:
    logger = logging.getLogger("amplifier_agent_http._reconciler")
    catcher = WarningCatcher()
    logger.addHandler(catcher)

    rows = []
    for name, expected, messages in CASES:
        catcher.records.clear()
        store = StubStore()
        returned = reconcile_client_history(
            client_messages=[dict(m) for m in messages],
            session_id="probe-" + name.split()[0].lower(),
            store=store,
        )
        warned = bool(catcher.records)
        saved = store.saved or []
        fabricated = any(FABRICATION in json.dumps(m.get("content", "")) for m in saved)
        actual = "repaired" if warned else "passthrough"
        rows.append(
            {
                "case": name,
                "expected_after_patch": expected,
                "actual": actual,
                "warning": warned,
                "in": len(messages),
                "saved": len(saved),
                "returned": len(returned),
                "fabricated_in_store": fabricated,
                "verdict": "OK" if actual == expected else "MISMATCH",
            }
        )

    width = max(len(r["case"]) for r in rows)
    print(f"{'case'.ljust(width)}  expected      actual        warn   in->saved  fabricated  verdict")
    print("-" * (width + 62))
    for r in rows:
        warn = str(r["warning"])
        fab = str(r["fabricated_in_store"])
        flow = f"{r['in']}->{r['saved']}"
        print(
            f"{r['case'].ljust(width)}  {r['expected_after_patch']:<12}  {r['actual']:<12}  "
            f"{warn:<5}  {flow:<9}  {fab:<10}  {r['verdict']}"
        )

    mismatches = [r for r in rows if r["verdict"] != "OK"]
    print()
    print(f"{len(rows) - len(mismatches)}/{len(rows)} rows match the post-patch expectation.")
    if mismatches:
        print("MISMATCH rows (expected before the patch is applied):")
        for r in mismatches:
            print(f"  - {r['case']}: expected {r['expected_after_patch']}, got {r['actual']}")


if __name__ == "__main__":
    main()
