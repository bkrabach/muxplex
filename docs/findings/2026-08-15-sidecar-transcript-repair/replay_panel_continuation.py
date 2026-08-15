"""Fresh end-to-end reproduction, driven through muxplex's OWN proxy
(POST /api/agent/chat/completions) exactly as chat.js does, using the panel's
real MODEL / SYSTEM_PROMPT / TOOLS and the panel's exact message shapes.

R1: [system, user]                              -> model asks for a tool
R2: [system, user, assistant(tool_calls), tool] -> the continuation the panel
    is REQUIRED to send. Watch what the reconciler says about it.
"""
import json, time, urllib.request

CONSTS = json.load(open("/tmp/panel-consts.json"))
URL = "http://127.0.0.1:8088/api/agent/chat/completions"
CSID = "sidecar-lane-repro-" + str(int(time.time()))
print("client_session_id =", CSID)
print()

def post(messages, label):
    body = {"model": CONSTS["MODEL"], "stream": True,
            "messages": [{"role": "system", "content": CONSTS["SYSTEM_PROMPT"]}] + messages,
            "tools": CONSTS["TOOLS"]}
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Client-Session-Id": CSID})
    tool_calls, chunk_id, finish = {}, None, None
    with urllib.request.urlopen(req, timeout=180) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data: "):
                continue
            data = line[6:]
            if data == "[DONE]":
                break
            try:
                ev = json.loads(data)
            except Exception:
                continue
            chunk_id = chunk_id or ev.get("id")
            for ch in ev.get("choices") or []:
                for tc in (ch.get("delta") or {}).get("tool_calls") or []:
                    # chat.js's workaround: key on index:id, because the HTTP
                    # face emits index 0 for every parallel call.
                    key = "%s:%s" % (tc.get("index"), tc.get("id") or "")
                    slot = tool_calls.setdefault(key, {
                        "id": tc.get("id"), "type": "function",
                        "function": {"name": "", "arguments": ""}})
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
    calls = list(tool_calls.values())
    print("%-4s POSTed %d messages | finish_reason=%s | chunk_id=%s | tool_calls=%s"
          % (label, len(body["messages"]), finish, chunk_id,
             [c["function"]["name"] for c in calls]))
    return calls, chunk_id, finish

user = {"role": "user", "content": "List the muxplex sessions and name the busiest one."}
calls, cid1, fin1 = post([user], "R1")
if fin1 != "tool_calls" or not calls:
    raise SystemExit("R1 did not request a tool call; cannot exercise a continuation.")

# Exactly what chat.js pushes: the assistant turn, then one role=tool per call.
msgs = [user, {"role": "assistant", "content": "", "tool_calls": calls}]
for c in calls:
    msgs.append({"role": "tool", "tool_call_id": c["id"],
                 "content": json.dumps([
                     {"name": "counter",  "last_activity_at": 1786788891},
                     {"name": "logtail", "last_activity_at": 1786788880},
                     {"name": "sysmon",  "last_activity_at": 1786788800}])})
print("R2 payload tail role sequence:", [m["role"] for m in msgs])
calls2, cid2, fin2 = post(msgs, "R2")
print()
print("CORRELATORS -> client_session_id=%s  R1 chunk_id=%s  R2 chunk_id=%s" % (CSID, cid1, cid2))
