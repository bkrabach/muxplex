"""Isolating experiment: does diagnose_transcript flag the panel's WELL-FORMED
continuation request as broken, purely because it ends on a tool result?"""
import json
from amplifier_foundation.session import diagnose_transcript, repair_transcript

def diag(label, msgs):
    ann = [{**m, "line_num": i + 1} for i, m in enumerate(msgs)]
    d = diagnose_transcript(ann)
    print(f"--- {label} (n={len(msgs)}) ---")
    print("  status         :", d["status"])
    print("  failure_modes  :", d["failure_modes"])
    print("  orphaned       :", d["orphaned_tool_ids"])
    print("  misplaced      :", d["misplaced_tool_ids"])
    print("  incomplete     :", d["incomplete_turns"])
    if d["status"] != "healthy":
        rep = repair_transcript(ann, d)
        print("  entries_after  :", len(rep))
        print("  APPENDED       :", json.dumps(rep[-1])[:200])
    print()
    return d

SYS  = {"role": "system", "content": "You are a small assistant embedded in a muxplex dashboard."}
USER = {"role": "user", "content": 'Type "hello" into the counter session.'}
def A(tcid, name="list_muxplex_sessions"):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"id": tcid, "type": "function",
                            "function": {"name": name, "arguments": "{}"}}]}
def T(tcid):
    return {"role": "tool", "tool_call_id": tcid, "content": '[{"name":"counter"}]'}

# R1: opening request -- panel sends [system, user]
diag("R1 opening request  [system,user]", [SYS, USER])

# R2: continuation after ONE tool call -- what the panel actually POSTs.
#     This is the documented, REQUIRED shape for an OpenAI tool continuation.
diag("R2 continuation     [system,user,assistant(tc),tool]", [SYS, USER, A("toolu_A"), T("toolu_A")])

# R3: second tool round in the same user turn (entries_before=6 in the journal)
diag("R3 continuation     [system,user,A(tc1),tool,A(tc2),tool]",
     [SYS, USER, A("toolu_A"), T("toolu_A"), A("toolu_B"), T("toolu_B")])

# R2 with PARALLEL tool calls -- tests the index:0 workaround hypothesis directly
par = {"role": "assistant", "content": "",
       "tool_calls": [{"id": "toolu_P1", "type": "function",
                       "function": {"name": "list_muxplex_sessions", "arguments": "{}"}},
                      {"id": "toolu_P2", "type": "function",
                       "function": {"name": "get_muxplex_session", "arguments": "{}"}}]}
diag("R2-parallel         [system,user,assistant(2 tc),tool,tool]",
     [SYS, USER, par, T("toolu_P1"), T("toolu_P2")])

# CONTROL: the SAME array with the closing assistant response the model has not
# produced yet. If this is healthy, the only "defect" is that the answer is missing.
diag("CONTROL  R2 + closing assistant text",
     [SYS, USER, A("toolu_A"), T("toolu_A"), {"role": "assistant", "content": "Done."}])

# CONTROL: genuinely broken -- tool call with NO result. This is what FM1 is for.
diag("CONTROL  genuinely broken (orphaned tool_use)", [SYS, USER, A("toolu_X")])
