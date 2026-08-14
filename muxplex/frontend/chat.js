/* chat.js -- thin POC chat panel for muxplex.
 *
 * Talks ONLY to muxplex's own origin:
 *   - POST /api/agent/chat/completions   (muxplex's server-side proxy into the
 *     amplifier-agent sidecar; see main.py's agent_chat_completions_proxy)
 *   - GET  /api/sessions                 (FIRST host-provided tool: read-only
 *     session list)
 *   - GET  /api/sessions/{name}          (SECOND: read-only single-session
 *     detail + pane scrollback)
 *   - POST /api/sessions/{name}/connect  (THIRD: make a session the
 *     dashboard's active one -- same effect as clicking its tile)
 *   - PATCH /api/state                   (FOURTH: change the active view
 *     filter, after validating the name against GET /api/view's own list)
 *   - POST /api/sessions/{name}/input    (FIFTH: type text/keys into a
 *     session's terminal -- remote code execution by design, fenced
 *     server-side by settings.input_enabled / input_allowed_sessions, both
 *     LOCAL_ONLY_KEYS the operator can only set by editing settings.json on
 *     disk. This code never tries to open or route around that fence; it
 *     only ever surfaces whatever muxplex itself decides.)
 *   - GET  /api/federation/sessions      (SIXTH: session list merged across
 *     every configured peer device, not just this one)
 *
 * The agent sidecar never sees this browser directly and holds no muxplex
 * credential of any kind. This file IS the "browser executes the tool"
 * half of that architecture: for every tool above, this code -- running as
 * the logged-in user, with the user's own muxplex_session cookie -- is what
 * actually calls the corresponding muxplex endpoint. The agent inherits
 * EXACTLY the calling user's authority; it is never handed a separate
 * credential and can never grant itself more than the browser's own cookie
 * already permits.
 *
 * No build step. Loaded directly via <script src="/chat.js" defer></script>.
 */
