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
          "as if it might succeed differently. Separately, and even when " +
          "enabled server-side: EVERY call to this tool pauses for an " +
          "explicit human confirmation click in the browser before anything " +
          "is sent -- there is no way to skip, pre-approve, or batch-approve " +
          "this, including within one turn or across repeated calls. If the " +
          "human declines, the call returns a decline error; do not retry " +
          "the same request in this turn -- tell the user it was declined. " +
          "Use only when the user explicitly asks you to type/run/send/press " +
          "something into a named session.",
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
    "verbatim rather than guessing a workaround or retrying. Every call " +
    "ALSO pauses for a human confirmation click in the browser first, with " +
    "no way to skip or pre-approve it; if declined, tell the user rather " +
    "than retrying the same call.\n" +
    "- list_muxplex_federated_sessions: sessions across ALL federated " +
    "devices, not just this one -- use for cross-device/fleet questions " +
    "that list_muxplex_sessions cannot answer.\n" +
    "If the user names a session you haven't seen yet, call " +
    "list_muxplex_sessions first to confirm its exact name before acting on " +
    "it. Keep answers short.";

  var clientSessionId = null;
  var messages = []; // OpenAI-style chat messages for the CURRENT conversation

  var panelEl, messagesEl, inputEl, sendBtn, newBtn, closeBtn, openBtn, exportBtn, exportLinkEl;

  // Confirmation-gate elements (send_muxplex_session_input only -- see
  // requestInputConfirmation()/resolveConfirm() below).
  var confirmBackdropEl, confirmDialogEl, confirmSessionEl, confirmTextEl,
    confirmKeysEl, confirmCancelBtn, confirmSendBtn;

  // Resolver for the currently-open confirmation gate's Promise, or null
  // when no gate is open. Exactly one gate is ever open at a time -- see
  // requestInputConfirmation()'s guard.
  var pendingConfirmResolve = null;

  // ---------------------------------------------------------------------
  // Debug capture engine (muxplex-4kl).
  //
  // Problem this solves: everything the panel knows about a conversation --
  // transcript, tool calls, raw tool results, timings, the SSE stream, app
  // state at the moment of the turn, console/network errors -- lives only
  // in this tab's memory and evaporates when the panel closes. When a turn
  // fails, there has been no way to hand an engineer anything but a
  // hand-copied transcript.
  //
  // This buffers a flat, chronological, append-only event log for the
  // CURRENT conversation (reset by newConversation(), same lifecycle as
  // `messages`). "Export" (see exportCaptureRecord()) is the ONLY thing
  // that ever reads it -- nothing here is transmitted anywhere on its own;
  // export writes a local .json file via a Blob object URL, never a
  // network request. No telemetry, no auto-upload.
  //
  // Every event carries `turn` (index of the user message it belongs to)
  // and `request` (index of the HTTP round trip to
  // /api/agent/chat/completions within that turn -- a turn makes one
  // round trip per model/tool-call cycle) so a reader can regroup the flat
  // log by conversation structure without this code having to build and
  // maintain a parallel tree in real time. `client_session_id` (sent as
  // X-Client-Session-Id, forwarded verbatim by muxplex's proxy -- see
  // main.py's agent_chat_completions_proxy) is the thread an engineer
  // pulls to line this record up against the amplifier-agent sidecar's own
  // journal (`journalctl -u amplifier-agent-http`), which logs the same
  // string on every "chat-completion start" line, plus a per-request
  // chunk_id captured below from each SSE stream's first chunk -- a
  // tighter, single-request correlator than the session id alone.
  var CAPTURE_MAX_STRING = 20000; // cap any single captured string (tool results carry terminal scrollback)
  var CAPTURE_MAX_EVENTS = 5000; // safety cap on total events per conversation
  var captureEvents = [];
  var captureSeq = 0;
  var captureCapped = false;
  var turnIndex = -1; // index of the current user message within this conversation
  var requestIndex = -1; // index of the current HTTP round trip within the current turn

  function nowIso() {
    return new Date().toISOString();
  }

  /** Truncate a captured string with a visible marker -- never silently
   * drop data; always say exactly how much was cut and from where. */
  function truncateForCapture(str) {
    if (typeof str !== "string") return str;
    if (str.length <= CAPTURE_MAX_STRING) return str;
    return str.slice(0, CAPTURE_MAX_STRING) +
      "\n...[chat panel capture: truncated " + (str.length - CAPTURE_MAX_STRING) + " more characters]";
  }

  /** Append one event to the capture log. Never throws, never silently
   * drops without saying so: once CAPTURE_MAX_EVENTS is reached, exactly
   * one "capture_capped" event is recorded and every push after that is a
   * documented no-op (visible in the exported record itself) rather than
   * an unbounded memory leak or a silent truncation. */
  function capPush(type, fields) {
    if (captureCapped) return null;
    if (captureEvents.length >= CAPTURE_MAX_EVENTS) {
      captureCapped = true;
      var capEvt = {
        seq: captureSeq++, ts: nowIso(), t_ms: Math.round(performance.now()),
        turn: turnIndex, request: requestIndex, type: "capture_capped",
        note: "capture buffer reached " + CAPTURE_MAX_EVENTS + " events for this conversation; " +
          "further events are dropped. Start a New conversation to reset the capture buffer.",
      };
      captureEvents.push(capEvt);
      return capEvt;
    }
    var evt = Object.assign(
      { seq: captureSeq++, ts: nowIso(), t_ms: Math.round(performance.now()), turn: turnIndex, request: requestIndex, type: type },
      fields || {}
    );
    captureEvents.push(evt);
    return evt;
  }

  /** Synchronous snapshot of app state visible from this tab right now --
   * no network calls (this must reflect the moment of the turn, not a
   * fetch a few hundred ms later). Deliberately reads only generic,
   * stable-contract DOM (data-session attributes, this panel's own hidden
   * state) rather than app.js's internal view-name CSS classes, which are
   * not part of any documented contract this file depends on. */
  function snapshotAppState() {
    var sessionEls = document.querySelectorAll("[data-session]");
    var sessionNames = [];
    for (var i = 0; i < sessionEls.length; i++) {
      var n = sessionEls[i].getAttribute("data-session");
      if (n && sessionNames.indexOf(n) === -1) sessionNames.push(n);
    }
    return {
      href: location.href,
      title: document.title,
      visibility_state: document.visibilityState,
      viewport: { width: window.innerWidth, height: window.innerHeight },
      user_agent: navigator.userAgent,
      panel_open: panelEl ? !panelEl.classList.contains("hidden") : null,
      rendered_session_names: sessionNames,
      conversation_message_count: messages.length,
    };
  }

  /** Best-effort structured form of an arbitrary console argument /
   * rejection reason -- Error objects stringify to just their message by
   * default, losing the stack, so those are special-cased. Never throws. */
  function safeDescribe(value) {
    if (value instanceof Error) {
      return { message: value.message, stack: value.stack || null };
    }
    try {
      return JSON.parse(JSON.stringify(value));
    } catch (e) {
      try {
        return String(value);
      } catch (e2) {
        return "[unstringifiable value]";
      }
    }
  }

  /** Install page-wide (not panel-scoped) capture hooks exactly once, at
   * module load -- before init() runs, so even an init()-time failure
   * (e.g. missing DOM elements -> the "chat panel BROKEN" throw) is itself
   * captured. Wraps console.error/warn and listens for uncaught
   * exceptions and unhandled promise rejections anywhere on the page, not
   * just inside this file -- the failures this exists to catch are not
   * guaranteed to originate in chat.js. */
  function installGlobalCaptureHooks() {
    var origError = console.error;
    var origWarn = console.warn;
    console.error = function () {
      capPush("console_error", { args: Array.prototype.slice.call(arguments).map(safeDescribe) });
      return origError.apply(console, arguments);
    };
    console.warn = function () {
      capPush("console_warn", { args: Array.prototype.slice.call(arguments).map(safeDescribe) });
      return origWarn.apply(console, arguments);
    };
    window.addEventListener("error", function (e) {
      capPush("window_error", {
        message: e.message,
        filename: e.filename,
        lineno: e.lineno,
        colno: e.colno,
        stack: e.error && e.error.stack ? e.error.stack : null,
      });
    });
    window.addEventListener("unhandledrejection", function (e) {
      capPush("unhandled_rejection", { reason: safeDescribe(e.reason) });
    });
  }
  installGlobalCaptureHooks();

  /** fetch() wrapper for muxplex API calls that ALSO records a
   * "network_call" capture event (method, url, request body, HTTP status,
   * raw response body, duration, and any transport-level error) --
   * capturing raw tool results, not just the summarized JSON this file's
   * tool handlers return to the model. Reads the response body via
   * .text() exactly once (a Response body can only be consumed once) and
   * parses it as JSON for callers that want a `.json` field; callers that
   * need the exact original error-body text (e.g. to append it verbatim
   * to a thrown Error, matching this file's existing error-message
   * conventions) use `.text` instead of re-parsing.
   *
   * A transport-level failure (fetch() itself throwing -- DNS/network
   * down, not an HTTP error status) is captured, then RE-THROWN so every
   * call site's existing behavior (letting such an error propagate
   * naturally out of executeToolCall) is unchanged by this wrapper. */
  async function apiFetch(method, url, options) {
    options = options || {};
    var startedAt = performance.now();
    var requestBody = options.body != null ? String(options.body) : null;
    var resp = null;
    var text = "";
    var transportErr = null;
    try {
      resp = await fetch(url, Object.assign({ method: method }, options));
      text = await resp.text().catch(function () { return ""; });
    } catch (fetchErr) {
      transportErr = fetchErr;
    }
    var durationMs = Math.round(performance.now() - startedAt);
    var json;
    if (!transportErr && text) {
      try { json = JSON.parse(text); } catch (e) { /* not JSON -- leave undefined */ }
    }
    capPush("network_call", {
      method: method,
      url: url,
      request_body: requestBody ? truncateForCapture(requestBody) : null,
      ok: transportErr ? false : resp.ok,
      status: transportErr ? null : resp.status,
      duration_ms: durationMs,
      body_raw: transportErr ? null : truncateForCapture(text),
      transport_error: transportErr ? String(transportErr && transportErr.message || transportErr) : null,
    });
    if (transportErr) throw transportErr;
    return { ok: resp.ok, status: resp.status, text: text, json: json };
  }

  /** Build the complete exportable record for the CURRENT conversation.
   * Pure function of current state -- safe to call more than once (e.g.
   * a failed download attempt can be retried without re-deriving data). */
  function buildCaptureRecord() {
    return {
      format: "muxplex-agent-panel-debug-record",
      format_version: 1,
      generated_at: nowIso(),
      client_session_id: clientSessionId,
      model: MODEL,
      page: { href: location.href, title: document.title, user_agent: navigator.userAgent },
      event_count: captureEvents.length,
      capped: captureCapped,
      events: captureEvents,
    };
  }

  // The previous export's Blob object URL, so it can be revoked once a new
  // export supersedes it (rather than on a fixed timer -- there's no way to
  // know how long a slow download or a manual "save as" dialog needs it).
  var lastExportUrl = null;

  /** The ONLY export path: writes a local .json file via a Blob object URL
   * and a synthetic click on the persistent #chat-export-link anchor --
   * never a network request, never anything automatic. Sensitive-by-
   * default: tool results in `events` can contain live terminal scrollback,
   * so this never fires without the user explicitly clicking the Export
   * button. Failures are reported in the transcript AND re-thrown -- never
   * a silent no-op button click. */
  function exportCaptureRecord() {
    var record;
    try {
      record = buildCaptureRecord();
    } catch (buildErr) {
      appendError("chat panel: failed to build debug record: " + String(buildErr && buildErr.message || buildErr));
      throw buildErr;
    }
    var json = JSON.stringify(record, null, 2);
    var blob = new Blob([json], { type: "application/json" });
    var url = URL.createObjectURL(blob);
    if (lastExportUrl) URL.revokeObjectURL(lastExportUrl);
    lastExportUrl = url;
    exportLinkEl.href = url;
    exportLinkEl.download = "muxplex-agent-debug-" + clientSessionId + "-" + Date.now() + ".json";
    exportLinkEl.click();
    appendSystemLine(
      "exported debug record (" + record.event_count + " events" +
      (record.capped ? ", capped" : "") + ") to a local file -- nothing was sent anywhere."
    );
  }

  function $(id) {
    return document.getElementById(id);
  }

  function newConversation() {
    clientSessionId = "chat-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
    messages = [];
    messagesEl.textContent = "";
    // Debug capture buffer shares this conversation's lifecycle -- a fresh
    // conversation gets a fresh, empty capture log rather than mixing
    // events from an unrelated earlier conversation into one export.
    captureEvents = [];
    captureSeq = 0;
    captureCapped = false;
    turnIndex = -1;
    requestIndex = -1;
    appendSystemLine("New conversation (" + clientSessionId + ")");
    capPush("conversation_new", { client_session_id: clientSessionId });
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

  // ----------------------------------------------------------------------
  // Tool-result rendering  (muxplex-n7z: the panel is the PHONE agent
  // surface, so it has to actually work at phone width)
  // ----------------------------------------------------------------------
  // This used to render every tool result as
  //     appendSystemLine("tool result (" + name + "): " + resultContent)
  // i.e. the ENTIRE JSON payload, verbatim, into the transcript. On a
  // desktop that is merely noisy. Measured on a real 390x844 viewport it is
  // disqualifying: one get_muxplex_session_details call returns a whole pane
  // snapshot -- 30+ lines of terminal scrollback carrying its raw ANSI SGR
  // bytes (\x1b[1m ...) -- and a vision pass over the actual pixels put ~60%
  // of the visible message area under machine output, with the agent's real
  // answer pushed below the fold. That is the concrete thing that made the
  // <=599px entry button feel like a promise the panel does not keep.
  //
  // The payload is not the problem; showing all of it, always, is. So:
  // one summary line on screen, full payload one tap away in a collapsed
  // <details>. NOTHING is discarded -- the raw text is still in the DOM, and
  // what is sent to the model (messages.push({role:"tool", ...})) is the
  // untouched resultContent, byte for byte, exactly as before. Rendering
  // only. The debug-capture engine above reads the SAME raw resultContent
  // directly at its own call site (capPush("tool_call_result", ...) below)
  // -- both consumers read one raw string for different purposes; neither
  // is ever handed the summarized text.

  /** Strip ANSI CSI/OSC escape sequences so a terminal snapshot can be
   * summarized as text. Deliberately applied ONLY to the summary line --
   * the raw payload in the <details> keeps its real bytes, because that is
   * the thing you open it to debug. */
  function stripAnsi(s) {
    return String(s)
      .replace(/\u001b\][^\u0007\u001b]*(?:\u0007|\u001b\\)/g, "")
      .replace(/\u001b\[[0-9;?]*[ -\/]*[@-~]/g, "")
      .replace(/\u001b[@-Z\\-_]/g, "");
  }

  function clipText(s, n) {
    s = String(s).replace(/\s+/g, " ").trim();
    return s.length > n ? s.slice(0, n - 1) + "\u2026" : s;
  }

  /** One line describing what a tool actually returned. Falls back to an
   * honest "unparsed" label rather than pretending a malformed payload was
   * fine -- and the raw text sits in the <details> either way, so the
   * summary can never be the only account of what happened. */
  function summarizeToolResult(raw) {
    var data;
    try {
      data = JSON.parse(raw);
    } catch (e) {
      return "unparsed payload, " + String(raw).length + " chars \u2014 open to read";
    }

    if (Array.isArray(data)) {
      var names = data.map(function (d) {
        return (d && (d.name || d.deviceName)) || "?";
      });
      var head = names.slice(0, 3).join(", ");
      var more = names.length > 3 ? " (+" + (names.length - 3) + " more)" : "";
      return data.length + (data.length === 1 ? " session: " : " sessions: ") + head + more;
    }

    if (data && typeof data === "object") {
      if (typeof data.snapshot === "string") {
        var lines = stripAnsi(data.snapshot).split("\n").filter(function (l) {
          return l.trim();
        });
        var last = lines.length ? lines[lines.length - 1] : "(empty)";
        var pending = data.followups && data.followups.pending;
        return (data.name || "session") + " \u2014 " + lines.length + " lines" +
          (pending ? " \u00b7 " + pending + " pending follow-up(s)" : "") +
          " \u00b7 last: " + clipText(last, 60);
      }
      if (data.active_session) return "active session \u2192 " + data.active_session;
      if (data.active_view) return "active view \u2192 " + data.active_view;
      if (data.error) return "error: " + clipText(data.error, 90);
      if (data.ok !== undefined) {
        return (data.ok ? "ok" : "not ok") + (data.session ? " \u00b7 " + data.session : "");
      }
    }

    return clipText(raw, 90);
  }

  // ----------------------------------------------------------------------
  // Live attention count in the panel header (muxplex-n7z)
  // ----------------------------------------------------------------------
  // On a phone this panel is the whole screen, so while it is open the
  // dashboard behind it can no longer tell you that something started
  // needing you. Rather than shrink the panel to leave a 31px strip that a
  // vision pass on the real pixels called "too fragmentary to provide any
  // meaningful status" -- the appearance of session awareness rather than
  // session awareness -- carry the one signal that is actually actionable
  // mid-question, in a header row that already exists.
  //
  // Source of truth is GET /api/view?sort=attention -- the SAME endpoint and
  // the SAME server-computed `needs_attention` flag the soft deck reads
  // (deck.js's poll). This deliberately does not re-derive the bell/
  // follow-up predicate client-side: two implementations of one rule drift,
  // and deck.js's own header comment says so.
  //
  // Polls only while the panel is visible. A failed poll says "attention: ?"
  // in the error colour -- it must never render as "all clear", which is the
  // one lie that would matter.

  var ATTENTION_POLL_MS = 5000;
  var attentionTimer = null;
  var attentionEl = null;

  function renderAttention(state, count) {
    if (!attentionEl) return;
    attentionEl.classList.remove("agent-panel-attention--attention",
      "agent-panel-attention--unknown");
    if (state === "unknown") {
      attentionEl.classList.add("agent-panel-attention--unknown");
      attentionEl.textContent = "attention: ?";
      attentionEl.title = "Could not read /api/view -- session state is UNKNOWN, not clear.";
      return;
    }
    if (count > 0) {
      attentionEl.classList.add("agent-panel-attention--attention");
      attentionEl.textContent = count + " need" + (count === 1 ? "s" : "") + " you";
    } else {
      attentionEl.textContent = "all clear";
    }
    attentionEl.title = "Live count of sessions in the current view flagged " +
      "needs_attention by the server -- the same flag the deck reads.";
  }

  async function pollAttentionOnce() {
    try {
      var resp = await fetch("/api/view?sort=attention", { method: "GET" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      var data = await resp.json();
      var sessions = (data && data.sessions) || [];
      var n = sessions.filter(function (s) { return !!s.needs_attention; }).length;
      renderAttention("ok", n);
    } catch (e) {
      // Loud, not silent: an unreadable server is not an empty queue.
      console.warn("chat panel: attention poll failed:", e);
      renderAttention("unknown", 0);
    }
  }

  function startAttentionPolling() {
    if (attentionTimer !== null) return;
    pollAttentionOnce();
    attentionTimer = setInterval(pollAttentionOnce, ATTENTION_POLL_MS);
  }

  function stopAttentionPolling() {
    if (attentionTimer === null) return;
    clearInterval(attentionTimer);
    attentionTimer = null;
  }

  /** Collapsed tool result: summary on screen, full payload on demand. */
  function appendToolResult(name, raw) {
    var det = document.createElement("details");
    det.className = "agent-msg-tool";
    var sum = document.createElement("summary");
    sum.className = "agent-msg-tool-summary";
    sum.textContent = "tool result (" + name + "): " + summarizeToolResult(raw);
    var pre = document.createElement("pre");
    pre.className = "agent-msg-tool-raw";
    pre.textContent = raw;
    det.appendChild(sum);
    det.appendChild(pre);
    messagesEl.appendChild(det);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  /** Human-readable description of what happens after the literal text --
   * "Enter", "Enter, then: C-c", "C-c" (enter:false), or an explicit
   * no-op label when neither applies. Used by both the confirmation dialog
   * and the transcript status lines so they always describe the SAME
   * outcome, worded the same way. */
  function describeKeys(spec) {
    var enter = typeof spec.enter === "boolean" ? spec.enter : true;
    var keys = Array.isArray(spec.keys) ? spec.keys : [];
    var parts = [];
    if (enter) parts.push("Enter");
    parts = parts.concat(keys);
    if (!parts.length) return "nothing else (no Enter, no keys)";
    return parts.join(", then ");
  }

  /** The confirmation gate for send_muxplex_session_input. Shows the exact
   * session name and exact literal text (never paraphrased) in a real modal
   * -- native <dialog>.showModal(), which blocks and focus-traps the whole
   * page, not just an inline chat bubble -- and returns a Promise that
   * resolves true only if the human clicks the Send button. Every other
   * path (Cancel click, backdrop click, Escape/'cancel' event, or the
   * dialog closing for any other reason) resolves false; see
   * resolveConfirm(). Cancel is focused on open, so doing nothing lands on
   * the safe outcome. Throws (does not silently queue) if a gate is already
   * open -- two overlapping confirmations is a bug to surface, not paper
   * over with an implicit fallback. */
  function requestInputConfirmation(sessionName, text, keySpec) {
    if (pendingConfirmResolve) {
      throw new Error(
        "chat panel: a confirmation gate is already open -- refusing to open a second one"
      );
    }
    return new Promise(function (resolve) {
      confirmSessionEl.textContent = sessionName;
      confirmTextEl.textContent = text === "" ? "(empty -- no literal text)" : text;
      confirmKeysEl.textContent = describeKeys(keySpec);
      pendingConfirmResolve = resolve;
      confirmBackdropEl.classList.remove("hidden");
      confirmDialogEl.showModal();
      confirmCancelBtn.focus(); // safe default: doing nothing lands on Cancel
    });
  }

  /** Settle the currently-open confirmation gate exactly once. The pending
   * resolver is cleared BEFORE closing the dialog, so the 'close' event that
   * closing triggers (fired for every close reason, including this one)
   * finds no pending resolver and is a no-op -- the outcome is decided by
   * whichever call reaches here first, never overwritten afterward. */
  function resolveConfirm(confirmed) {
    if (!pendingConfirmResolve) return;
    var resolve = pendingConfirmResolve;
    pendingConfirmResolve = null;
    if (confirmDialogEl.open) confirmDialogEl.close();
    confirmBackdropEl.classList.add("hidden");
    resolve(confirmed);
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
      var resp = await apiFetch("GET", "/api/sessions");
      if (!resp.ok) {
        throw new Error("GET /api/sessions failed: HTTP " + resp.status);
      }
      var sessions = resp.json || [];
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
      var resp2 = await apiFetch("GET", url);
      if (!resp2.ok) {
        // Fail loud with the real server error (e.g. muxplex's own 404 body
        // "Session 'x' not found") -- no silent catch, no fake-empty result.
        throw new Error(
          "GET " + url + " failed: HTTP " + resp2.status + (resp2.text ? " -- " + resp2.text : "")
        );
      }
      var detail = resp2.json;
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
      var connectResp = await apiFetch("POST", connectUrl);
      if (!connectResp.ok) {
        // Fail loud with muxplex's real error (e.g. its own 404 "Session 'x'
        // not found") -- no silent catch, no fake-success result.
        throw new Error(
          "POST " + connectUrl + " failed: HTTP " + connectResp.status +
          (connectResp.text ? " -- " + connectResp.text : "")
        );
      }
      var connectResult = connectResp.json;
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
      var viewResp = await apiFetch("GET", "/api/view");
      if (!viewResp.ok) {
        throw new Error(
          "GET /api/view failed: HTTP " + viewResp.status + (viewResp.text ? " -- " + viewResp.text : "")
        );
      }
      var viewData = viewResp.json || {};
      var validViews = viewData.views || [];
      if (validViews.indexOf(args.view) === -1) {
        throw new Error(
          "chat panel: unknown view " + JSON.stringify(args.view) +
          ". Valid views right now: " + validViews.join(", ")
        );
      }
      var patchResp = await apiFetch("PATCH", "/api/state", {
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active_view: args.view }),
      });
      if (!patchResp.ok) {
        throw new Error(
          "PATCH /api/state failed: HTTP " + patchResp.status + (patchResp.text ? " -- " + patchResp.text : "")
        );
      }
      var patchResult = patchResp.json || {};
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

      // Nothing-fires-without-a-human-beat gate. This is the ONE tool that
      // reaches this line every single time it's called -- there is no path
      // through this function that reaches the fetch() below without a
      // human clicking Send in requestInputConfirmation()'s dialog first,
      // no matter how many times the model calls this tool in this turn or
      // across turns. The status line names the CONCRETE action (exact
      // session + exact text), not a generic spinner, so the transcript
      // itself is a safety record even if the modal is missed or dismissed
      // without reading it.
      appendSystemLine(
        "awaiting your confirmation: type " + JSON.stringify(inputBody.text) +
        " into session \"" + args.session_name + "\" (then: " +
        describeKeys(inputBody) + ")"
      );
      capPush("confirmation_requested", {
        tool_call_id: toolCall.id,
        session_name: args.session_name,
        text: inputBody.text,
        keys_description: describeKeys(inputBody),
      });
      var userConfirmed = await requestInputConfirmation(
        args.session_name, inputBody.text, inputBody
      );
      capPush("confirmation_resolved", { tool_call_id: toolCall.id, confirmed: userConfirmed });
      if (!userConfirmed) {
        appendSystemLine(
          "cancelled: you declined to send input to session \"" + args.session_name + "\""
        );
        throw new Error(
          "User declined to confirm sending input to session '" + args.session_name +
          "'. Do not retry this exact call in this turn -- tell the user it was declined."
        );
      }
      appendSystemLine(
        "sending " + JSON.stringify(inputBody.text) + " to session \"" + args.session_name + "\"..."
      );

      var inputUrl = "/api/sessions/" + encodeURIComponent(args.session_name) + "/input";
      var inputResp = await apiFetch("POST", inputUrl, {
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
        throw new Error(
          "POST " + inputUrl + " failed: HTTP " + inputResp.status +
          (inputResp.text ? " -- " + inputResp.text : "")
        );
      }
      var inputResult = inputResp.json || {};
      return JSON.stringify({
        ok: inputResult.ok,
        session: inputResult.session,
        snapshot: inputResult.snapshot,
      });
    }

    if (name === "list_muxplex_federated_sessions") {
      var fedResp = await apiFetch("GET", "/api/federation/sessions");
      if (!fedResp.ok) {
        throw new Error(
          "GET /api/federation/sessions failed: HTTP " + fedResp.status +
          (fedResp.text ? " -- " + fedResp.text : "")
        );
      }
      var fedSessions = fedResp.json || [];
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
    requestIndex++;
    var requestStartedAt = performance.now();
    var body = {
      model: MODEL,
      stream: true,
      messages: [{ role: "system", content: SYSTEM_PROMPT }].concat(messages),
      tools: TOOLS,
    };

    capPush("request_start", {
      url: "/api/agent/chat/completions",
      model: MODEL,
      tool_names: TOOLS.map(function (t) { return t.function.name; }),
      client_session_id: clientSessionId,
      // The exact messages POSTed, not just a count -- includes any prior
      // tool results now folded into history. Content is truncated
      // per-message (terminal scrollback lives here after a
      // get_muxplex_session_details/send_muxplex_session_input round trip),
      // never dropped wholesale.
      messages: body.messages.map(function (m) {
        return {
          role: m.role,
          content: typeof m.content === "string" ? truncateForCapture(m.content) : m.content,
          tool_call_id: m.tool_call_id,
          tool_calls: m.tool_calls,
        };
      }),
    });

    var resp = null;
    var transportErr = null;
    try {
      resp = await fetch("/api/agent/chat/completions", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Client-Session-Id": clientSessionId,
        },
        body: JSON.stringify(body),
      });
    } catch (fetchErr) {
      transportErr = fetchErr;
    }

    if (transportErr) {
      capPush("request_error", {
        transport_error: String(transportErr && transportErr.message || transportErr),
        duration_ms: Math.round(performance.now() - requestStartedAt),
      });
      throw transportErr; // preserve original behavior: propagate to handleSend's catch
    }

    if (!resp.ok || !resp.body) {
      var errText = await resp.text().catch(function () { return ""; });
      capPush("request_error", {
        http_status: resp.status,
        body_raw: truncateForCapture(errText),
        duration_ms: Math.round(performance.now() - requestStartedAt),
      });
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
    var sseChunkId = null; // this request's chunk id, from the SSE wire -- the sidecar's own
    // per-request log correlator (see amplifier_agent_http's "chat-completion
    // start chunk_id=%s ... client_session_id=%r" log line).

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

        if (chunk.id && !sseChunkId) sseChunkId = chunk.id;
        capPush("sse_chunk", { chunk: chunk });

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

      capPush("request_end", {
        finish_reason: finishReason,
        chunk_id: sseChunkId,
        assistant_text: truncateForCapture(assistantText),
        tool_call_count: toolCalls.length,
        duration_ms: Math.round(performance.now() - requestStartedAt),
      });
      capPush("tool_calls_requested", {
        tool_calls: toolCalls.map(function (t) {
          return { id: t.id, name: t.function.name, arguments_raw: t.function.arguments };
        }),
      });

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
        var toolStartedAt = performance.now();
        try {
          resultContent = await executeToolCall(tc2);
          // Summary on screen, full payload one tap away. `resultContent`
          // itself is untouched and still goes to the model verbatim below
          // -- the capture event immediately below also gets it raw.
          appendToolResult(tc2.function.name, resultContent);
          capPush("tool_call_result", {
            tool_call_id: tc2.id,
            name: tc2.function.name,
            arguments_raw: tc2.function.arguments,
            ok: true,
            result_raw: truncateForCapture(resultContent),
            duration_ms: Math.round(performance.now() - toolStartedAt),
          });
        } catch (toolErr) {
          resultContent = JSON.stringify({ error: String(toolErr && toolErr.message || toolErr) });
          appendError("chat panel: tool execution failed: " + resultContent);
          capPush("tool_call_result", {
            tool_call_id: tc2.id,
            name: tc2.function.name,
            arguments_raw: tc2.function.arguments,
            ok: false,
            error: String(toolErr && toolErr.message || toolErr),
            duration_ms: Math.round(performance.now() - toolStartedAt),
          });
        }
        messages.push({ role: "tool", tool_call_id: tc2.id, content: resultContent });
      }

      // Continue the same turn -- this is the re-POST the spec describes.
      await runTurn();
      return;
    }

    capPush("request_end", {
      finish_reason: finishReason,
      chunk_id: sseChunkId,
      assistant_text: truncateForCapture(assistantText),
      tool_call_count: 0,
      duration_ms: Math.round(performance.now() - requestStartedAt),
    });

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

    turnIndex++;
    requestIndex = -1; // runTurn() increments this to 0 on its first call for this turn
    capPush("user_message", { text: text, app_state: snapshotAppState() });

    sendBtn.disabled = true;
    try {
      await runTurn();
    } catch (err) {
      appendError("chat panel: request failed: " + String(err && err.message || err));
      capPush("turn_error", { error: String(err && err.message || err) });
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
    exportBtn = $("chat-export-btn");
    exportLinkEl = $("chat-export-link");

    confirmBackdropEl = $("chat-confirm-backdrop");
    confirmDialogEl = $("chat-confirm-dialog");
    confirmSessionEl = $("chat-confirm-session");
    confirmTextEl = $("chat-confirm-text");
    confirmKeysEl = $("chat-confirm-keys");
    confirmCancelBtn = $("chat-confirm-cancel-btn");
    confirmSendBtn = $("chat-confirm-send-btn");

    var __missing = [];
    if (!panelEl) __missing.push("chat-panel");
    if (!messagesEl) __missing.push("chat-messages");
    if (!inputEl) __missing.push("chat-input");
    if (!sendBtn) __missing.push("chat-send-btn");
    if (!newBtn) __missing.push("chat-new-btn");
    if (!closeBtn) __missing.push("chat-close-btn");
    if (!openBtn) __missing.push("chat-open-btn");
    if (!exportBtn) __missing.push("chat-export-btn");
    // The confirmation gate is required for init, not optional: if any part
    // of it is missing, the panel must not come up at all -- the dangerous
    // tool must never be reachable without its gate. Failing loud here
    // (same pattern as the checks above) guarantees that by construction,
    // rather than by a runtime check that a future edit could accidentally
    // skip.
    if (!confirmBackdropEl) __missing.push("chat-confirm-backdrop");
    if (!confirmDialogEl) __missing.push("chat-confirm-dialog");
    if (!confirmSessionEl) __missing.push("chat-confirm-session");
    if (!confirmTextEl) __missing.push("chat-confirm-text");
    if (!confirmKeysEl) __missing.push("chat-confirm-keys");
    if (!confirmCancelBtn) __missing.push("chat-confirm-cancel-btn");
    if (!confirmSendBtn) __missing.push("chat-confirm-send-btn");
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

    // The attention badge is deliberately NOT in the fatal __missing check
    // above: it is an added signal, not part of the panel's own contract.
    // If the markup is absent the panel must still work -- but say so, so a
    // missing badge can never be mistaken for "nothing needs you".
    attentionEl = $("chat-attention");
    if (!attentionEl) {
      console.warn("chat panel: #chat-attention not found -- panel will not " +
        "show the live needs-attention count while open");
    }

    newConversation();

    function togglePanel() {
      var nowHidden = panelEl.classList.toggle("hidden");
      // Poll only while visible: this panel is a foreground surface, and a
      // background timer hitting /api/view forever is exactly the kind of
      // cost nobody asked for.
      if (nowHidden) {
        stopAttentionPolling();
      } else {
        startAttentionPolling();
      }
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
      stopAttentionPolling();
    });
    newBtn.addEventListener("click", newConversation);
    exportBtn.addEventListener("click", exportCaptureRecord);
    sendBtn.addEventListener("click", handleSend);
    inputEl.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    });

    // Confirmation gate wiring. Exactly one path resolves true: an explicit
    // click on Send. Every other path -- Cancel click, backdrop click,
    // Escape (fires the dialog's native 'cancel' event), or the dialog
    // closing for any other reason (native 'close' event, which fires
    // unconditionally on every close including Send's own) -- resolves
    // false. resolveConfirm()'s guard means whichever of these fires first
    // decides the outcome; the rest become no-ops.
    confirmCancelBtn.addEventListener("click", function () { resolveConfirm(false); });
    confirmSendBtn.addEventListener("click", function () { resolveConfirm(true); });
    confirmBackdropEl.addEventListener("click", function () { resolveConfirm(false); });
    confirmDialogEl.addEventListener("cancel", function () { resolveConfirm(false); });
    confirmDialogEl.addEventListener("close", function () { resolveConfirm(false); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
