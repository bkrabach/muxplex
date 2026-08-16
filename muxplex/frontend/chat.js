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
          "fails with the server's real 403 error. Never retry it and never " +
          "imply you can work around it -- but the error text itself tells " +
          "the user how a local operator unblocks it, so relay that instead " +
          "of dead-ending on \"not possible\". Separately, and even when " +
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
    "If it's disabled you will get a real 403 back. Never retry it and never " +
    "invent a way around it -- but do NOT stop at \"no workaround\" either: " +
    "the 403 you get back explains which of the two fences refused, names the " +
    "settings file and the two settings that control it, and says that only a " +
    "local operator (often the person you are talking to) can change it. Pass " +
    "that on in plain language. Every call " +
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

  var panelEl, messagesEl, inputEl, sendBtn, newBtn, openBtn, exportBtn, exportLinkEl;
  // muxplex-fx1: the "Agent isn't set up" gate -- see checkAgentGate()/
  // setGateState() near init() and #chat-gate's comment in index.html.
  var gateEl, gateTextEl, gateSettingsBtn, headerEl, composerEl, bylineEl;

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

  // ---------------------------------------------------------------------
  // Markdown export (muxplex-6oq)
  // ---------------------------------------------------------------------
  // Export used to write the raw JSON record above. It now writes MARKDOWN,
  // and markdown is the only format -- no dropdown, no format chooser.
  //
  // That was a decision the owner explicitly left open, on one condition:
  // "Make that call based on whether anything actually imports the JSON --
  // do not build a format chooser for a format nobody reads." A repo-wide
  // search for the JSON's own format string ("muxplex-agent-panel-debug-
  // record"), its filename prefix, and its format_version found exactly
  // three hits, all of them inside this file -- i.e. the producer and
  // nothing else. Nothing in muxplex, its tests, its docs, its deck, or its
  // tooling has ever read one. So there is no data-driven consumer to serve,
  // and the actual use case -- paste it into an LLM session, or read it
  // yourself -- is served strictly better by prose.
  //
  // WHAT IS DELIBERATELY COLLAPSED, and why that is not a fidelity loss:
  //   * Per-token SSE chunks. These are the bulk of the JSON by count (one
  //     event per streamed token) and carry nothing the concatenated
  //     assistant text does not already say. Collapsed to a count, with the
  //     chunk id kept -- the chunk id is the correlator, not the chunks.
  //   * The re-POSTed message history on each continuation request. The
  //     provider requires the whole conversation on every round trip, so the
  //     JSON contains turn 1 verbatim once per subsequent request. Collapsed
  //     to a role/size manifest; the turns themselves are rendered above it.
  //   * The response body of SUCCESSFUL HTTP calls, clipped at 1200 chars
  //     with a visible marker. The tool result derived from it is printed in
  //     full right below, unclipped -- that is what the model actually saw.
  //
  // WHAT IS NEVER COLLAPSED OR CLIPPED, because it is the reason this
  // feature exists: any FAILING HTTP call's method, URL, exact status code
  // and exact response body; every tool call's arguments; every tool
  // result the model was handed; every confirmation-gate transition; every
  // console error, page error and unhandled rejection; and both correlation
  // ids (the conversation id and each request's chunk id) that let an
  // engineer grep the sidecar's own journal for the matching entries.
  //
  // Still local-only: a Blob object URL and a synthetic click on a
  // persistent anchor. No network request, nothing automatic, nothing
  // transmitted anywhere.

  var MD_OK_BODY_CLIP = 1200; // successful HTTP bodies only -- failures are never clipped

  function fence(lang, body) {
    // Long backtick runs inside terminal scrollback would otherwise break out
    // of a 3-backtick fence; widen the fence past anything in the payload.
    var longest = 0;
    String(body).replace(/`+/g, function (m) { longest = Math.max(longest, m.length); return m; });
    var bar = new Array(Math.max(3, longest + 1) + 1).join("`");
    return bar + (lang || "") + "\n" + String(body) + "\n" + bar;
  }

  function clipBody(text, limit) {
    text = String(text == null ? "" : text);
    if (text.length <= limit) return text;
    return text.slice(0, limit) + "\n[... " + (text.length - limit) + " more characters omitted]";
  }

  function kb(n) {
    return (n / 1024).toFixed(1) + " KB";
  }

  /** Render one tool call -- its arguments, the HTTP it performed, its
   * confirmation gate if it had one, and its result or its failure. */
  function mdToolCall(call, evs) {
    var L = [];
    L.push("#### tool: `" + call.name + "`  (id `" + call.id + "`)");
    var args = call.arguments_raw;
    L.push("Arguments:");
    L.push(fence("json", args && args.trim() ? args : "{}"));

    var gate = evs.filter(function (e) {
      return (e.type === "confirmation_requested" || e.type === "confirmation_resolved") &&
        e.tool_call_id === call.id;
    });
    gate.forEach(function (e) {
      if (e.type === "confirmation_requested") {
        L.push("Confirmation gate: REQUESTED -- session `" + e.session_name +
          "`, text " + JSON.stringify(e.text) + ", then " + e.keys_description + ".");
      } else {
        L.push("Confirmation gate: " + (e.confirmed ? "CONFIRMED by the user." : "DECLINED by the user."));
      }
    });

    // HTTP calls made while this tool ran. apiFetch records them in order, so
    // the ones between this call's start and its result belong to it.
    (call._http || []).forEach(function (n) {
      if (n.transport_error) {
        L.push("HTTP: `" + n.method + " " + n.url + "` -- **TRANSPORT ERROR** after " +
          n.duration_ms + " ms: " + n.transport_error);
        return;
      }
      L.push("HTTP: `" + n.method + " " + n.url + "` -> **" + n.status + "**" +
        (n.ok ? "" : "  <-- FAILED") + "  (" + n.duration_ms + " ms)");
      if (n.request_body) {
        L.push("Request body:");
        L.push(fence("json", clipBody(n.request_body, MD_OK_BODY_CLIP)));
      }
      if (!n.ok) {
        // Verbatim, never clipped. This is the thing you opened the file for.
        L.push("Response body (verbatim):");
        L.push(fence("", n.body_raw == null ? "(empty)" : n.body_raw));
      } else if (n.body_raw) {
        L.push("Response body (clipped -- the tool result below is what the model saw):");
        L.push(fence("json", clipBody(n.body_raw, MD_OK_BODY_CLIP)));
      }
    });

    var res = call._result;
    if (!res) {
      L.push("Result: **NONE RECORDED** -- the tool never returned. Something " +
        "interrupted the turn between the call and its result.");
    } else if (res.ok) {
      L.push("Result (ok, " + res.duration_ms + " ms) -- exactly what the model was handed:");
      L.push(fence("json", res.result_raw));
    } else {
      L.push("Result: **FAILED** after " + res.duration_ms + " ms:");
      L.push(fence("", res.error));
    }
    return L.join("\n\n");
  }

  /** The whole conversation as markdown. Pure function of captureEvents --
   * safe to call repeatedly, and safe to call for a size comparison without
   * side effects. */
  function buildMarkdownRecord() {
    var evs = captureEvents;
    var L = [];
    L.push("# muxplex agent panel -- conversation record");
    L.push("");
    L.push("- **Generated:** " + nowIso());
    L.push("- **Conversation id (`client_session_id`):** `" + clientSessionId + "`");
    L.push("- **Model:** `" + MODEL + "`");
    L.push("- **Page:** " + location.href);
    L.push("- **Viewport:** " + window.innerWidth + "x" + window.innerHeight);
    L.push("- **User agent:** " + navigator.userAgent);
    L.push("- **Events captured:** " + evs.length + (captureCapped ? " (CAPPED -- buffer full, later events dropped)" : ""));
    L.push("");
    L.push("> To line this up against the agent sidecar's own log, grep its journal");
    L.push("> (`journalctl -u amplifier-agent-http`) for the conversation id above --");
    L.push("> it appears on every `chat-completion start` line -- or for the per-request");
    L.push("> chunk id printed under each request below, which is tighter.");
    L.push("");
    L.push("> Nothing here was transmitted anywhere. This file was written locally");
    L.push("> from this browser tab, on an explicit click.");

    // Index events by turn -> request so the flat log can be re-grouped.
    var turns = {};
    var turnOrder = [];
    evs.forEach(function (e) {
      var t = e.turn == null ? -1 : e.turn;
      if (!turns[t]) { turns[t] = []; turnOrder.push(t); }
      turns[t].push(e);
    });

    turnOrder.sort(function (a, b) { return a - b; }).forEach(function (t) {
      var tev = turns[t];
      if (t < 0) {
        // Pre-conversation / unattached events (conversation_new, any console
        // error raised before the first message).
        var pre = tev.filter(function (e) { return e.type !== "conversation_new"; });
        if (!pre.length) return;
        L.push("");
        L.push("## Before the first message");
        pre.forEach(function (e) { L.push(mdLooseEvent(e)); });
        return;
      }

      var um = tev.filter(function (e) { return e.type === "user_message"; })[0];
      L.push("");
      L.push("## Turn " + (t + 1) + " -- user");
      L.push("");
      L.push(um ? fence("", um.text) : "_(no user message recorded for this turn)_");
      if (um && um.app_state) {
        var st = um.app_state;
        L.push("");
        L.push("App state at this turn: " + (st.rendered_session_names || []).length +
          " session(s) on screen" +
          ((st.rendered_session_names || []).length ? " (" + st.rendered_session_names.join(", ") + ")" : "") +
          ", panel " + (st.panel_open ? "open" : "closed") +
          ", viewport " + (st.viewport ? st.viewport.width + "x" + st.viewport.height : "?") +
          ", " + st.conversation_message_count + " message(s) in history.");
      }

      // Group this turn's events by request index.
      var reqs = {};
      var reqOrder = [];
      tev.forEach(function (e) {
        var r = e.request == null ? -1 : e.request;
        if (!reqs[r]) { reqs[r] = []; reqOrder.push(r); }
        reqs[r].push(e);
      });

      reqOrder.sort(function (a, b) { return a - b; }).forEach(function (r) {
        if (r < 0) return;
        var rev = reqs[r];
        var start = rev.filter(function (e) { return e.type === "request_start"; })[0];
        var end = rev.filter(function (e) { return e.type === "request_end"; })[0];
        var rerr = rev.filter(function (e) { return e.type === "request_error"; })[0];
        var chunks = rev.filter(function (e) { return e.type === "sse_chunk"; });

        L.push("");
        L.push("### Request " + (r + 1) +
          (end && end.chunk_id ? "  (`chunk_id` `" + end.chunk_id + "`)" : ""));

        if (start) {
          var roles = {};
          (start.messages || []).forEach(function (m) { roles[m.role] = (roles[m.role] || 0) + 1; });
          var manifest = Object.keys(roles).map(function (k) { return roles[k] + " " + k; }).join(", ");
          L.push("`POST " + start.url + "` with " + (start.messages || []).length +
            " message(s) [" + manifest + "] and " + (start.tool_names || []).length + " tool(s) declared.");
        }
        if (rerr) {
          L.push("**REQUEST FAILED** after " + rerr.duration_ms + " ms" +
            (rerr.http_status ? " -- HTTP **" + rerr.http_status + "**" : "") +
            (rerr.transport_error ? " -- transport error: " + rerr.transport_error : "") + ".");
          if (rerr.body_raw) {
            L.push("Response body (verbatim):");
            L.push(fence("", rerr.body_raw));
          }
        }
        if (end) {
          L.push("Finished `" + end.finish_reason + "` in " + end.duration_ms + " ms" +
            " -- " + chunks.length + " SSE chunk(s) (per-token deltas collapsed; the" +
            " assistant text they concatenate into is below)" +
            (end.tool_call_count ? ", " + end.tool_call_count + " tool call(s)" : "") + ".");
          if (end.assistant_text && end.assistant_text.trim()) {
            L.push("");
            L.push("**assistant:**");
            L.push("");
            L.push(end.assistant_text);
          }
        }

        // Tool calls: stitch each requested call to its result and to the
        // HTTP traffic recorded between them.
        var requested = rev.filter(function (e) { return e.type === "tool_calls_requested"; })[0];
        if (requested) {
          var results = rev.filter(function (e) { return e.type === "tool_call_result"; });
          var nets = rev.filter(function (e) { return e.type === "network_call"; });
          var netIdx = 0;
          requested.tool_calls.forEach(function (c) {
            var call = { id: c.id, name: c.name, arguments_raw: c.arguments_raw, _http: [], _result: null };
            var res = results.filter(function (e) { return e.tool_call_id === c.id; })[0];
            call._result = res || null;
            // apiFetch's network_call events are appended in execution order,
            // and executeToolCall runs the calls in order, so consuming them
            // in sequence attributes each HTTP round trip to the right tool.
            if (res) {
              while (netIdx < nets.length && nets[netIdx].seq < res.seq) {
                call._http.push(nets[netIdx]);
                netIdx++;
              }
            }
            L.push("");
            L.push(mdToolCall(call, rev));
          });
        }

        rev.filter(function (e) {
          return e.type === "console_error" || e.type === "console_warn" ||
            e.type === "window_error" || e.type === "unhandled_rejection" ||
            e.type === "turn_error" || e.type === "capture_capped";
        }).forEach(function (e) { L.push(""); L.push(mdLooseEvent(e)); });
      });
    });

    L.push("");
    return L.join("\n") + "\n";
  }

  /** Anything that is not part of the request/tool spine: errors, warnings,
   * the capture cap. Never silently dropped. */
  function mdLooseEvent(e) {
    if (e.type === "console_error" || e.type === "console_warn") {
      return "- **" + (e.type === "console_error" ? "console.error" : "console.warn") + "** " +
        e.ts + ": " + (e.args || []).map(function (a) {
          return typeof a === "string" ? a : JSON.stringify(a);
        }).join(" ");
    }
    if (e.type === "window_error") {
      return "- **uncaught error** " + e.ts + ": " + e.message +
        " (" + e.filename + ":" + e.lineno + ":" + e.colno + ")" +
        (e.stack ? "\n" + fence("", e.stack) : "");
    }
    if (e.type === "unhandled_rejection") {
      return "- **unhandled promise rejection** " + e.ts + ": " + JSON.stringify(e.reason);
    }
    if (e.type === "turn_error") {
      return "- **turn failed** " + e.ts + ": " + e.error;
    }
    if (e.type === "capture_capped") {
      return "- **capture capped** " + e.ts + ": " + e.note;
    }
    return "- " + e.type + " " + e.ts;
  }

  // The previous export's Blob object URL, so it can be revoked once a new
  // export supersedes it (rather than on a fixed timer -- there's no way to
  // know how long a slow download or a manual "save as" dialog needs it).
  var lastExportUrl = null;

  /** The ONLY export path: writes a local .md file via a Blob object URL and
   * a synthetic click on the persistent #chat-export-link anchor -- never a
   * network request, never anything automatic. Sensitive-by-default: tool
   * results can contain live terminal scrollback, so this never fires
   * without the user explicitly clicking Export. Failures are reported in
   * the transcript AND re-thrown -- never a silent no-op button click. */
  function exportCaptureRecord() {
    var md;
    try {
      md = buildMarkdownRecord();
    } catch (buildErr) {
      appendError("chat panel: failed to build the conversation record: " +
        String(buildErr && buildErr.message || buildErr));
      throw buildErr;
    }
    var blob = new Blob([md], { type: "text/markdown" });
    var url = URL.createObjectURL(blob);
    if (lastExportUrl) URL.revokeObjectURL(lastExportUrl);
    lastExportUrl = url;
    exportLinkEl.href = url;
    exportLinkEl.download = "muxplex-agent-" + clientSessionId + "-" + Date.now() + ".md";
    exportLinkEl.click();
    // Say what the collapsing actually bought, with both numbers, rather than
    // asserting "token optimized" and leaving the user to take it on faith.
    var jsonSize = JSON.stringify(buildCaptureRecord(), null, 2).length;
    appendSystemLine(
      "exported this conversation as markdown (" + captureEvents.length + " events, " +
      kb(md.length) + (captureCapped ? ", capture capped" : "") +
      "; the raw JSON equivalent would be " + kb(jsonSize) + ") to a local file -- " +
      "nothing was sent anywhere."
    );
  }

  function $(id) {
    return document.getElementById(id);
  }

  // ---------------------------------------------------------------------
  // Composer sizing and editing helpers (muxplex-8qp)
  // ---------------------------------------------------------------------

  // STATED MAXIMUM. The composer grows with its content up to this height and
  // then scrolls internally instead of continuing to eat the transcript. 160px
  // is roughly eight lines at the desktop 13px, five at the phone 16px --
  // enough to see a paragraph you are composing, not enough to push the
  // conversation you are replying to off the top of the panel.
  // ---------------------------------------------------------------------
  // Composer send/newline mode (muxplex-18f)
  // ---------------------------------------------------------------------
  // Two modes, chosen per device in the Agent settings tab:
  //
  //   MODE A "enter-newline" (DEFAULT)
  //     Enter / Shift+Enter / Alt+Enter / Ctrl+J -> newline
  //     Ctrl+Enter or Cmd+Enter                  -> send
  //     Matches muxplex's own command bar. CRITICAL FOR TOUCH: a phone
  //     keyboard has no chords, so this is the ONLY mode in which multiline
  //     entry is possible at all. It must stay the default and must stay
  //     reachable -- do not make mode B the default "because desktop".
  //
  //   MODE B "enter-send"
  //     Enter                                    -> send
  //     Shift+Enter / Alt+Enter / Ctrl+J         -> newline
  //     Matches amplifier-app-cli, for people who want the terminal app's
  //     muscle memory. Ctrl/Cmd+Enter still sends here too: it is a strict
  //     superset, and a chord that sends in one mode must never silently
  //     become a newline in the other.
  //
  // Storage is localStorage -- PER DEVICE, deliberately NOT a muxplex server
  // setting and explicitly NOT federation-synced, because the right answer
  // genuinely differs between a phone and a desktop. Key naming follows the
  // established 'muxplex-<feature>' convention (see app.js's
  // COMPOSE_PREF_STORAGE_KEY / SYNC_GROUP_STORAGE_KEY).
  //
  // getSendMode() is the ONE source of truth: the keydown handler branches
  // on it and applyComposerKeyMode() writes every surface that DESCRIBES the
  // chord from it. A hint that disagrees with the handler is worse than no
  // hint, so there must never be a second place that decides this.
  var SEND_MODE_KEY = "muxplex-agent-send-mode";
  var SEND_MODE_NEWLINE = "enter-newline"; // Mode A
  var SEND_MODE_SEND = "enter-send";       // Mode B

  /** The active mode. Anything unrecognised (or a blocked localStorage)
   * falls back to Mode A -- the mode that works on every device. */
  function getSendMode() {
    try {
      var v = localStorage.getItem(SEND_MODE_KEY);
      if (v === SEND_MODE_SEND || v === SEND_MODE_NEWLINE) return v;
    } catch (e) {
      // localStorage blocked (private mode, storage partitioning) -- stay on
      // the default for this session rather than failing the composer.
    }
    return SEND_MODE_NEWLINE;
  }

  /** Persist the mode and immediately re-describe it everywhere. Returns the
   * value actually stored, which is never an unrecognised one. */
  function setSendMode(mode) {
    var m = mode === SEND_MODE_SEND ? SEND_MODE_SEND : SEND_MODE_NEWLINE;
    try { localStorage.setItem(SEND_MODE_KEY, m); } catch (e) { /* blocked -- ok */ }
    applyComposerKeyMode();
    return m;
  }

  /** Rewrite every surface that DESCRIBES the send chord, from the single
   * value the handler BRANCHES on. Three surfaces, one source: the visible
   * byline hint, the textarea's aria-keyshortcuts (what a screen reader
   * announces), and the Send button's tooltip. */
  function applyComposerKeyMode() {
    var sends = getSendMode() === SEND_MODE_SEND;
    var hint = document.getElementById("chat-key-hint");
    if (hint) hint.textContent = (sends ? "Enter" : "Ctrl+Enter") + " to send";
    if (inputEl) {
      inputEl.setAttribute("aria-keyshortcuts", sends ? "Enter" : "Control+Enter Meta+Enter");
    }
    if (sendBtn) {
      sendBtn.title = sends
        ? "Send (Enter). Shift+Enter and Ctrl+J insert a newline instead of sending."
        : "Send (Ctrl+Enter, or Cmd+Enter on a Mac). Enter, Shift+Enter and Ctrl+J " +
          "all insert a newline instead of sending.";
    }
  }

  var COMPOSER_MAX_PX = 160;

  /** Resize the composer to fit its content, bounded. Sets height to "auto"
   * first because scrollHeight only shrinks back down once the element is
   * not already being held open by its own inline height. */
  function autoGrowInput() {
    if (!inputEl) return;
    inputEl.style.height = "auto";
    var needed = inputEl.scrollHeight;
    var h = Math.min(needed, COMPOSER_MAX_PX);
    inputEl.style.height = h + "px";
    // Only show a scrollbar once there is genuinely something to scroll --
    // a permanently-scrollable box with two lines in it looks broken.
    inputEl.style.overflowY = needed > COMPOSER_MAX_PX ? "auto" : "hidden";
  }

  /** Insert a newline at the caret, replacing any selection, and keep the
   * caret after it. Used only by Ctrl+J, whose browser default (open the
   * downloads panel) has to be suppressed -- so the newline the user asked
   * for has to be produced explicitly rather than left to the textarea. */
  function insertNewlineAtCursor() {
    var start = inputEl.selectionStart;
    var end = inputEl.selectionEnd;
    var v = inputEl.value;
    inputEl.value = v.slice(0, start) + "\n" + v.slice(end);
    inputEl.selectionStart = inputEl.selectionEnd = start + 1;
    autoGrowInput();
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
    appendEmptyState();
    // The conversation id is still recorded here, and still travels in the
    // exported record and in the X-Client-Session-Id header -- it is the
    // string an engineer greps the sidecar journal with. muxplex-z6h removed
    // it from the SCREEN, not from the record: it was the first thing the
    // panel said to a human, which one reviewer called "the equivalent of a
    // person introducing themselves with their employee ID number."
    capPush("conversation_new", { client_session_id: clientSessionId });
  }

  /** What occupies the transcript before the first message: an actual
   * starting point, in the user's own vocabulary. Removed the moment the
   * conversation has real content (see handleSend), so it is an opening
   * line rather than permanent chrome. */
  function appendEmptyState() {
    var div = document.createElement("div");
    div.className = "agent-msg-empty";
    div.id = "chat-empty-state";
    div.textContent =
      "Ask about your sessions \u2014 what's running, what one of them is " +
      "printing right now, or switch the dashboard to a different session " +
      "or view.";
    messagesEl.appendChild(div);
  }

  function clearEmptyState() {
    var el = document.getElementById("chat-empty-state");
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }

  // Branding pass: these three renderers used to set raw inline styles
  // (arbitrary hex colors unrelated to the app's palette). They now assign
  // class names defined in style.css (.agent-msg-*), which use the same
  // --bg-surface/--border/--text/--accent tokens as the rest of muxplex.
  // DOM shape, text content, and scroll behavior are unchanged.
  // ---------------------------------------------------------------------
  // Announcements to assistive tech (muxplex-46p, WCAG 4.1.3)
  // ---------------------------------------------------------------------
  // The transcript itself is deliberately NOT a live region (see the
  // aria-live="off" on #chat-messages in index.html and the comment there).
  // A live transcript on a token-by-token stream announces once per token,
  // which is the naive fix the review specifically called out. Instead the
  // accumulated text is buffered here and flushed into a hidden live region
  // at CLAUSE boundaries, at most every LIVE_FLUSH_MS -- so a screen reader
  // hears "Three sessions are running: counter, logtail and sysmon." once,
  // not thirty times mid-word.
  //
  // Silence is the other failure, so the buffer is also flushed
  // unconditionally at the end of a turn: whatever is left over is announced
  // even if it never reached a full stop.

  var LIVE_FLUSH_MS = 1200;
  var liveEl = null;
  var liveFullText = "";   // everything streamed so far this turn
  var liveAnnounced = 0;   // how much of it has been announced
  var liveTimer = null;

  function announceNow(text) {
    if (!liveEl || !text) return;
    // aria-atomic="true", so replacing the content re-announces the whole
    // of it -- one complete thought per write, which is the point.
    liveEl.textContent = text;
  }

  function flushLive() {
    liveTimer = null;
    if (!liveEl) return;
    var pending = liveFullText.slice(liveAnnounced);
    if (!pending) return;
    // Announce only up to the last sentence/line boundary, so nothing is
    // ever read out mid-clause. If there is no boundary yet, wait for one.
    var m = /^[\s\S]*[.!?\n:](?=\s|$)/.exec(pending);
    if (!m) {
      liveTimer = setTimeout(flushLive, LIVE_FLUSH_MS);
      return;
    }
    liveAnnounced += m[0].length;
    announceNow(m[0].trim());
    if (liveFullText.length > liveAnnounced) {
      liveTimer = setTimeout(flushLive, LIVE_FLUSH_MS);
    }
  }

  /** Called with the ACCUMULATED assistant text on every content delta. */
  function announceStreamed(fullSoFar) {
    liveFullText = fullSoFar;
    if (!liveTimer) liveTimer = setTimeout(flushLive, LIVE_FLUSH_MS);
  }

  /** End of a turn: say whatever is left, even if it never reached a full
   * stop, then reset for the next turn. Silence would be the worse bug. */
  function announceStreamEnd() {
    if (liveTimer) { clearTimeout(liveTimer); liveTimer = null; }
    var rest = liveFullText.slice(liveAnnounced).trim();
    if (rest) announceNow(rest);
    liveFullText = "";
    liveAnnounced = 0;
  }

  /** A short, complete status sentence -- announced immediately rather than
   * buffered, because these are what tell a non-sighted user that something
   * is happening at all during the long silent gap while a tool runs. */
  function announceStatus(text) {
    if (liveTimer) { clearTimeout(liveTimer); liveTimer = null; }
    var rest = liveFullText.slice(liveAnnounced).trim();
    liveAnnounced = liveFullText.length;
    announceNow(rest ? rest + " " + text : text);
  }

  // ---------------------------------------------------------------------
  // Minimal markdown renderer (muxplex-04m)
  // ---------------------------------------------------------------------
  // The model emits markdown -- **bold**, bullet lists, `code` -- and the
  // panel used to print the asterisks and backticks verbatim.
  //
  // SAFETY IS STRUCTURAL HERE, NOT A SANITISER. This renderer NEVER touches
  // innerHTML, never builds an HTML string, and never parses HTML. It walks
  // the markdown source and builds DOM with createElement/createTextNode.
  // Any HTML in the source -- <script>, <img onerror=...>, an unclosed tag,
  // anything -- reaches the page as a TEXT NODE, because a text node is the
  // only thing this code can produce for content it does not recognise as
  // markdown. There is no escaping step to forget and no sanitiser
  // allowlist to get wrong, which matters: what renders here is model
  // output and tool results, i.e. content this panel does not control.
  //
  // Links are the one construct that could still smuggle behaviour (a
  // `javascript:` href), so the href is allowlisted to http/https by regex
  // at the point of matching -- anything else never becomes an anchor at
  // all and stays literal text.
  //
  // Applied to ASSISTANT output only. A user's own message is echoed back
  // verbatim: rendering it as markdown would be surface for no benefit, and
  // silently reformatting what someone just typed is its own small lie.

  // Ordered alternation: a code span is matched FIRST and its contents are
  // consumed whole, so nothing inside backticks is ever re-parsed as
  // emphasis. Bold before italic so ** is not read as two nested *.
  //
  // Held as a SOURCE STRING, and a fresh RegExp is built per call. That is
  // not stylistic -- it is a bug fix. mdInline() recurses (bold can contain
  // code, a link label can contain bold), and a /g regex carries mutable
  // `lastIndex` state on the object itself. Sharing one instance meant an
  // inner call reset and advanced the SAME lastIndex the outer loop was
  // walking with, so the outer loop resumed from an unrelated offset and
  // could re-match the same position forever. Measured, not theorised: it
  // hung the render loop and took the whole browser tab down with it, on
  // the first response that contained bold text.
  var MD_INLINE_SRC =
    "(`+)([\\s\\S]*?)\\1" +                        // 1,2  `code`
    "|\\*\\*([\\s\\S]+?)\\*\\*" +                  // 3    **bold**
    "|__([\\s\\S]+?)__" +                          // 4    __bold__
    "|\\*([^*\\n]+?)\\*" +                         // 5    *italic*
    "|_([^_\\n]+?)_" +                             // 6    _italic_
    "|\\[([^\\]\\n]*)\\]\\((https?://[^\\s)]+)\\)"; // 7,8  [text](https://...)

  // Recursion depth guard. Belt-and-braces beside the per-call regex above:
  // this renders untrusted model output, and a pathological string must
  // degrade to plain text, never to a hung tab.
  var MD_MAX_DEPTH = 8;

  function mdInline(parent, text, depth) {
    text = String(text);
    depth = depth || 0;
    if (depth >= MD_MAX_DEPTH) {
      parent.appendChild(document.createTextNode(text));
      return;
    }
    var re = new RegExp(MD_INLINE_SRC, "g");
    var last = 0;
    var m;
    while ((m = re.exec(text)) !== null) {
      if (m[0].length === 0) { re.lastIndex++; continue; } // cannot stall
      if (m.index > last) {
        parent.appendChild(document.createTextNode(text.slice(last, m.index)));
      }
      if (m[2] !== undefined) {
        var code = document.createElement("code");
        code.className = "agent-md-code";
        code.textContent = m[2];
        parent.appendChild(code);
      } else if (m[3] !== undefined || m[4] !== undefined) {
        var strong = document.createElement("strong");
        mdInline(strong, m[3] !== undefined ? m[3] : m[4], depth + 1);
        parent.appendChild(strong);
      } else if (m[5] !== undefined || m[6] !== undefined) {
        var em = document.createElement("em");
        mdInline(em, m[5] !== undefined ? m[5] : m[6], depth + 1);
        parent.appendChild(em);
      } else if (m[8] !== undefined) {
        var a = document.createElement("a");
        a.href = m[8]; // already constrained to http/https by the pattern
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.className = "agent-md-link";
        mdInline(a, m[7] || m[8], depth + 1);
        parent.appendChild(a);
      }
      last = m.index + m[0].length;
    }
    if (last < text.length) {
      parent.appendChild(document.createTextNode(text.slice(last)));
    }
  }

  /** Render markdown source into `el`, replacing its contents. Block level:
   * fenced code, headings, bullet and numbered lists, paragraphs. Anything
   * unrecognised falls through to a paragraph of literal text -- the
   * renderer degrades to plain text rather than to nothing. */
  function renderMarkdownInto(el, src) {
    el.textContent = "";
    var lines = String(src).split("\n");
    var i = 0;

    function inlineOf(tag, text) {
      var node = document.createElement(tag);
      mdInline(node, text);
      return node;
    }

    while (i < lines.length) {
      var line = lines[i];

      var fence = /^\s*(```+|~~~+)\s*([A-Za-z0-9_+-]*)\s*$/.exec(line);
      if (fence) {
        var marker = fence[1];
        var body = [];
        i++;
        while (i < lines.length && !new RegExp("^\\s*" + marker[0] + "{" + marker.length + ",}\\s*$").test(lines[i])) {
          body.push(lines[i]);
          i++;
        }
        i++; // consume the closing fence (or run off the end -- unclosed is fine)
        var pre = document.createElement("pre");
        pre.className = "agent-md-pre";
        var codeEl = document.createElement("code");
        codeEl.textContent = body.join("\n");
        pre.appendChild(codeEl);
        el.appendChild(pre);
        continue;
      }

      var heading = /^(#{1,6})\s+(.*)$/.exec(line);
      if (heading) {
        var h = inlineOf("div", heading[2]);
        h.className = "agent-md-heading";
        el.appendChild(h);
        i++;
        continue;
      }

      var bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
      var numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
      if (bullet || numbered) {
        var ordered = !!numbered;
        var list = document.createElement(ordered ? "ol" : "ul");
        list.className = "agent-md-list";
        while (i < lines.length) {
          var b = /^\s*[-*+]\s+(.*)$/.exec(lines[i]);
          var n = /^\s*\d+[.)]\s+(.*)$/.exec(lines[i]);
          if (ordered ? !n : !b) break;
          list.appendChild(inlineOf("li", (ordered ? n : b)[1]));
          i++;
        }
        el.appendChild(list);
        continue;
      }

      if (!line.trim()) { i++; continue; }

      // Paragraph: consecutive non-blank lines that do not start a block.
      var para = [];
      while (i < lines.length && lines[i].trim() &&
             !/^\s*(```+|~~~+)/.test(lines[i]) &&
             !/^#{1,6}\s+/.test(lines[i]) &&
             !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
        para.push(lines[i]);
        i++;
      }
      if (para.length) {
        var p = inlineOf("p", para.join("\n"));
        p.className = "agent-md-p";
        el.appendChild(p);
      }
    }
  }

  /** Swap a finished assistant turn from streamed plain text to rendered
   * markdown. Deliberately done ONCE, at the end of the turn, not on every
   * delta: mid-stream the source is routinely half-written (an unclosed
   * fence, a lone `**`), so re-rendering per token would flash malformed
   * structure at the reader and cost a full re-parse per token for it.
   * While streaming, the raw text is shown as-is -- which is honest, and is
   * what the panel did before this. */
  function finishAssistantBubble(bodyEl, text) {
    if (!bodyEl || !text) return;
    renderMarkdownInto(bodyEl, text);
    bodyEl.classList.add("agent-msg-body--md");
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  /** Screen-reader-only role label. WCAG 1.4.1 is about not using colour as
   * the ONLY visual cue -- the CSS handles that with offset, corner shape
   * and a left rule -- but a screen reader gets no cue from any of those,
   * so each message also carries its role as real (hidden) text. */
  function roleLabel(text) {
    var span = document.createElement("span");
    span.className = "agent-msg-role";
    span.textContent = text + ": ";
    return span;
  }

  function appendSystemLine(text) {
    var div = document.createElement("div");
    div.className = "agent-msg-system";
    div.appendChild(roleLabel("Status"));
    div.appendChild(document.createTextNode(text));
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    announceStatus(text);
  }

  function appendBubble(role) {
    var div = document.createElement("div");
    div.className = "agent-msg-bubble " +
      (role === "user" ? "agent-msg-bubble--user" : "agent-msg-bubble--assistant");
    div.appendChild(roleLabel(role === "user" ? "You" : "Agent"));
    // The text node the caller writes into, kept separate from the hidden
    // role label so assigning to it can never wipe the label out.
    var body = document.createElement("span");
    body.className = "agent-msg-body";
    div.appendChild(body);
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return body;
  }

  function appendError(text) {
    var div = document.createElement("div");
    div.className = "agent-msg-error";
    // role="alert" makes this its own assertive live region: an error is the
    // one thing that must interrupt rather than queue politely behind a
    // streaming response.
    div.setAttribute("role", "alert");
    div.appendChild(roleLabel("Error"));
    div.appendChild(document.createTextNode(text));
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

  // ---------------------------------------------------------------------
  // What the agent is DOING right now (muxplex-l2y)
  // ---------------------------------------------------------------------
  // The transcript used to say "model requested tool call(s):
  // get_muxplex_session_details" -- the function name, in the model's
  // vocabulary, not the user's -- and then nothing at all for the seconds
  // the tool actually took. During that gap the token stream produces
  // nothing, and silence reads as broken.
  //
  // A generic spinner would be the wrong fix and was explicitly ruled out:
  // motion manufactured to feel alive, carrying no information. So the
  // status line names the CONCRETE action, in ordinary words, including the
  // session or view it is about.
  //
  // READS AND WRITES ARE DELIBERATELY DIFFERENT REGISTERS. Reading a
  // session's output and typing keystrokes into it are not the same kind of
  // event and must not look the same. Reads are muted with a neutral
  // marker; the one write tool gets its own treatment, names the target
  // session AND the exact text, and stays on screen while the confirmation
  // gate is open -- which makes the status line the cheapest safety net
  // there is: the moment you can see, and stop, something before it lands.

  var statusEl = null;      // the single live status row, or null
  var statusWatchdog = null; // fires if a turn goes quiet for too long
  var STATUS_STALL_MS = 15000;

  /** Human phrasing for a tool call, and which register it belongs to.
   * Falls back to the raw tool name rather than inventing a description for
   * a tool this function has not been taught -- an honest "running X" beats
   * a confident wrong sentence. */
  function describeToolAction(name, args) {
    args = args || {};
    var sess = args.session_name ? '"' + args.session_name + '"' : "a session";
    if (name === "list_muxplex_sessions") {
      return { kind: "read", text: "Reading your list of sessions" };
    }
    if (name === "list_muxplex_federated_sessions") {
      return { kind: "read", text: "Reading sessions across every connected device" };
    }
    if (name === "get_muxplex_session_details") {
      return { kind: "read", text: "Reading what " + sess + " is showing right now" };
    }
    if (name === "switch_muxplex_session") {
      return { kind: "read", text: "Switching the dashboard to " + sess };
    }
    if (name === "switch_muxplex_view") {
      return { kind: "read", text: "Switching the view to \"" + (args.view || "?") + "\"" };
    }
    if (name === "send_muxplex_session_input") {
      return {
        kind: "write",
        text: "Typing " + JSON.stringify(typeof args.text === "string" ? args.text : "") +
          " into " + sess,
      };
    }
    // Sentinel used for a mid-stream SSE error frame (muxplex-695), not a
    // real tool call -- give it a phrasing of its own so the generic
    // fallback below ("Could not finish running __stream__.") never
    // reaches the screen.
    if (name === "__stream__") {
      return { kind: "read", text: "Getting the agent's response" };
    }
    return { kind: "read", text: "Running " + name };
  }

  /** Show (or replace) the single status row at the foot of the transcript.
   * kind: "read" | "write" | "wait" | "stalled". */
  function setStatus(kind, text) {
    clearStatusWatchdog();
    if (!statusEl) {
      statusEl = document.createElement("div");
      messagesEl.appendChild(statusEl);
    }
    statusEl.className = "agent-status agent-status--" + kind;
    statusEl.textContent = "";
    statusEl.appendChild(roleLabel("Status"));
    statusEl.appendChild(document.createTextNode(text));
    messagesEl.appendChild(statusEl); // keep it last, even after new bubbles
    messagesEl.scrollTop = messagesEl.scrollHeight;
    announceStatus(text);
  }

  function clearStatus() {
    clearStatusWatchdog();
    if (statusEl && statusEl.parentNode) statusEl.parentNode.removeChild(statusEl);
    statusEl = null;
  }

  function clearStatusWatchdog() {
    if (statusWatchdog) { clearTimeout(statusWatchdog); statusWatchdog = null; }
  }

  /** Arm the stall detector. If nothing arrives for STATUS_STALL_MS the row
   * stops claiming to be working and says so instead -- "appears to still be
   * working" is precisely the failure this guards against. */
  function armStallWatch(what) {
    clearStatusWatchdog();
    var startedAt = Date.now();
    statusWatchdog = setTimeout(function () {
      if (!statusEl) return;
      var secs = Math.round((Date.now() - startedAt) / 1000);
      setStatus("stalled", "No response for " + secs + "s while " +
        what + ". It may still finish, or the connection may have dropped.");
      armStallWatch(what); // keep counting rather than going quiet again
    }, STATUS_STALL_MS);
  }

  // ---------------------------------------------------------------------
  // Humanised tool failures (muxplex-oi2, and the 403 remedy of muxplex-5so)
  // ---------------------------------------------------------------------
  // What the user used to see, verbatim:
  //   chat panel: tool execution failed: {"error":"POST /api/sessions/
  //   counter/input failed: HTTP 403 -- {\"detail\":\"Session input is
  //   disabled (settings.input_enabled=false)\"}"}
  // A JSON string inside a JSON string, an HTTP status, and an internal
  // config key, handed to someone who just wanted to type into a terminal.
  //
  // The rule now: ONE plain sentence about what happened and what they can
  // do, with the raw text still one click away. Transparency is a feature
  // of this tool; being the primary reading surface is not.

  /** Which of muxplex's two input fences refused a 403, from the server's OWN
   * wording: "Session input is disabled (settings.input_enabled=false)" (the
   * GLOBAL switch) versus "...does not match any input_allowed_sessions
   * pattern" (the ALLOWLIST). Returns "global" or "allowlist".
   *
   * muxplex-9n9: this used to be re-derived at render time by testing the
   * WHOLE thrown message for /input_allowed_sessions/. That message also
   * carries the model-facing guidance, which NAMES BOTH SETTINGS in every
   * case -- so the test matched unconditionally and every refusal was
   * reported as the allowlist case, telling users to edit the wrong setting
   * while displaying a server detail that said the opposite. Two defences
   * now: the fence is classified once at the throw site and carried on the
   * Error (err.inputFence), and this function only ever looks at the part
   * BEFORE the blank line -- the server's own text -- so even the fallback
   * path cannot read the guidance prose.
   *
   * Keying on the PRESENCE of the allowlist phrase (rather than the absence
   * of the other) means an unrecognised future message degrades to the
   * global case -- the more conservative advice -- instead of confidently
   * naming the wrong fence. */
  function classifyInputFence(text) {
    var serverPart = String(text || "").split("\n\n")[0];
    return /input_allowed_sessions/i.test(serverPart) ? "allowlist" : "global";
  }

  /** Turn a thrown tool error into { headline, remedy } in plain language.
   * `remedy` may be null when there is genuinely nothing the user can do.
   * `err` is the original Error when one is available; its structured
   * fields are preferred over anything re-parsed out of the message. */
  function humaniseToolError(name, message, err) {
    var msg = String(message || "");
    var action = describeToolAction(name, {});
    var m403 = /HTTP 403/.test(msg);
    var m404 = /HTTP 404/.test(msg);
    var m5xx = /HTTP 5\d\d/.test(msg);

    // A mid-stream SSE error frame (muxplex-695: name === "__stream__",
    // see runTurn()'s `if (chunk.error)` handling). muxplex's own proxy
    // names this exact phrase when the sidecar can't be reached at all --
    // by far the most common cause is the sidecar failing to START (a bad
    // or missing provider API key), not a live network problem, so say
    // that plainly and point straight at the sidecar's own log instead of
    // repeating the proxy's network-flavoured wording. See
    // docs/AGENT_CHAT_SETUP.md \u00a77 for the full diagnosis; this message
    // exists so reading that doc is not required for the common case.
    if (name === "__stream__" && /agent sidecar unreachable/i.test(msg)) {
      return {
        headline: "The agent sidecar isn't running.",
        remedy: "This is almost always a missing or invalid provider API key at " +
          "sidecar startup, not a network problem. Its own log names the exact " +
          "reason -- on the box running the sidecar: " +
          "journalctl -u amplifier-agent-http -n 50",
      };
    }

    if (name === "send_muxplex_session_input" && m403) {
      // A 403 here is not a fault. It is a permission state with a known
      // human remedy, and the person reading this is usually the one person
      // who can apply it. Never say "no workaround" -- see muxplex-5so.
      // The fence the THROWER classified wins; classifyInputFence() is only
      // the fallback for a 403 this file did not compose.
      var fence = (err && err.inputFence) || classifyInputFence(msg);
      var globallyOff = fence !== "allowlist";
      return {
        headline: globallyOff
          ? "muxplex is not accepting typed input into any session yet."
          : "muxplex is not accepting typed input into that particular session.",
        remedy: globallyOff
          ? "This is off by default on purpose. Whoever runs muxplex on that machine can turn it " +
            "on by editing ~/.config/muxplex/settings.json and setting input_enabled to true, then " +
            "listing the sessions they want to allow in input_allowed_sessions. If that is you, " +
            "you are the only person who can: both settings are deliberately local-file-only and " +
            "cannot be changed from this or any other API, including by the agent."
          : "Typed input is on, but this session is not on the allowlist. Whoever runs muxplex can " +
            "add it to input_allowed_sessions in ~/.config/muxplex/settings.json. That file is " +
            "deliberately the only way -- the allowlist cannot be changed through any API, " +
            "including by the agent.",
      };
    }
    if (/User declined/i.test(msg)) {
      return {
        headline: "You declined that, so nothing was sent.",
        remedy: "Ask again if you change your mind -- every one of these needs a fresh confirmation.",
      };
    }
    if (m404) {
      return {
        headline: "muxplex could not find that session.",
        remedy: "It may have been closed or renamed. Ask for the list of sessions to see what is live.",
      };
    }
    if (m403) {
      return { headline: "muxplex refused that request.", remedy: null };
    }
    // muxplex-at9: the 503 muxplex's OWN proxy returns when
    // AMPLIFIER_AGENT_BEARER_TOKEN is unset -- i.e. the agent was never
    // installed/configured on THIS server (main.py's
    // agent_chat_completions_proxy). That is the default starting state
    // for every fresh muxplex install, not a transient fault, so
    // "worth retrying" (the generic m5xx phrasing just below) is actively
    // wrong advice here -- retrying can never help until an operator
    // configures it. Checked on the server's OWN wording, not on `name`:
    // this failure reaches here through the plain `!resp.ok` path in
    // runTurn() (name === "__request__"), never the SSE error-frame path,
    // so it must not be scoped to name === "__stream__" the way the
    // sidecar-unreachable case above is.
    //
    // In normal use nobody should ever see this: checkAgentGate() (below)
    // blanks the panel before a turn can even be attempted once the panel
    // is known to be unconfigured. This stays as the humanised fallback for
    // the rare race (gate check said "configured" a moment ago, sidecar
    // credential removed since) or a gate-check failure that fails open.
    //
    // Kept deliberately short -- one clause, no doc path. Most installs are
    // via `uv tool install`, which never sees README.md or docs/, so a
    // docs/AGENT_CHAT_SETUP.md reference here is dead text for nearly every
    // reader; Settings -> Agent is reachable from inside the app itself.
    // Anything more than this belongs behind a disclosure, the same way
    // the raw response text already sits behind "technical detail" below --
    // not stacked into a second visible paragraph.
    if (m5xx && /not configured on this server/i.test(msg)) {
      return {
        headline: "The Agent isn't set up on this server yet.",
        remedy: "Set it up from Settings -> Agent.",
      };
    }
    if (m5xx) {
      return {
        headline: "muxplex hit an error of its own while handling that.",
        remedy: "Worth retrying once; if it keeps happening, the server log will have the detail.",
      };
    }
    if (/NetworkError|Failed to fetch|TypeError: |load failed/i.test(msg)) {
      return {
        headline: "Could not reach muxplex.",
        remedy: "The connection dropped or the server is down. Check it is still running.",
      };
    }
    return {
      headline: "Could not finish " + action.text.charAt(0).toLowerCase() + action.text.slice(1) + ".",
      remedy: null,
    };
  }

  /** Render a failed tool call: a sentence, a remedy if one exists, and the
   * raw text collapsed underneath. */
  function appendToolError(name, message, err) {
    var h = humaniseToolError(name, message, err);
    var div = document.createElement("div");
    div.className = "agent-msg-error";
    div.setAttribute("role", "alert");
    div.appendChild(roleLabel("Error"));

    // muxplex-fx1: everything readable lives in ONE wrapper, separate from
    // the icon. .agent-msg-error's own ::before glyph used to precede the
    // headline DIRECTLY -- a block-level div right after an inline glyph,
    // which forces the glyph onto a line by itself with the headline text
    // starting fresh underneath it ("the '!' icon is on a line of its own
    // before the text"). Wrapping headline+remedy+details in one flex
    // sibling next to the icon (see .agent-msg-error's flex rule in
    // style.css) puts the icon and the first line of text on the same row,
    // the same way .agent-status already does for its own ::before glyph.
    var body = document.createElement("div");
    body.className = "agent-msg-error-body";

    var head = document.createElement("div");
    head.className = "agent-msg-error-headline";
    head.textContent = h.headline;
    body.appendChild(head);

    if (h.remedy) {
      var rem = document.createElement("div");
      rem.className = "agent-msg-error-remedy";
      rem.textContent = h.remedy;
      body.appendChild(rem);
    }

    // Raw detail, collapsed. Never the primary reading surface, never gone.
    //
    // muxplex-ixl: this renders err.userDetail -- the server's own response
    // -- NOT the thrown message. The thrown message also carries guidance
    // addressed to the model ("TELL THE USER exactly this", "Do NOT retry
    // this call"), which was appearing on screen and reading as the app
    // talking to itself in front of the customer. The model still receives
    // that guidance in full; see the catch site in runTurn(). Errors with no
    // userDetail (every other tool path) are unchanged and show as before.
    var det = document.createElement("details");
    det.className = "agent-msg-tool";
    var sum = document.createElement("summary");
    sum.className = "agent-msg-tool-summary";
    sum.textContent = "technical detail";
    var pre = document.createElement("pre");
    pre.className = "agent-msg-tool-raw";
    pre.textContent = (err && err.userDetail) || message;
    det.appendChild(sum);
    det.appendChild(pre);
    body.appendChild(det);

    div.appendChild(body);

    messagesEl.appendChild(div);
    if (statusEl) messagesEl.appendChild(statusEl);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    announceStatus(h.headline + (h.remedy ? " " + h.remedy : ""));
  }

  /** Collapsed tool result: summary on screen, full payload on demand. */
  function appendToolResult(name, raw) {
    var det = document.createElement("details");
    det.className = "agent-msg-tool";
    var sum = document.createElement("summary");
    sum.className = "agent-msg-tool-summary";
    // Human phrasing, not the function name (muxplex-oi2/l2y). The raw
    // payload underneath is unchanged and still one click away.
    sum.textContent = describeToolAction(name, {}).text.replace(/^Reading/, "Read")
      .replace(/^Switching/, "Switched").replace(/^Typing/, "Typed")
      .replace(/^Running/, "Ran") + " \u2014 " + summarizeToolResult(raw);
    var pre = document.createElement("pre");
    pre.className = "agent-msg-tool-raw";
    pre.textContent = raw;
    det.appendChild(sum);
    det.appendChild(pre);
    messagesEl.appendChild(det);
    if (statusEl) messagesEl.appendChild(statusEl); // status stays last
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
        // THIS is the fenced endpoint. A 403 here is muxplex's fence doing
        // exactly its job, and the server's real text is always surfaced
        // verbatim. This code never retries and never tries to widen the
        // fence -- it cannot: both settings are local-file-only by design.
        //
        // muxplex-5so: what it must ALSO do is stop dead-ending the user. A
        // 403 from this endpoint is not a fault, it is a PERMISSION STATE
        // with a known human remedy, and the person reading the answer is
        // usually the exact person who can apply it. The observed failure
        // was the agent replying "No workaround available on my end" while
        // this file knew the setting names, the file path, and that they
        // are operator-only. So the error handed back to the model now
        // carries all of that, and distinguishes the two distinct 403s.
        if (inputResp.status === 403) {
          var fenceText = inputResp.text || "";
          // Which of the two fences refused. Classified ONCE, here, from the
          // server's own raw response -- and carried on the Error as a field
          // so nothing downstream ever has to re-derive it from prose.
          // See classifyInputFence() for why re-deriving it is a trap.
          var fence = classifyInputFence(fenceText);
          // The line a human may read. Server's own words, nothing else.
          var userDetail =
            "POST " + inputUrl + " failed: HTTP 403" +
            (fenceText ? " -- " + fenceText : "");
          // Everything after the blank line is addressed to the MODEL and is
          // never rendered on screen (muxplex-ixl). appendToolError() shows
          // err.userDetail instead. The model still receives all of it,
          // because it is what lets the model explain the remedy.
          var fenceErr = new Error(
            userDetail + "\n\n" +
            "This is NOT a dead end and NOT a fault -- it is a permission state with a " +
            "known human remedy. Terminal input is gated by whoever operates this muxplex, " +
            "in the file ~/.config/muxplex/settings.json on the machine running it, via two " +
            "settings: input_enabled (a global on/off switch, false by default) and " +
            "input_allowed_sessions (a per-session allowlist). " +
            (fence === "global"
              ? "This particular refusal is the GLOBAL switch: input_enabled is false, so no " +
                "session accepts typed input yet. The operator needs to set it to true AND list " +
                "the sessions they want to allow."
              : "input_enabled is already on -- this refusal is the ALLOWLIST: the session " +
                JSON.stringify(args.session_name) + " is not in input_allowed_sessions. The " +
                "operator needs to add that session name to that list.") + " " +
            "Both settings are deliberately local-file-only: they cannot be changed through " +
            "this or any other API call, by you or by anyone else -- only by a person editing " +
            "that file on disk. " +
            "TELL THE USER exactly this: what is blocked, which of the two cases it is, the " +
            "file name and both setting names, and that a local operator (very possibly them) " +
            "is the one who can change it. Do NOT tell them there is no workaround. Do NOT " +
            "retry this call."
          );
          fenceErr.inputFence = fence;
          fenceErr.userDetail = userDetail;
          throw fenceErr;
        }
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
    setStatus("wait", "Thinking about what to do next...");
    armStallWatch("waiting for the agent to respond");
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
      clearStatus();
      appendToolError("__request__",
        "POST /api/agent/chat/completions failed: HTTP " + resp.status +
        (errText ? " -- " + errText : ""));
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

        // A frame carrying `error` (and no `choices`) is a fatal,
        // whole-turn failure -- either muxplex's proxy reporting its own
        // layer (e.g. the sidecar unreachable) or the sidecar forwarding
        // a provider error mid-stream. Both deliberately share this shape
        // (see main.py's agent_chat_completions_proxy docstring: "mirrors
        // the agent's own mid-stream error convention"). This must be
        // handled BEFORE the `!choice` guard below -- previously it fell
        // straight through that guard (an error frame has no `choices`)
        // and the turn ended with nothing rendered at all (muxplex-695).
        // Checked for ANY error frame, not just the sidecar-unreachable
        // message: this is a general blind spot, and the proxy or the
        // sidecar can each emit this shape for different causes.
        if (chunk.error) {
          var streamErrMsg = (chunk.error && chunk.error.message) ||
            JSON.stringify(chunk.error);
          capPush("request_error", {
            sse_error: chunk.error,
            duration_ms: Math.round(performance.now() - requestStartedAt),
          });
          clearStatus();
          appendToolError("__stream__", streamErrMsg);
          return;
        }

        var choice = chunk.choices && chunk.choices[0];
        if (!choice) continue;

        if (choice.delta && typeof choice.delta.content === "string" && choice.delta.content) {
          if (!assistantBubble) { clearStatus(); assistantBubble = appendBubble("assistant"); }
          assistantText += choice.delta.content;
          assistantBubble.textContent = assistantText;
          // Buffered, clause-boundary announcement -- NOT one per token.
          announceStreamed(assistantText);
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


      announceStreamEnd();
      finishAssistantBubble(assistantBubble, assistantText);
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
        // Name the concrete action before doing it, in the right register
        // (muxplex-l2y). Arguments are parsed leniently here purely for
        // phrasing -- executeToolCall does its own strict validation.
        var __args = {};
        try { __args = JSON.parse(tc2.function.arguments || "{}"); } catch (e) { __args = {}; }
        var __act = describeToolAction(tc2.function.name, __args);
        setStatus(__act.kind, __act.text + "...");
        armStallWatch(__act.text.charAt(0).toLowerCase() + __act.text.slice(1));
        try {
          resultContent = await executeToolCall(tc2);
          // Summary on screen, full payload one tap away. `resultContent`
          // itself is untouched and still goes to the model verbatim below
          // -- the capture event immediately below also gets it raw.
          clearStatus();
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
          var __errMsg = String(toolErr && toolErr.message || toolErr);
          // The MODEL still gets the full, unedited error -- including any
          // guidance addressed to it -- because that is what lets it explain
          // the remedy. The USER gets one plain sentence, with only the
          // SERVER's own words collapsed underneath: appendToolError renders
          // toolErr.userDetail when the thrower supplied one, never this
          // blended string (muxplex-ixl).
          resultContent = JSON.stringify({ error: __errMsg });
          clearStatus();
          appendToolError(tc2.function.name, __errMsg, toolErr);
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

    announceStreamEnd();
    finishAssistantBubble(assistantBubble, assistantText);
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
    autoGrowInput(); // collapse the composer back down with its content
    clearEmptyState(); // the opening line has done its job the moment there is a real message
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
      clearStatus();
      appendToolError("__request__", String(err && err.message || err));
      capPush("turn_error", { error: String(err && err.message || err) });
    } finally {
      // Whatever happened, the turn is over: no row may be left saying the
      // agent is still working. This is the "stalled or dropped must be
      // unambiguous" half of muxplex-l2y.
      clearStatus();
      sendBtn.disabled = false;
    }
  }

  // ---------------------------------------------------------------------
  // The "Agent isn't set up" gate (muxplex-fx1)
  // ---------------------------------------------------------------------
  // "I shouldn't have to submit a chat to find out it's broken." Checked
  // every time the panel opens (applyPanelVisualState()'s open branch,
  // below) against the SAME /api/agent/provider-credential endpoint the
  // Settings -> Agent tab already polls (see _fetchAgentCredentialStatus()
  // further down this file) -- one status contract, read from two places,
  // never reimplemented.
  //
  // "not_installed" and "not_configured" both mean the same thing to
  // someone typing a message here: there is nothing to talk to yet.
  // "error" (the status check itself failed) and "configured"/
  // "configured_shadowed" all mean "let the panel behave normally" --
  // failing OPEN on a status-check error, not closed, because blocking the
  // whole panel over a transient check failure would recreate the exact
  // false "looks broken" impression this gate exists to prevent, just for
  // the opposite case. A genuine Agent failure still surfaces normally the
  // moment a real turn is attempted -- humaniseToolError() handles that;
  // this gate only ever pre-empts the KNOWN-inert case.
  var AGENT_GATE_UNCONFIGURED_STATES = ["not_installed", "not_configured"];
  var _gateState = null; // null (never resolved yet) | "configured" | "unconfigured"

  /** Show either the gate or the normal chat UI (header, messages,
   * composer) for one of three states: "checking" (status lookup in
   * flight -- see checkAgentGate for when this is actually shown),
   * "unconfigured", or "configured". */
  function setGateState(state) {
    _gateState = state;
    var unconfigured = state === "unconfigured";
    var checking = state === "checking";
    var gated = unconfigured || checking;
    gateTextEl.textContent = checking
      ? "Checking whether the Agent is set up..."
      : "The Agent isn't set up on this server yet.";
    gateSettingsBtn.classList.toggle("hidden", !unconfigured);
    gateEl.classList.toggle("hidden", !gated);
    headerEl.classList.toggle("hidden", gated);
    messagesEl.classList.toggle("hidden", gated);
    composerEl.classList.toggle("hidden", gated);
    // The byline's send-chord hint ("Ctrl+Enter to send") describes a
    // composer that isn't on screen while gated -- hide it along with the
    // rest of the chrome rather than leave a hint for a control that
    // isn't there.
    bylineEl.classList.toggle("hidden", gated);
  }

  /** Ask muxplex whether the Agent has ever been configured on this
   * install. Called every time the panel opens (never cached across
   * opens, so a key just added in Settings -> Agent takes effect the
   * moment the panel is reopened -- no reload required).
   *
   * Deliberately does NOT reset to "checking" on repeat opens: once a real
   * answer is known, that answer stays on screen while this quietly
   * re-validates in the background, so reopening the panel never flashes
   * a neutral placeholder over a state that was already correct. Only the
   * very first check of the session (there is no answer yet at all) shows
   * "checking" -- see setGateState's callers below and in init(). */
  async function checkAgentGate() {
    if (_gateState === null) setGateState("checking");
    var data;
    try {
      var resp = await fetch("/api/agent/provider-credential", {
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      data = await resp.json();
    } catch (err) {
      // Fails OPEN, loudly: the status check itself failing is not
      // evidence the Agent is unconfigured. Never a silent catch -- logged
      // so a real outage is still discoverable in devtools, even though
      // the panel does not block on it.
      console.error("[agent-gate] could not check whether the Agent is " +
        "configured -- failing open (panel behaves as if configured):", err);
      setGateState("configured");
      return;
    }
    var unconfigured = AGENT_GATE_UNCONFIGURED_STATES.indexOf(data.state) !== -1;
    setGateState(unconfigured ? "unconfigured" : "configured");
  }

  function init() {
    panelEl = $("chat-panel");
    messagesEl = $("chat-messages");
    inputEl = $("chat-input");
    sendBtn = $("chat-send-btn");
    newBtn = $("chat-new-btn");
    openBtn = $("chat-open-btn");
    exportBtn = $("chat-export-btn");
    exportLinkEl = $("chat-export-link");
    headerEl = $("chat-panel-header");
    composerEl = $("chat-composer");
    bylineEl = $("chat-byline");
    gateEl = $("chat-gate");
    gateTextEl = $("chat-gate-text");
    gateSettingsBtn = $("chat-gate-settings-btn");
    // Deliberately NOT in the fatal __missing check below: a missing live
    // region degrades accessibility, it does not break the panel. But it is
    // never silent -- a missing one would otherwise look exactly like a
    // working one to anyone not using a screen reader.
    liveEl = $("chat-live");
    if (!liveEl) {
      console.warn("chat panel: #chat-live not found -- streaming responses " +
        "will NOT be announced to assistive technology");
    }

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
    if (!openBtn) __missing.push("chat-open-btn");
    if (!exportBtn) __missing.push("chat-export-btn");
    // The "Agent isn't set up" gate is required for init, not optional, for
    // the same reason the confirmation gate below is: if it is missing,
    // checkAgentGate() has nothing to render into, and the panel would
    // fall back to always showing a working-looking composer -- silently
    // reintroducing the exact "submit a turn to find out it's broken"
    // failure this gate exists to prevent. Fail loud and don't come up,
    // same as every other required element here.
    if (!headerEl) __missing.push("chat-panel-header");
    if (!composerEl) __missing.push("chat-composer");
    if (!bylineEl) __missing.push("chat-byline");
    if (!gateEl) __missing.push("chat-gate");
    if (!gateTextEl) __missing.push("chat-gate-text");
    if (!gateSettingsBtn) __missing.push("chat-gate-settings-btn");
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

    newConversation();

    // ------------------------------------------------------------------
    // Inline panel placement (muxplex-rle)
    // ------------------------------------------------------------------
    // The panel is no longer a fixed overlay: it is an inline column that
    // sits beside content, below the header bar, exactly the way
    // .session-sidebar does. Both views (#view-overview and #view-expanded)
    // now use the same .view-body flex row, so "beside content" means "the
    // last flex child of the ACTIVE view's .view-body".
    //
    // There is exactly ONE #chat-panel element and it gets reparented,
    // rather than one copy per view: two copies would duplicate every id in
    // that markup (#chat-input, #chat-send-btn, the whole confirmation
    // wiring) and getElementById would silently bind to whichever came
    // first -- a panel that looks fine and types into the wrong DOM.
    //
    // Reparenting a live node preserves its state (the textarea's value is
    // a property, the transcript nodes move with it); it does not re-run
    // any of this file's wiring, because the listeners are bound to the
    // elements, not to their position.

    /** The .view-body of whichever view is currently on screen, or null if
     * the DOM does not look the way this function expects. Never guesses. */
    function activeViewBody() {
      var expanded = document.getElementById("view-expanded");
      var overview = document.getElementById("view-overview");
      var showing = (expanded && !expanded.classList.contains("hidden"))
        ? expanded
        : overview;
      return showing ? showing.querySelector(".view-body") : null;
    }

    var _homeWarned = false;

    /** Move the panel into the active view's content row, if it is not
     * already there. Safe to call repeatedly -- a no-op when already
     * correctly placed, so it can be driven from a MutationObserver
     * without churning the DOM on every unrelated class change. */
    function homeAgentPanel() {
      var host = activeViewBody();
      if (!host) {
        // Loud once, then stop: an unplaced panel still works (it keeps its
        // parking spot at body level), but it will not be beside content,
        // and that is worth saying rather than silently degrading.
        if (!_homeWarned) {
          _homeWarned = true;
          console.warn("chat panel: no .view-body found for the active view -- " +
            "panel cannot be placed inline beside content");
        }
        return;
      }
      if (panelEl.parentNode !== host) host.appendChild(panelEl);
    }

    homeAgentPanel();

    // ------------------------------------------------------------------
    // iOS keyboard-over-composer fix (muxplex-d5v / muxplex-m3n)
    // ------------------------------------------------------------------
    // On iOS the software keyboard is painted OVER the page rather than
    // shrinking it. The composer is pinned to this panel's bottom edge, and
    // at <=959px style.css gives the panel `inset: 0` -- so the moment a
    // user taps the input, Send goes underneath the keyboard.
    //
    // Fix: mirror window.visualViewport's height into --agent-panel-visual-h
    // on this panel, via the SAME mechanism terminal.js established for the
    // terminal view (terminal.js's _trackVisualViewportHeight -- see
    // b7186b0, 7fd3296) rather than a second, parallel one. terminal.js
    // loads before this file (see index.html's <script> order) and exposes
    // the helper on `window`. style.css's <=959px rule reads the variable
    // (`bottom: auto; height: var(--agent-panel-visual-h, 100%)`) so the
    // panel's bottom edge -- and thus the composer/Send pinned to it --
    // rides the actually-visible region instead of the keyboard-obscured
    // one. _trackVisualViewportHeight itself subtracts the panel's own
    // current `getBoundingClientRect().top` from the raw visualViewport
    // height, which is what makes this correct for THIS panel: unlike
    // terminal.js's #view-expanded (which always sits at viewport y=0),
    // #chat-panel is nested below the app's own page-level header, so the
    // raw height alone would overshoot the visible region by that header's
    // rendered height.
    //
    // Handle kept in _panelVpTracker (module-level, alongside the other
    // panel-lifecycle state below) rather than created once here: see
    // syncPanelToVisualViewport()'s own docstring for why the tracker must
    // be (re)created every time the panel actually becomes VISIBLE, not
    // once at this init()-time call site.
    var _panelVpTracker = null;

    /**
     * (Re)create the visualViewport -> --agent-panel-visual-h tracker for
     * this panel. Call this every time the panel transitions from hidden to
     * visible (applyPanelVisualState's open branch, below) -- NOT just once
     * here at init() time.
     *
     * WHY NOT ONCE AT INIT: the panel starts `.hidden` (display:none) until
     * the user opens it (or a persisted `agentPanelOpen:true` setting
     * restores it). `getBoundingClientRect()` on a display:none element
     * always reports `top: 0`, regardless of where the element will render
     * once shown -- so a seed call made while still hidden bakes in the
     * WRONG (zero) top offset. Since opening/closing the panel is not
     * itself a visualViewport `resize`/`scroll` event, that wrong value
     * would never get corrected until a genuine visualViewport change
     * happened to fire afterward -- confirmed as a real, measured
     * regression (44px permanent overshoot, matching the page header's
     * height) via simulated-keyboard browser verification while building
     * this fix (muxplex-m3n). Re-creating the tracker at the moment the
     * panel becomes visible (`.hidden` already removed by the caller
     * before this runs) measures the CORRECT, current top offset instead.
     *
     * Tearing down any previous tracker first keeps this idempotent across
     * repeated opens (no accumulating duplicate visualViewport listeners).
     */
    function syncPanelToVisualViewport() {
      if (typeof window._trackVisualViewportHeight !== "function") return;
      if (_panelVpTracker) {
        _panelVpTracker.teardown();
        _panelVpTracker = null;
      }
      _panelVpTracker = window._trackVisualViewportHeight(panelEl, "--agent-panel-visual-h");
    }

    // app.js switches views by toggling #view-expanded's `hidden`/
    // `view--active` classes (see openSession/closeSession). It does not
    // publish an event for that, and it is not this lane's file to change,
    // so observe the class attribute directly -- one observer, no polling.
    // If the panel is open when the view changes it follows the user across;
    // if it is closed this just keeps its parking spot correct for the next
    // open.
    var expandedViewEl = document.getElementById("view-expanded");
    if (expandedViewEl && typeof MutationObserver === "function") {
      new MutationObserver(function () {
        homeAgentPanel();
      }).observe(expandedViewEl, { attributes: true, attributeFilter: ["class"] });
    } else {
      console.warn("chat panel: cannot observe view switches -- the panel will " +
        "still be re-homed on every open, but not mid-view-change");
    }

    /** Every header entry point for the panel. Both carry the toggle state,
     * so the two headers can never disagree about whether it is open. */
    function agentButtons() {
      var btns = [openBtn];
      var expandedBtn = document.getElementById("chat-open-btn-expanded");
      if (expandedBtn) btns.push(expandedBtn);
      return btns;
    }

    // ------------------------------------------------------------------
    // Focus handling on open/close (muxplex-d5v, and WCAG 2.4.3 from
    // muxplex-46p -- one implementation, not two)
    // ------------------------------------------------------------------
    // On open: the composer takes focus, so typing goes straight in with no
    // intermediate click. Opening the panel IS the signal that you want to
    // use it.
    //
    // On close: focus goes back to the button that opened it. Without this a
    // keyboard user is dumped at the top of a dense dashboard and has to tab
    // all the way back every single time -- and focus lands on a
    // display:none element's former position, which is worse than useless.
    // The button that opened it is remembered, so closing returns you to the
    // header you actually came from rather than always to the overview one.
    //
    // COARSE POINTERS ARE DELIBERATELY EXEMPT FROM THE AUTO-FOCUS. Focusing
    // a text input on a touch device raises the software keyboard
    // immediately, which on this panel would cover the composer's own Send
    // button before the user has read a word of the transcript. That is a
    // real risk and it is NOT verifiable in this environment -- a headless
    // browser has no software keyboard to raise, so "it looked fine in the
    // test" would be a claim about nothing. So the safe, checkable
    // behaviour ships: no auto-focus on a coarse pointer, no keyboard, the
    // composer is one tap away. Focus RESTORATION on close is unconditional
    // -- it costs nothing and raises no keyboard.
    //
    // This is a media QUERY, not a width check: it asks what the pointing
    // device actually is, so a touchscreen laptop at 1400px is treated as
    // touch and a mouse-driven small window is not.
    var finePointer = !window.matchMedia || window.matchMedia("(pointer: fine)").matches;
    var panelIsOpen = false;
    var lastOpener = null;

    /** Apply ONLY the panel's visual open/closed state: the hidden class,
     * aria-pressed on both header buttons, and (when opening) re-homing the
     * panel into the active view. No interactive side effects (focus,
     * autoGrowInput) -- those belong to setPanelOpen below, which wraps this
     * for real user-driven toggles. This split mirrors app.js's
     * initSidebar()/toggleSidebar() split: a plain state-application
     * function, shared by both the init-time restore and the interactive
     * toggle, so there is exactly one definition of what "open" looks like. */
    function applyPanelVisualState(open) {
      panelIsOpen = !!open;
      panelEl.classList.toggle("hidden", !open);
      agentButtons().forEach(function (b) {
        b.setAttribute("aria-pressed", open ? "true" : "false");
      });
      if (open) {
        homeAgentPanel();
        // Re-measure now, the first moment the panel is actually visible --
        // see syncPanelToVisualViewport()'s docstring for why this cannot
        // happen once at init() time instead.
        syncPanelToVisualViewport();
        // muxplex-fx1: re-check every real open (interactive toggle AND
        // the persisted-restore-on-load path below both funnel through
        // here) -- see checkAgentGate()'s own docstring for why this is
        // cheap to repeat and never flashes a placeholder over a state
        // that was already known.
        checkAgentGate();
      }
    }

    /** Single source of truth for "is the panel open", reflected onto both
     * buttons' aria-pressed (which style.css also styles off -- see
     * .header-btn--agent[aria-pressed="true"]) so the visual active state
     * and the accessibility tree cannot drift apart. */
    function setPanelOpen(open, opener) {
      var wasOpen = panelIsOpen;
      applyPanelVisualState(open);
      if (open) {
        // muxplex-2y1: the composer's resting height is guaranteed by CSS
        // min-height, which is what makes first paint correct. This call is
        // the second half: while the panel was display:none, scrollHeight
        // read 0, so any inline height left over from a previous session is
        // stale. Measuring here -- the first moment the element has a real
        // layout box -- makes the height EXACT rather than merely floored.
        autoGrowInput();
        if (opener) lastOpener = opener;
        if (finePointer) inputEl.focus();
      } else if (wasOpen) {
        // `wasOpen` guards the init-time call below: restoring focus to a
        // button at page load would steal it from wherever the user actually
        // is. Only a real close moves focus.
        (lastOpener || openBtn).focus();
      }
    }

    // ------------------------------------------------------------------
    // Persisted open/closed state (muxplex-2qs)
    // ------------------------------------------------------------------
    // Remembers the panel's open/closed state the SAME way the left
    // session sidebar remembers its own (settings.sidebarOpen in
    // muxplex/settings.py's DEFAULT_SETTINGS/SYNCABLE_KEYS): a plain
    // boolean synced through GET/PATCH /api/settings, not a second,
    // parallel localStorage scheme. This file does not reach into app.js's
    // _serverSettings/patchServerSetting internals to do it -- this file is
    // a closed IIFE that talks to muxplex only through its own apiFetch
    // (see the file header's endpoint list and the "ONE deliberate
    // exception" note near the bottom of this file), so it fetches and
    // patches /api/settings directly, using the exact same key
    // (agentPanelOpen) and the exact same server-side contract the sidebar
    // relies on. Same mechanism; independent wiring; no drift possible
    // because there is exactly one key and one server-side definition of
    // it.
    //
    // Unlike sidebarOpen, a null/never-set value does NOT auto-detect from
    // screen width -- the panel is an opt-in secondary tool (opened via a
    // button click), not primary navigation, so "never toggled" simply
    // means "stay closed", which is also today's pre-existing default.
    var _panelUserToggled = false;

    /** PATCH the persisted state. Fire-and-forget from the caller's point
     * of view (matches patchServerSetting's own "log and move on" failure
     * handling) -- a failed save degrades to "this device's next reload
     * uses the last value the server had", never a thrown error the user
     * would see. */
    async function persistPanelOpen(open) {
      try {
        await apiFetch("PATCH", "/api/settings", {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ agentPanelOpen: !!open }),
        });
      } catch (e) {
        console.warn("chat panel: failed to persist agentPanelOpen", e);
      }
    }

    function togglePanel(e) {
      _panelUserToggled = true;
      setPanelOpen(!panelIsOpen, e && e.currentTarget);
      persistPanelOpen(panelIsOpen);
    }

    setPanelOpen(false); // establish aria-pressed="false" before any click

    // Restore the persisted value once it's available. Independent of
    // app.js's own settings load (see note above) -- so this is not racing
    // app.js's DOMContentLoaded handler, only its own GET. If the user
    // manages to toggle the panel before this GET resolves, _panelUserToggled
    // guards against clobbering their fresh, already-persisted choice with
    // this now-stale read.
    (async function restorePersistedPanelState() {
      var resp;
      try {
        resp = await apiFetch("GET", "/api/settings");
      } catch (e) {
        console.warn("chat panel: failed to load agentPanelOpen", e);
        return;
      }
      if (_panelUserToggled) return;
      if (!resp.ok || !resp.json) return;
      var stored = resp.json.agentPanelOpen;
      if (stored === null || stored === undefined) return; // never set -- stay closed
      applyPanelVisualState(!!stored);
    })();

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

    // muxplex-2qs: there is no close X any more. The Agent button IS the
    // toggle, in both headers, and closing through it is what returns focus
    // to it (see setPanelOpen's else-branch). Do not re-add a second close
    // control without also deciding where focus lands when it is used.
    newBtn.addEventListener("click", newConversation);
    exportBtn.addEventListener("click", exportCaptureRecord);
    sendBtn.addEventListener("click", handleSend);

    // muxplex-fx1: pops open Settings already switched to the Agent tab --
    // the SAME openSettings()+switchSettingsTab('agent') pair app.js's own
    // "manage views" dropdown action already calls (bindStaticEventListeners()
    // there); reused rather than a second way to open a settings tab. Both
    // live in app.js as plain (non-module) top-level functions, so they are
    // real globals -- see this file's own window._trackVisualViewportHeight
    // read above for the same cross-file-global convention. Guarded rather
    // than assumed: this file must not throw if app.js's shape ever changes,
    // the same defensiveness _trackVisualViewportHeight gets.
    gateSettingsBtn.addEventListener("click", function () {
      if (typeof window.openSettings === "function") window.openSettings();
      if (typeof window.switchSettingsTab === "function") window.switchSettingsTab("agent");
    });

    // ------------------------------------------------------------------
    // Composer keys (muxplex-8qp)
    // ------------------------------------------------------------------
    // This inverts the old behaviour, deliberately. It used to be
    // Enter = send, Shift+Enter = newline. It is now:
    //
    //   Enter, Shift+Enter, Alt+Enter, Ctrl+J  -> newline, never sends
    //   Ctrl+Enter, Cmd+Enter                  -> send
    //   the Send button                        -> send
    //
    // The failure mode that matters here is silently sending a half-written
    // message, so every path that a habit might reach for is routed to
    // "newline", and only the two explicit chords send. Shift+Enter and
    // Ctrl+J are in that list precisely BECAUSE they are habits from other
    // chat clients -- someone who reaches for them must not be punished by
    // having their draft fired off.
    //
    // Note on how each case is implemented, because they are not the same:
    //  * Plain/Shift/Alt Enter: do NOTHING. A textarea already inserts a
    //    newline on Enter; the correct fix is to stop calling
    //    preventDefault(), not to insert one ourselves.
    //  * Ctrl+J: MUST be intercepted. Its browser default is "open the
    //    downloads panel" in Chrome/Edge/Firefox, so leaving it alone would
    //    yank the user out of the composer instead of adding a line. It is
    //    preventDefault()'d and the newline inserted explicitly.
    //  * Ctrl/Cmd+Enter: preventDefault() as well -- without it the browser
    //    inserts a newline into the draft on its way out, so the input would
    //    be cleared with a stray blank line already typed into the next one.
    //
    // muxplex-18f made the plain-Enter case configurable. What did NOT change:
    // Ctrl/Cmd+Enter sends in BOTH modes, and Shift+Enter / Alt+Enter / Ctrl+J
    // insert a newline in BOTH modes. Only bare Enter differs. That is
    // deliberate -- a chord whose meaning flips between modes is how someone
    // fires off a half-written message after changing a setting they have
    // already forgotten about.
    inputEl.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        if (e.ctrlKey || e.metaKey) {
          e.preventDefault();
          handleSend();
          return;
        }
        // Mode B only: bare Enter sends. Shift/Alt are the escape hatches and
        // stay newline. getSendMode() is read HERE, per keystroke, not cached
        // at wiring time -- the setting can change in another tab or in the
        // settings dialog while this listener is already bound.
        if (getSendMode() === SEND_MODE_SEND && !e.shiftKey && !e.altKey) {
          e.preventDefault();
          handleSend();
        }
        return; // otherwise: the textarea's own newline
      }
      if (e.ctrlKey && !e.metaKey && (e.key === "j" || e.key === "J")) {
        e.preventDefault();
        insertNewlineAtCursor();
      }
    });
    inputEl.addEventListener("input", autoGrowInput);
    autoGrowInput();
    // Describe the chord that is actually active, from the same value the
    // handler above branches on (muxplex-18f).
    applyComposerKeyMode();

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

  // ---------------------------------------------------------------------
  // The panel's per-device preferences, for the Agent settings tab (muxplex-3lr)
  // ---------------------------------------------------------------------
  // This file is otherwise a closed IIFE and contributes ZERO globals -- see
  // AGENTS.md's v0.31.3 shared-scope incident, where two classic scripts each
  // declared a top-level binding of the same name and the second one silently
  // failed to parse. This is the single deliberate exception: the settings
  // dialog lives in app.js and must not re-implement the storage key, the
  // valid values, or the defaulting, because a second implementation of those
  // is exactly how the hint and the handler drift apart.
  //
  // ONE namespaced object, assigned at IIFE-body level rather than inside
  // init(), so it exists even if init() throws on a missing DOM element --
  // the settings tab is then still coherent rather than half-wired.
  window.muxplexAgentPrefs = {
    SEND_MODE_NEWLINE: SEND_MODE_NEWLINE,
    SEND_MODE_SEND: SEND_MODE_SEND,
    getSendMode: getSendMode,
    setSendMode: setSendMode,
  };

  // -------------------------------------------------------------------
  // Agent provider credential (Settings -> Agent) --
  // docs/designs/agent-credentials.md. Same "own the storage/state,
  // expose read-only helpers" shape as muxplexAgentPrefs above: the form
  // lives in index.html (app.js's openSettings() calls into this), the
  // fetch logic and state lives here.
  //
  // NEVER logs, stores, or echoes the key value itself -- only ever
  // forwarded in the POST body to muxplex's own origin, once, on submit.
  // -------------------------------------------------------------------
  async function _fetchAgentCredentialStatus() {
    const resp = await fetch("/api/agent/provider-credential", {
      headers: { Accept: "application/json" },
    });
    if (!resp.ok) {
      throw new Error("status fetch failed: HTTP " + resp.status);
    }
    return resp.json();
  }

  function _renderAgentCredentialStatus(data) {
    const statusEl = document.getElementById("agent-credential-status");
    const shadowEl = document.getElementById("agent-credential-shadow-warning");
    const restartWarnEl = document.getElementById("agent-credential-restart-warning");
    const submitBtn = document.getElementById("agent-credential-submit-btn");
    if (!statusEl) return;

    // muxplex-at9: "not_installed" (the sidecar's service account / CLI
    // binary do not exist on this box -- the feature was never set up
    // here) is a DIFFERENT, more basic state than "error" (the status
    // check itself failed for some other reason) or "not_configured"
    // (installed, but no provider key yet). It is the default starting
    // state for every fresh muxplex install, so this reads as a fact with
    // a next step -- never the raw `sudo: unknown user aa-svc` /
    // audit-plugin stderr this used to fall through to as `state: "error"`
    // (main.py's get_agent_provider_credential no longer even shells out
    // to learn this). The form is disabled rather than left to fail a
    // POST that can only ever be refused (post_agent_provider_credential
    // gives that same clean refusal if this is reached some other way).
    if (data.state === "not_installed") {
      statusEl.textContent = data.message ||
        "The Agent sidecar isn't installed on this server yet. See docs/AGENT_CHAT_SETUP.md " +
        "to install it, then come back here to add a key.";
      if (shadowEl) shadowEl.classList.add("hidden");
      if (restartWarnEl) restartWarnEl.classList.add("hidden");
      if (submitBtn) submitBtn.disabled = true;
      return;
    }
    if (submitBtn) submitBtn.disabled = false;

    if (data.state === "error") {
      statusEl.textContent = "Could not check the Agent's credential status: " + (data.message || "unknown error");
      return;
    }

    const providers = data.providers || {};
    const lines = [];
    for (const p of Object.keys(providers).sort()) {
      const entry = providers[p];
      if (entry.source === "not_set") {
        lines.push(p + ": not configured");
      } else {
        lines.push(p + ": " + (entry.masked || "***") + " (" + entry.source + ")");
      }
    }
    if (data.state === "not_configured") {
      statusEl.textContent = "The Agent has no model provider key. It cannot run until one is set.";
    } else {
      statusEl.textContent = (data.sidecar === "running" ? "Agent running. " : "Agent not responding. ") + lines.join(", ");
    }

    if (shadowEl) {
      shadowEl.classList.toggle("hidden", data.state !== "configured_shadowed");
    }
    // A restart is needed whenever the sidecar isn't already up and
    // serving -- the server decides for real at submit time; this is
    // just the pre-submit heads-up the design calls for.
    if (restartWarnEl) {
      restartWarnEl.classList.toggle("hidden", data.sidecar === "running");
    }
  }

  async function _refreshAgentCredentialStatus() {
    const statusEl = document.getElementById("agent-credential-status");
    if (!statusEl) return; // form not in the DOM (older frontend build)
    try {
      const data = await _fetchAgentCredentialStatus();
      _renderAgentCredentialStatus(data);
    } catch (err) {
      statusEl.textContent = "Could not check the Agent's credential status.";
      console.error("[agent-credential] status fetch failed:", err);
    }
  }

  function _bindAgentCredentialForm() {
    const form = document.getElementById("agent-credential-form");
    const keyInput = document.getElementById("agent-credential-key");
    const providerSelect = document.getElementById("agent-credential-provider");
    const resultEl = document.getElementById("agent-credential-result");
    const submitBtn = document.getElementById("agent-credential-submit-btn");
    if (!form || !keyInput || !providerSelect || !resultEl || !submitBtn) return;

    form.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      const apiKey = keyInput.value;
      if (!apiKey) {
        resultEl.textContent = "Enter a key first (clearing is a separate, explicit action).";
        return;
      }
      submitBtn.disabled = true;
      resultEl.textContent = "Validating...";
      try {
        const resp = await fetch("/api/agent/provider-credential", {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ provider: providerSelect.value, api_key: apiKey }),
        });
        const data = await resp.json().catch(function () { return {}; });
        if (!resp.ok) {
          resultEl.textContent = "Rejected: " + (data.detail || ("HTTP " + resp.status));
          return;
        }
        keyInput.value = ""; // never leave the typed key sitting in the form
        resultEl.textContent = data.restarted
          ? "Key saved. Agent service restarted (" + data.detail + ")."
          : "Key saved. Takes effect on the next turn -- no restart needed.";
        await _refreshAgentCredentialStatus();
      } catch (err) {
        resultEl.textContent = "Request failed: " + err;
      } finally {
        submitBtn.disabled = false;
      }
    });
  }

  window.muxplexAgentCredential = {
    refreshStatus: _refreshAgentCredentialStatus,
    bindForm: _bindAgentCredentialForm,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