(function () {
  "use strict";

  var MODEL = "claude-sonnet-5";

  var TOOLS = [
    {
      type: "function",
      function: {
        name: "list_muxplex_sessions",
        description:
          "List the tmux sessions currently visible in this muxplex instance " +
          "(name, last activity, current working directory).",
        parameters: { type: "object", properties: {}, additionalProperties: false },
      },
    },
    {
      type: "function",
      function: {
        name: "get_muxplex_session_details",
        description:
          "Get one specific tmux session's recent pane content (its actual " +
          "captured terminal output/scrollback) plus metadata (last activity, " +
          "created time, working directory, pending follow-ups). Use this " +
          "whenever the user asks what's happening/showing/printing/running " +
          "INSIDE a named session, or wants to see its output or logs. If you " +
          "don't already know the exact session name, call " +
          "list_muxplex_sessions first to look it up -- don't guess it.",
        parameters: {
          type: "object",
          properties: {
            session_name: {
              type: "string",
              description:
                "Exact tmux session name, e.g. one returned by list_muxplex_sessions.",
            },
            lines: {
              type: "integer",
              description:
                "How many lines of recent pane scrollback to return (1-2000). " +
                "Omit to use the server's default window.",
            },
          },
          required: ["session_name"],
          additionalProperties: false,
        },
      },
    },
    {
      type: "function",
      function: {
        name: "switch_muxplex_session",
        description:
          "Switch the dashboard's active tmux session -- the same effect as " +
          "the user clicking that session's tile to open it (ensures a live " +
          "terminal exists for it, then makes it the focused/active " +
          "session). Use when the user asks to switch to, open, focus, or " +
          "go to a named session. If you don't already know the exact " +
          "session name, call list_muxplex_sessions first -- don't guess it.",
        parameters: {
          type: "object",
          properties: {
            session_name: {
              type: "string",
              description: "Exact tmux session name to make active.",
            },
          },
          required: ["session_name"],
          additionalProperties: false,
        },
      },
    },
    {
      type: "function",
      function: {
        name: "switch_muxplex_view",
        description:
          "Change which view filter of sessions is currently active in the " +
          "dashboard -- the same effect as picking a view from the view " +
          "dropdown/sidebar. 'all' shows every visible session; 'hidden' " +
          "shows sessions the user has hidden; any other name must be one " +
          "of the user's own configured views. An invalid name is rejected " +
          "with an error naming the exact current valid list -- retry with " +
          "one of those. Use when the user asks to switch/change/filter the " +
          "view.",
        parameters: {
          type: "object",
          properties: {
            view: {
              type: "string",
              description:
                "View name to activate, e.g. \"all\", \"hidden\", or a configured view name.",
            },
          },
          required: ["view"],
          additionalProperties: false,
        },
      },
    },
    {
      type: "function",
      function: {
        name: "send_muxplex_session_input",
        description:
          "Type text and/or special keys into a tmux session's terminal, " +
          "exactly as if the user had typed it at the keyboard -- this " +
          "actually runs commands (remote code execution by design). It is " +
          "OFF by default: the muxplex operator must explicitly enable it " +
          "(settings.input_enabled) AND allow-list the specific session " +
          "(settings.input_allowed_sessions) on the server -- a setting only " +
          "changeable by editing a file on disk, never through this or any " +
          "API call. If either is not set for the target session, this call " +
          "fails with the server's real 403 error -- report that error to " +
          "the user verbatim; never imply there is a way around it or retry " +
          "as if it might succeed differently. Use only when the user " +
          "explicitly asks you to type/run/send/press something into a " +
          "named session.",
        parameters: {
          type: "object",
          properties: {
            session_name: {
              type: "string",
              description: "Exact tmux session name to type into.",
            },
            text: {
              type: "string",
              description:
                "Literal text to type (sent as literal characters, never shell-interpreted).",
            },
            enter: {
              type: "boolean",
              description:
                "Press Enter after the text, submitting the line. Defaults to true.",
            },
            keys: {
              type: "array",
              items: {
                type: "string",
                enum: [
                  "Enter", "Escape", "Tab", "C-c", "C-d",
                  "Up", "Down", "Left", "Right", "PageUp", "PageDown",
                ],
              },
              description:
                "Named special keys to send, in order, after text (e.g. [\"C-c\"] to interrupt).",
            },
          },
          required: ["session_name"],
          additionalProperties: false,
        },
      },
    },
    {
      type: "function",
      function: {
        name: "list_muxplex_federated_sessions",
        description:
          "List tmux sessions across every federated muxplex device -- this " +
          "device plus any configured peer devices reachable over the " +
          "federation link -- not just the local one. Each entry is tagged " +
          "with deviceId/deviceName so you can tell which device it's on; a " +
          "peer that's offline or misconfigured shows up as a status entry " +
          "(e.g. status: \"unreachable\") instead of session data. Use when " +
          "the user asks about sessions on another machine/device, or wants " +
          "a fleet-wide view across devices -- this is something " +
          "list_muxplex_sessions cannot see, since that tool is local-only.",
        parameters: { type: "object", properties: {}, additionalProperties: false },
      },
    },
  ];

  var SYSTEM_PROMPT =
    "You are a small assistant embedded in a muxplex dashboard (a web UI for " +
    "tmux sessions). Every tool you call runs with the logged-in user's own " +
    "authority -- exactly what they could do by clicking around the UI " +
    "themselves, never more. You have six tools:\n" +
    "- list_muxplex_sessions: what sessions/panes/terminals exist locally, " +
    "and their names/activity.\n" +
    "- get_muxplex_session_details: what's happening inside a specific " +
    "named session (its output/logs/scrollback).\n" +
    "- switch_muxplex_session: make a named session the dashboard's active " +
    "one.\n" +
    "- switch_muxplex_view: change which view filter is active (\"all\", " +
    "\"hidden\", or a configured view name).\n" +
    "- send_muxplex_session_input: type text/keys into a session's terminal " +
    "-- real remote code execution, fenced server-side and OFF by default. " +
    "If it's disabled you will get a real 403 back; report that error " +
    "verbatim rather than guessing a workaround or retrying.\n" +
    "- list_muxplex_federated_sessions: sessions across ALL federated " +
    "devices, not just this one -- use for cross-device/fleet questions " +
    "that list_muxplex_sessions cannot answer.\n" +
    "If the user names a session you haven't seen yet, call " +
    "list_muxplex_sessions first to confirm its exact name before acting on " +
    "it. Keep answers short.";

  var clientSessionId = null;
  var messages = []; // OpenAI-style chat messages for the CURRENT conversation

  var panelEl, messagesEl, inputEl, sendBtn, newBtn, closeBtn, openBtn;

  function $(id) {
    return document.getElementById(id);
  }

  function newConversation() {
    clientSessionId = "chat-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
    messages = [];
    messagesEl.textContent = "";
    appendSystemLine("New conversation (" + clientSessionId + ")");
  }

  // Branding pass: these three renderers used to set raw inline styles
  // (arbitrary hex colors unrelated to the app's palette). They now assign
  // class names defined in style.css (.agent-msg-*), which use the same
  // --bg-surface/--border/--text/--accent tokens as the rest of muxplex.
  // DOM shape, text content, and scroll behavior are unchanged.
  function appendSystemLine(text) {
    var div = document.createElement("div");
    div.className = "agent-msg-system";
    div.textContent = text;
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function appendBubble(role) {
    var div = document.createElement("div");
    div.className = "agent-msg-bubble " +
      (role === "user" ? "agent-msg-bubble--user" : "agent-msg-bubble--assistant");
    div.textContent = "";
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return div;
  }

  function appendError(text) {
    var div = document.createElement("div");
    div.className = "agent-msg-error";
    div.textContent = text;
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  /** Parse one SSE "data: ..." payload line. Returns the parsed object, or
   * null for a line that isn't a data line (keepalive comments, blank lines)
   * or is the literal "[DONE]" sentinel. */
  function parseSseLine(line) {
    if (!line.startsWith("data:")) return null;
    var payload = line.slice(5).trim();
    if (payload === "[DONE]") return null;
    try {
      return JSON.parse(payload);
    } catch (e) {
      appendError("chat panel: could not parse SSE chunk: " + payload);
      return null;
    }
  }

  /** Execute a single host-provided tool call. Two tools are implemented,
   * dispatched by name; anything else fails loudly rather than pretending to
   * succeed. Same-origin fetch: the browser's own muxplex_session cookie is
   * sent automatically (credentials default to "same-origin"). Neither path
   * adds any credential or server-side proxying -- the agent sidecar still
   * cannot reach muxplex; only this browser code, as the logged-in user,
   * can. */
  async function executeToolCall(toolCall) {
    var name = toolCall.function && toolCall.function.name;

    var args = {};
    var rawArgs = toolCall.function && toolCall.function.arguments;
    if (rawArgs) {
      try {
        args = JSON.parse(rawArgs);
      } catch (e) {
        throw new Error(
          "chat panel: could not parse arguments for " + name + ": " + rawArgs
        );
      }
    }

    if (name === "list_muxplex_sessions") {
      var resp = await fetch("/api/sessions", { method: "GET" });
      if (!resp.ok) {
        throw new Error("GET /api/sessions failed: HTTP " + resp.status);
      }
      var sessions = await resp.json();
      var summary = sessions.map(function (s) {
        return {
          name: s.name,
          last_activity_at: s.last_activity_at,
          created_at: s.created_at,
          cwd: s.cwd,
        };
      });
      return JSON.stringify(summary);
    }

    if (name === "get_muxplex_session_details") {
      if (!args.session_name || typeof args.session_name !== "string") {
        throw new Error(
          "chat panel: get_muxplex_session_details requires a session_name argument"
        );
      }
      var url = "/api/sessions/" + encodeURIComponent(args.session_name);
      if (args.lines) {
        url += "?lines=" + encodeURIComponent(args.lines);
      }
      var resp2 = await fetch(url, { method: "GET" });
      if (!resp2.ok) {
        // Fail loud with the real server error (e.g. muxplex's own 404 body
        // "Session 'x' not found") -- no silent catch, no fake-empty result.
        var errBody = await resp2.text().catch(function () { return ""; });
        throw new Error(
          "GET " + url + " failed: HTTP " + resp2.status + (errBody ? " -- " + errBody : "")
        );
      }
      var detail = await resp2.json();
      return JSON.stringify({
        name: detail.name,
        snapshot: detail.snapshot,
        lines: detail.lines,
        last_activity_at: detail.last_activity_at,
        created_at: detail.created_at,
        cwd: detail.cwd,
        followups: detail.followups,
      });
    }

    if (name === "switch_muxplex_session") {
      if (!args.session_name || typeof args.session_name !== "string") {
        throw new Error(
          "chat panel: switch_muxplex_session requires a session_name argument"
        );
      }
      var connectUrl = "/api/sessions/" + encodeURIComponent(args.session_name) + "/connect";
      var connectResp = await fetch(connectUrl, { method: "POST" });
      if (!connectResp.ok) {
        // Fail loud with muxplex's real error (e.g. its own 404 "Session 'x'
        // not found") -- no silent catch, no fake-success result.
        var connectErrBody = await connectResp.text().catch(function () { return ""; });
        throw new Error(
          "POST " + connectUrl + " failed: HTTP " + connectResp.status +
          (connectErrBody ? " -- " + connectErrBody : "")
        );
      }
      var connectResult = await connectResp.json();
      return JSON.stringify({
        active_session: connectResult.active_session,
        terminal_session: connectResult.terminal_session,
      });
    }

    if (name === "switch_muxplex_view") {
      if (!args.view || typeof args.view !== "string") {
        throw new Error("chat panel: switch_muxplex_view requires a view argument");
      }
      // Validate against the server's OWN current view list before writing
      // anything -- PATCH /api/state accepts an unknown active_view without
      // error (it just resolves to zero visible sessions), which would be a
      // silent, confusing failure disguised as success. Fail loud here
      // instead, naming the exact valid options.
      var viewResp = await fetch("/api/view", { method: "GET" });
      if (!viewResp.ok) {
        var viewErrBody = await viewResp.text().catch(function () { return ""; });
        throw new Error(
          "GET /api/view failed: HTTP " + viewResp.status + (viewErrBody ? " -- " + viewErrBody : "")
        );
      }
      var viewData = await viewResp.json();
      var validViews = viewData.views || [];
      if (validViews.indexOf(args.view) === -1) {
        throw new Error(
          "chat panel: unknown view " + JSON.stringify(args.view) +
          ". Valid views right now: " + validViews.join(", ")
        );
      }
      var patchResp = await fetch("/api/state", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active_view: args.view }),
      });
      if (!patchResp.ok) {
        var patchErrBody = await patchResp.text().catch(function () { return ""; });
        throw new Error(
          "PATCH /api/state failed: HTTP " + patchResp.status + (patchErrBody ? " -- " + patchErrBody : "")
        );
      }
      var patchResult = await patchResp.json();
      return JSON.stringify({ active_view: patchResult.active_view });
    }

    if (name === "send_muxplex_session_input") {
      if (!args.session_name || typeof args.session_name !== "string") {
        throw new Error(
          "chat panel: send_muxplex_session_input requires a session_name argument"
        );
      }
      var inputBody = {
        text: typeof args.text === "string" ? args.text : "",
        enter: typeof args.enter === "boolean" ? args.enter : true,
        keys: Array.isArray(args.keys) ? args.keys : [],
      };
      var inputUrl = "/api/sessions/" + encodeURIComponent(args.session_name) + "/input";
      var inputResp = await fetch(inputUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(inputBody),
      });
      if (!inputResp.ok) {
        // THIS is the fenced endpoint. A 403 here (settings.input_enabled is
        // false, or the session isn't in input_allowed_sessions) is muxplex's
        // fence doing exactly its job -- surface the server's real detail
        // text verbatim. Never retry, never imply a workaround exists: the
        // fence can only be widened by a local operator editing
        // settings.json on disk, which this browser code has no path to do.
        var inputErrBody = await inputResp.text().catch(function () { return ""; });
        throw new Error(
          "POST " + inputUrl + " failed: HTTP " + inputResp.status +
          (inputErrBody ? " -- " + inputErrBody : "")
        );
      }
      var inputResult = await inputResp.json();
      return JSON.stringify({
        ok: inputResult.ok,
        session: inputResult.session,
        snapshot: inputResult.snapshot,
      });
    }

    if (name === "list_muxplex_federated_sessions") {
      var fedResp = await fetch("/api/federation/sessions", { method: "GET" });
      if (!fedResp.ok) {
        var fedErrBody = await fedResp.text().catch(function () { return ""; });
        throw new Error(
          "GET /api/federation/sessions failed: HTTP " + fedResp.status +
          (fedErrBody ? " -- " + fedErrBody : "")
        );
      }
      var fedSessions = await fedResp.json();
      var fedSummary = fedSessions.map(function (s) {
        return {
          name: s.name,
          deviceId: s.deviceId,
          deviceName: s.deviceName,
          remoteId: s.remoteId,
          status: s.status,
          last_activity_at: s.last_activity_at,
          cwd: s.cwd,
        };
      });
      return JSON.stringify(fedSummary);
    }

    throw new Error("chat panel: unknown tool requested by model: " + name);
  }

  /** Run one turn of the conversation against /api/agent/chat/completions,
   * streaming the SSE response into the panel. If the model calls one or
   * more tools, executes EACH of them in the browser (in call order) and
   * recurses to continue the turn -- this recursion IS the host-tool round
   * trip described in amplifier-agent/docs/spec/http-face.md's "Host-provided
   * tools" section. A tool-calls turn must produce one {role:"tool"} message
   * per tool_call_id, not just the first, or the continuation request is
   * malformed and the provider will reject it. */
  async function runTurn() {
    var body = {
      model: MODEL,
      stream: true,
      messages: [{ role: "system", content: SYSTEM_PROMPT }].concat(messages),
      tools: TOOLS,
    };

    var resp = await fetch("/api/agent/chat/completions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Client-Session-Id": clientSessionId,
      },
      body: JSON.stringify(body),
    });

    if (!resp.ok || !resp.body) {
      var errText = await resp.text().catch(function () { return ""; });
      appendError(
        "chat panel: /api/agent/chat/completions failed: HTTP " + resp.status + " " + errText
      );
      return;
    }

    var reader = resp.body.getReader();
    var decoder = new TextDecoder();
    var buf = "";
    var assistantText = "";
    var assistantBubble = null;
    var toolCallsByIndex = {}; // key -> {id, type, function:{name, arguments}, __seq}
    var toolCallSeq = 0; // insertion order -- keys are not reliably numeric, see below
    var finishReason = null;

    for (;;) {
      var res = await reader.read();
      if (res.done) break;
      buf += decoder.decode(res.value, { stream: true });

      var lines = buf.split("\n");
      buf = lines.pop(); // last (possibly partial) line stays in buf

      for (var i = 0; i < lines.length; i++) {
        var line = lines[i].trim();
        if (!line) continue;
        var chunk = parseSseLine(line);
        if (!chunk) continue;

        var choice = chunk.choices && chunk.choices[0];
        if (!choice) continue;

        if (choice.delta && typeof choice.delta.content === "string" && choice.delta.content) {
          if (!assistantBubble) assistantBubble = appendBubble("assistant");
          assistantText += choice.delta.content;
          assistantBubble.textContent = assistantText;
          messagesEl.scrollTop = messagesEl.scrollHeight;
        }

        if (choice.delta && choice.delta.tool_calls) {
          choice.delta.tool_calls.forEach(function (tc) {
            var idx = tc.index || 0;
            var existing = toolCallsByIndex[idx];

            // Some providers (observed live: amplifier-agent's
            // chat-completions endpoint reports index:0 for EVERY parallel
            // tool call in a turn instead of incrementing it per call) don't
            // give each parallel call a distinct index -- but each call is
            // still uniquely identified by a fresh `id` on its first chunk.
            // If an incoming id doesn't match what's already accumulated at
            // this index, this is a NEW tool call arriving, not a
            // continuation of the previous one -- key it separately so the
            // two calls' `arguments` strings don't get silently concatenated
            // into one corrupted JSON blob (was reproduced live: two calls
            // in one turn merged into `{"session_name":"a"}{"session_name":"b"}`).
            if (tc.id && existing && existing.id && tc.id !== existing.id) {
              idx = idx + ":" + tc.id;
              existing = toolCallsByIndex[idx];
            }

            if (!existing) {
              existing = {
                id: "",
                type: "function",
                function: { name: "", arguments: "" },
                __seq: toolCallSeq++,
              };
            }
            if (tc.id) existing.id = tc.id;
            if (tc.type) existing.type = tc.type;
            if (tc.function) {
              if (tc.function.name) existing.function.name = tc.function.name;
              if (typeof tc.function.arguments === "string") {
                existing.function.arguments += tc.function.arguments;
              }
            }
            toolCallsByIndex[idx] = existing;
          });
        }

        if (choice.finish_reason) finishReason = choice.finish_reason;
      }
    }

    if (finishReason === "tool_calls") {
      // Sort by __seq (insertion order), not by key -- keys may now be
      // synthetic "index:id" strings (see the parallel-call workaround
      // above), not reliably numeric.
      var toolCalls = Object.keys(toolCallsByIndex)
        .map(function (k) { return toolCallsByIndex[k]; })
        .sort(function (a, b) { return a.__seq - b.__seq; });

      appendSystemLine(
        "model requested tool call(s): " + toolCalls.map(function (t) { return t.function.name; }).join(", ")
      );

      // Record the assistant turn exactly as the model produced it (content
      // may legitimately be "" when the turn was tool-calls-only).
      messages.push({ role: "assistant", content: assistantText, tool_calls: toolCalls });

      // Every tool_call_id from this turn gets its own {role:"tool"} message
      // below, in call order, whether there's one call or several (including
      // repeats of the same tool name) -- the provider requires a reply to
      // each one before the continuation is well-formed.
      for (var t = 0; t < toolCalls.length; t++) {
        var tc2 = toolCalls[t];
        var resultContent;
        try {
          resultContent = await executeToolCall(tc2);
          appendSystemLine("tool result (" + tc2.function.name + "): " + resultContent);
        } catch (toolErr) {
          resultContent = JSON.stringify({ error: String(toolErr && toolErr.message || toolErr) });
          appendError("chat panel: tool execution failed: " + resultContent);
        }
        messages.push({ role: "tool", tool_call_id: tc2.id, content: resultContent });
      }

      // Continue the same turn -- this is the re-POST the spec describes.
      await runTurn();
      return;
    }

    // Normal stop: record the finished assistant turn in history.
    if (assistantText || finishReason === "stop") {
      messages.push({ role: "assistant", content: assistantText });
    }
  }

  async function handleSend() {
    var text = inputEl.value.trim();
    if (!text) return;
    inputEl.value = "";
    var bubble = appendBubble("user");
    bubble.textContent = text;
    messages.push({ role: "user", content: text });
    sendBtn.disabled = true;
    try {
      await runTurn();
    } catch (err) {
      appendError("chat panel: request failed: " + String(err && err.message || err));
    } finally {
      sendBtn.disabled = false;
    }
  }

  function init() {
    panelEl = $("chat-panel");
    messagesEl = $("chat-messages");
    inputEl = $("chat-input");
    sendBtn = $("chat-send-btn");
    newBtn = $("chat-new-btn");
    closeBtn = $("chat-close-btn");
    openBtn = $("chat-open-btn");

    var __missing = [];
    if (!panelEl) __missing.push("chat-panel");
    if (!messagesEl) __missing.push("chat-messages");
    if (!inputEl) __missing.push("chat-input");
    if (!sendBtn) __missing.push("chat-send-btn");
    if (!newBtn) __missing.push("chat-new-btn");
    if (!closeBtn) __missing.push("chat-close-btn");
    if (!openBtn) __missing.push("chat-open-btn");
    if (__missing.length) {
      var __msg = "chat panel BROKEN -- missing DOM element(s): " + __missing.join(", ");
      console.error(__msg);
      try {
        var __b = document.createElement("div");
        __b.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:99999;" +
          "background:#7a1010;color:#fff;padding:8px 12px;font:13px monospace;";
        __b.textContent = __msg;
        document.body.appendChild(__b);
      } catch (e) {}
      throw new Error(__msg);
    }

    newConversation();

    function togglePanel() {
      panelEl.classList.toggle("hidden");
    }

    openBtn.addEventListener("click", togglePanel);

    // Expanded (terminal) header's entry point -- same branded button,
    // mirroring how sync-group-btn/settings-btn each get a second listener
    // for their "-expanded" twin in app.js. Deliberately NOT in the fatal
    // __missing check above: this button is a nice-to-have second entry
    // point, not a required part of the panel's own contract.
    var openBtnExpanded = $("chat-open-btn-expanded");
    if (openBtnExpanded) {
      openBtnExpanded.addEventListener("click", togglePanel);
    } else {
      console.warn("chat panel: #chat-open-btn-expanded not found -- panel only reachable from the overview header");
    }

    closeBtn.addEventListener("click", function () {
      panelEl.classList.add("hidden");
    });
    newBtn.addEventListener("click", newConversation);
    sendBtn.addEventListener("click", handleSend);
    inputEl.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
