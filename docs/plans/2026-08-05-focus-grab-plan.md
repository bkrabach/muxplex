# Focus Grab — Implementation Specification

Status: **NOT YET BUILT.** Written 2026-08-05 as the specification for
`docs/BACKLOG.md` item 3, "Move focus-grabbing out of the deck and into
muxplex." Retained here — rather than only in the throwaway cross-repo
workspace it was written in — as the specification of record for whenever
this work is picked up. Unlike every other document in this directory, this
one is **not** a record of something already shipped.

**Backlog item:** `muxplex/docs/BACKLOG.md` §3, "Move focus-grabbing out of the deck and into muxplex"
**Target repos:** `muxplex` (v0.38.1), `muxplex-deck` (v0.13.0)

---

## 0. Read this first — what should NOT be built

Four things in the backlog's framing are wrong or unbuildable. Each is argued in
full below; they are collected here so nobody specifies around them.

### 0.1 Linux/Wayland cannot support this. Do not specify it.

There is no portable mechanism, and the reason is structural rather than
missing-work. Wayland's only sanctioned cross-process activation path is
`xdg-activation-v1`, and it requires the **requesting** process to be a Wayland
client that holds a surface and a seat, so it can mint an activation token
against a real input serial and hand that token to the target. muxplex is a
headless HTTP server: no surface, no seat, no input serial, not a Wayland client
at all. It cannot mint a token, so it cannot activate anything.

Compositor-specific escape hatches exist (sway's IPC `focus`, `hyprctl dispatch
focuswindow`, KWin scripting via D-Bus) but there is no portable one and
GNOME-on-Wayland — the most likely desktop a user is actually running — has
none. A per-compositor matrix is exactly the "new client reimplements it" problem
this item exists to end, relocated one layer down.

**Verdict: Wayland returns `501` and says so. It is not a gap to be closed later;
it is a property of the platform.**

### 0.2 Windows has nothing to move to, and the deck's Windows implementation dies with this change

muxplex has **no Windows port**. `README.md`'s platform table lists Linux, macOS,
and WSL only; `muxplex/service.py` dispatches on `_is_darwin()` / `_have_systemctl()`
with no Windows branch; ttyd is bound over `AF_UNIX`. There is no muxplex process
on a Windows desktop for a client to ask.

`muxplex-deck` today *does* implement Windows focus (`focus.py`'s
`_focus_windows`: `EnumWindows` title match, `SendInput` ALT tap,
`AttachThreadInput`, `SetForegroundWindow`). That implementation has no
server-side home and never will until muxplex runs on Windows.

**Verdict: `focus_app` stops working for a Windows-hosted PWA. This is a real
regression, it must be in the changelog, and §7.3 argues why accepting it beats
keeping a second implementation.**

### 0.3 Do not build a server-side broadcast fan-out

The backlog offers "popping the window on all devices" as the *simplifying*
first pass. It is the opposite — it is the expensive option.

A targeted `POST /api/federation/{device_id}/focus` is a near-verbatim copy of
`federation_bell_clear` (`main.py:4291`), a pattern this codebase has shipped
three times. A broadcast requires inventing, from nothing: a partial-failure
response shape, a total timeout budget across N peers, semantics for how a
circuit-open peer is reported, and a decision about whether one slow peer blocks
the caller's key press. None of that exists today because every federation
**write** passthrough is single-target.

**Verdict: no broadcast. §5 gives the targeted design; "raise everywhere" is a
client-side loop over a device list the client already has, and §5.3 argues why
that does not violate `AGENTS.md`'s resolve-it-server-side rule.**

### 0.4 Focus must stay an explicit action, never a consequence of switching sessions

The backlog calls this a UX toss-up. It is not. Making raise a side effect of
`POST /connect` means any client — a poll cycle, a federation peer, an agent,
another human's browser — yanks the physical foreground on someone else's
desktop as a side effect of a routine state write. `muxplex-deck`'s own
`_do_connect` docstring already draws this exact line: focus fires on an
explicit key press and never on a poll-driven repaint.

**Verdict: explicit action only. "Switch and raise" is two calls the client
composes, not one endpoint that does both.**

### 0.5 What IS worth building

`POST /api/focus` on macOS, and nothing else in v1. That single endpoint fixes
the reported bug completely (§1.2), needs no federation, and is roughly 80 lines
of server code plus one call site in each of two clients.

---

## 1. Problem restatement, corrected

### 1.1 muxplex is not "the thing being raised"

The backlog's load-bearing observation is *"muxplex already runs on every device,
and muxplex is the thing being raised."* The first clause is true and is the
whole justification for the move. The second is not: muxplex is a headless
server process with no window. The thing being raised is the **muxplex PWA
window** — a browser app window on that host's desktop.

The correct statement is: **muxplex is the process co-located with the window
that needs raising.** That distinction has two consequences the design must
carry:

1. The server needs a **target identifier** for the window/app, and that
   identifier is inherently per-host — which is exactly what the backlog
   suspects about `focus_app`. It stays local config (§4).
2. The server has no window of its own to fall back to. If the PWA is not open,
   "raise" has to mean something explicit (§3.4).

### 1.2 The reported bug needs no federation to fix

The soft deck (`muxplex/frontend/deck/deck.js`) fetches **same-origin, relative**
URLs — `getJSON('/api/view')`, `postJSON('/api/sessions/<name>/connect')`
(`deck.js:1816`, `deck.js:2909`). The phone loads the soft deck from whichever
muxplex serves it. If the owner's phone is pointed at the Mac's muxplex, then a
plain same-origin `POST /api/focus` reaches the Mac's server, which is local to
the Mac's window.

**Phase 1 as specified in §3 fixes the reported bug with zero federation code.**
Federation (§5) only becomes necessary for "phone is pointed at host A, wants to
raise host B," which is a different, unreported request.

### 1.3 The soft deck already has the hole this fills

`deck.js:2455` already has a `focus_app` case in its action dispatch that logs
`"focus_app is not yet implemented (see BACKLOG.md item 3)"` and returns. The
action is already in the catalog with the right kind; only the body is missing.

---

## 2. Platform feasibility — the honest table

Two conditions must BOTH hold for a platform to be supportable: muxplex must run
there, and the muxplex process must have access to the desktop's window server.
The second condition is the one the backlog does not mention and is the most
likely cause of a "works on my machine" failure.

| Platform | muxplex runs there? | Process has GUI-session access? | Mechanism | v1 |
|---|---|---|---|---|
| **macOS** | Yes — launchd agent | **Yes.** `service.py:339` bootstraps into `gui/{uid}`, the Aqua GUI domain | `open -a <focus_app>` via `create_subprocess_exec` (argv, never a shell) | **SHIP** |
| **Linux / X11** | Yes — systemd user service | **Unreliable.** `_SYSTEMD_UNIT_TEMPLATE` (`service.py:21`) sets only `PATH` and `TMUX_TMPDIR`. `DISPLAY`/`XAUTHORITY` are present only if the desktop session imported them into the systemd user manager — desktop-dependent, not guaranteed | `wmctrl -a <title>` / `xdotool search --name <t> windowactivate` — external binaries, neither is a muxplex dependency | **501** |
| **Linux / Wayland** | Yes | n/a | **None portable** — see §0.1 | **501** |
| **WSL** | Yes — systemd user service inside WSL | The window is a **Windows** browser window; there is no Linux window to raise | Would need `powershell.exe` interop from WSL to drive the Win32 sequence | **501** |
| **Windows native** | **No** — see §0.2 | n/a | n/a | **n/a** |

### 2.1 Why macOS is the one that works

muxplex's launchd agent is bootstrapped into `gui/$UID`, not `user/$UID`
(`muxplex/service.py:339`, and the matching teardown at `:308`). A `gui` job runs
inside the user's Aqua session and can talk to the window server. This is the
single structural fact that makes server-side raise possible on macOS and
unreliable on Linux.

**Supporting evidence, in this workspace:** `muxplex-deck`'s own launchd install
uses the identical `gui/{uid}` domain (`muxplex-deck/src/muxplex_deck/service.py:974`),
and the deck's `open -a` focus is the implementation that works today. A
`gui/$UID` launchd agent running `open -a` is therefore already a proven
combination in this project.

**This is strong evidence, not proof, and must be confirmed before shipping.**
See §9.1 — the one mandatory pre-implementation experiment.

### 2.2 Why `open -a` and not `osascript`

Inherited unchanged from `muxplex-deck/src/muxplex_deck/focus.py`'s
`macos_focus_command()` docstring, which already made the case: `open -a`
activates the app if running, launches it if not, works for Chrome/Safari-installed
PWAs (real `.app` bundles under `~/Applications/Chrome Apps.localized/`), and
requires no AppleScript/Automation permission prompt. `osascript` requires the
Automation permission grant, which a background service cannot obtain
non-interactively.

### 2.3 Consistency with existing platform dispatch

`muxplex/service.py` establishes the house pattern and the new code follows it
exactly:

- **Dispatch on capability where a capability exists, on platform where it does
  not.** `service.py` uses `_is_darwin()` for macOS and `_have_systemctl()` — a
  `shutil.which` probe, not a platform-name check — for systemd. Focus follows:
  `sys.platform == "darwin"` for the macOS branch (there is no capability to
  probe; Aqua access is a property of the launchd domain), and everything else
  falls to the unsupported branch.
- **Private `_<platform>_<verb>()` implementations, one public
  platform-dispatching wrapper.** Mirrors `service.py`'s `_launchd_start()` /
  `_systemd_start()` / `service_start()` shape (`service.py:405-490`).
- **The unsupported branch is an explicit, named error function**, mirroring
  `_no_systemctl_error()` (`service.py:410`) — which prints what is missing, why,
  and what the user can do instead. Never a silent return.

---

## 3. Phase 1 — `POST /api/focus` (local host only)

This is the whole of v1.

### 3.1 Module: `muxplex/focus.py` (new)

Purpose: one responsibility — resolve whether this host can raise its PWA
window, and do it. Owns platform dispatch and nothing else. No HTTP, no
settings loading, no auth.

**Contract**

```
resolve_focus_capability() -> FocusCapability
    Inputs:  none (reads sys.platform only)
    Outputs: FocusCapability(supported: bool, platform: str, mechanism: str, reason: str)
             - supported=True,  platform="darwin", mechanism="open -a", reason=""
             - supported=False, platform=<sys.platform or "wsl">, mechanism="",
               reason=<the honest, user-facing sentence for this platform>
    Side effects: none. Pure. Safe to call on every request.

async raise_window(app_name: str) -> None
    Inputs:  app_name — non-empty, from settings; NEVER from a request body
    Outputs: None on success
    Raises:  FocusUnsupportedError  — this platform has no implementation
             FocusFailedError(detail) — mechanism ran and failed; detail carries
                                        the real stderr / exception text
    Side effects: spawns `open -a <app_name>` via asyncio.create_subprocess_exec.
                  On macOS this LAUNCHES the app if it is not running (§3.4).
```

**Implementation notes**

- `create_subprocess_exec` with an argv list, **never** `create_subprocess_shell`
  and never a shell string. This mirrors `terminal_input.py`'s argv discipline
  and the injection-safety property asserted by
  `muxplex/tests/test_input.py:316`. Even though `app_name` is operator-supplied
  rather than caller-supplied, argv is the house rule for anything the server
  executes.
- Timeout: 2.0s, matching `muxplex-deck/src/muxplex_deck/focus.py`'s
  `FOCUS_TIMEOUT_SECONDS`. A timeout raises `FocusFailedError`, never returns
  success.
- Non-zero exit raises `FocusFailedError` carrying the real stderr. The
  overwhelmingly common cause is a `focus_app` value that names no installed
  app ("Unable to find application named …") and the operator needs that text
  verbatim to fix their config.
- WSL detection reuses the existing helper's technique —
  `"microsoft" in platform.uname().release.lower()`, already in
  `muxplex/ttyd.py:177`. Reported as its own platform string so the 501's
  `reason` can say something true about WSL rather than the generic Linux
  sentence. **Duplicate the two-line check; do not import across modules and do
  not extract a shared helper** — same rationale `AGENTS.md` gives for
  `views.matches_name_pattern` vs `terminal_input.session_matches_allowlist`:
  ttyd's copy guards an `AF_UNIX` bind failure, this one picks a diagnostic
  string, and a future tightening of either must not silently move the other.

### 3.2 Endpoint: `POST /api/focus` (`main.py`)

**No request body. No query parameters. No target of any kind.** §6.1 explains
why this is the load-bearing security property of the whole design.

Auth: the shared `AuthMiddleware` — localhost bypass / session cookie /
federation Bearer. No second key. Same reasoning `AGENTS.md` records for
`/input`: the council rejected a second key as theater.

**Response ordering** (first match wins, and the order matters — see §6.4):

| Condition | Status | Body |
|---|---|---|
| Platform has no implementation | `501` | `{"focus_unsupported_platform": true, "platform": "<p>", "detail": "<reason>"}` |
| `settings["focus_app"]` is empty / not a string | `409` | `{"focus_not_configured": true, "detail": "focus_app is not set in ~/.config/muxplex/settings.json on this host"}` |
| Mechanism ran and failed | `502` | `{"focus_failed": true, "detail": "<real stderr or exception text>"}` |
| Success | `200` | `{"ok": true, "platform": "darwin", "app": "<focus_app>"}` |

`focus_unsupported_platform`, `focus_not_configured`, and `focus_failed` join
the established discriminator convention documented in `docs/API_SEMANTICS.md`
alongside `backstop`, `terminal_conflict`, `unknown_command_id`,
`invalid_view_rule`, `bell_hook_unarmed`, and `queue_full`. A client
distinguishes the three by the discriminator key, never by parsing `detail`.

**There is no success-shaped response for an unsupported platform.** This is the
"no fallbacks that hide failures" constraint, stated as a test: §9.2's
`test_unsupported_platform_never_returns_200`.

### 3.3 Capability advertisement: `GET /api/instance-info`

Additive field, so a client can render an honest disabled state instead of a
dead key:

```
"focus": {
  "supported":  <bool>,   // this platform has an implementation
  "configured": <bool>,   // focus_app is a non-empty string in settings
  "platform":   "<str>",  // "darwin" | "linux" | "wsl" | ...
  "mechanism":  "<str>"   // "open -a" when supported, "" otherwise
}
```

Precedent: `bell_hook_armed` on the same endpoint, whose comment says it exists
so an operator or agent can tell bells are unarmed "without grepping logs."
Same purpose here.

**`focus_app`'s VALUE is deliberately not exposed here.** `/api/instance-info`
is an **unauthenticated** endpoint (its own docstring: "Public endpoint (no auth
required)"). `configured: true` is the fact a client needs; the app name is a
local-host detail that does not need to be readable by an unauthenticated
caller. Authenticated clients that genuinely want it can read
`GET /api/settings`, which is behind the middleware.

### 3.4 What "raise" means when no PWA window is open

On macOS, `open -a` **launches** the app when it is not running. That is more
than raising, and the spec adopts it deliberately rather than working around it:

- It is what the user asking means. "Bring up muxplex on the Mac" is not
  conditional on a window already existing.
- The alternative — probe for a running instance first, then branch — is a
  second mechanism, a second failure mode, and a second thing to keep true
  across macOS versions, in exchange for a behavior nobody asked for.
- The capability is bounded: the app that gets launched is the operator's own
  `focus_app` value, never anything a caller names (§6.1).

**This must be stated in the endpoint's docstring and in `API_SEMANTICS.md`.**
"Launches the configured app if it is not already running" is contract, not an
implementation detail, and a client author who does not know it will be
surprised.

### 3.5 Settings: `focus_app`

New key in `DEFAULT_SETTINGS` (`muxplex/settings.py`), default `""`.

**It joins `LOCAL_ONLY_KEYS`.** Not as a new rule — it satisfies the existing
stated one. `settings.py`'s `LOCAL_ONLY_KEYS` comment block defines the set as
covering *"ANY key that names a command or a filesystem path the SERVER itself
later executes or reads."* `focus_app` is the argument to a command the server
executes. `PATCH /api/settings` ignores it with a warning log, exactly like
`new_session_template`; it is never in `SYNCABLE_KEYS`.

Consequence, and it is intended: **`focus_app` never federates.** It is
per-host by nature — a `.app` bundle name on one machine means nothing on
another — which is the conclusion the backlog already reached. Same treatment
`session_commands` gets, for the same structural reason.

Validation on read (never crash the endpoint): a non-string or empty value is
treated as unconfigured → `409 focus_not_configured`. Fail-closed, matching the
strict-typed fence reads `AGENTS.md` requires elsewhere.

---

## 4. Configuration ownership

| Setting | Lives where | Why |
|---|---|---|
| `focus_app` (target app/window identifier) | **muxplex** `~/.config/muxplex/settings.json`, `LOCAL_ONLY_KEYS` | Names the app the local server executes `open -a` against. Machine-specific by nature. |
| `focus_app` in `muxplex-deck` `config.json` | **Removed** | The deck no longer performs focus locally (§7). Retaining it means two places to configure one behavior — the exact duplication this item exists to end. |

**Migration must be loud, per the deck's own standing rule ("Never print a
command that cannot work on the machine you are printing it to"):**
`muxplex-deck doctor` and the sidecar's startup path emit a warning when a
non-empty `focus_app` is present in the deck's config, naming the file it moves
to. A key that silently stops doing anything is precisely the failure mode the
deck's `_focus_windows` no-op branch was written to avoid.

The deck's `RELOADABLE_KEYS` tuple (`config.py:377`) drops `focus_app`.

---

## 5. Federation — Phase 2, deferred

**Not in v1.** §1.2 shows the reported bug does not need it. This section exists
so that when it is built it follows the established pattern rather than
inventing one.

### 5.1 Targeted proxy, mirroring `bell/clear`

`POST /api/federation/{device_id}/focus`

A near-verbatim copy of `federation_bell_clear` (`main.py:4291`), which is
itself the shape of `federation_connect`, `federation_create_session`, and
`federation_delete_session`. The pattern, unchanged:

1. `_lookup_remote_by_device_id(device_id)` → `404` when unknown.
2. `POST {remote_url}/api/focus` with `Authorization: Bearer {remote_key}` via
   `request.app.state.federation_client`.
3. `raise_for_status()` → `502 "Remote returned {code}"` on HTTP error.
4. Any other exception → `503 "Remote unreachable: ..."`, with a
   `_log.warning` first.

This makes focus the **fourth** federation passthrough, alongside `bell/clear`,
session create, and session delete, exactly as `docs/API_SEMANTICS.md` records
the first three.

**Consequence to state explicitly:** the remote's `501`/`409` — unsupported
platform, unconfigured `focus_app` — arrives at the caller as `502 "Remote
returned 501"`, because that is what `raise_for_status()` does in all three
existing proxies. The discriminator body is lost in transit. That is a real
fidelity loss and it is accepted here **only** for consistency with the
established pattern; improving it means changing all four proxies together, not
special-casing this one.

### 5.2 The circuit breaker needs no new behavior

`_federation_breaker` (`breaker.py`) counts connection-level failures only, and
its docstring is explicit that an HTTP error means the remote is reachable and
must not open the circuit. A remote answering `501 focus_unsupported_platform`
is reachable. The existing semantics are already correct for this endpoint; no
change.

### 5.3 "Raise everywhere" is a client loop, and that does not violate the server-side-resolution rule

`AGENTS.md` says a rule a client would otherwise re-implement should be resolved
server-side. That rule is about **semantics that drift** — the needs-attention
predicate, view membership, attention sort ordering — where two independent
implementations can silently disagree and produce different answers from the
same data.

Iterating a device list and POSTing to each is not a semantic. There is no
predicate, no ordering, no membership question, and therefore nothing to drift.
Meanwhile the server-side alternative requires inventing four things that do not
exist today (§0.3). The rule does not apply; the cost is real.

**Prerequisite, and it is not small:** neither the soft deck nor `muxplex-deck`
fetches federated sessions today — `docs/API_SEMANTICS.md` states this outright
in the `deviceLabelPlacement` entry. Neither has a device list to loop over.
Giving the soft deck one is its own work item and its own design, and it must be
done before "raise everywhere" is buildable at all. Naming that dependency is
most of this section's value.

---

## 6. Security posture

**Verdict: raise does not need a fence of its own. The design does the work
instead — the endpoint accepts no target, and the target it uses is
`LOCAL_ONLY`. A second `focus_enabled` boolean would be theater.**

This section argues that, including the parts that cut against it.

### 6.1 The load-bearing decision: the caller never names the target

This is the entire security design, and everything else follows from it.

If `POST /api/focus` accepted `{"app": "..."}`, the capability would be
`open -a <arbitrary string>` — launch any application on the operator's machine,
chosen by a remote caller. That is not a window raise; it is remote process
execution one thin layer removed from `/input`'s RCE-by-design, and it would
need `input_enabled`-grade fencing or outright rejection.

Because the target comes only from `settings["focus_app"]`, the capability is
bounded to *"trigger the one app the local operator already chose."* A caller
who fully controls the request body can still only cause exactly the effect the
operator pre-authorized by writing that value to disk.

**This property must be defended by a test, not a convention** — §9.2's
`test_focus_endpoint_accepts_no_target`. It is the assumption every other
paragraph in this section rests on.

### 6.2 Why `LOCAL_ONLY_KEYS` is the right and sufficient fence

The `LOCAL_ONLY_KEYS` rationale in `settings.py` is that the federation Bearer
key satisfies `PATCH /api/settings`' auth **and** is the same credential handed
to headless agents — so a PATCHable fence lets a key holder self-authorize. That
argument applies here without modification: a PATCHable `focus_app` would let a
Bearer holder point `open -a` at an app of their choosing, reconstituting §6.1's
launch-anything capability through the settings door. It is the identical shape
as the `new_session_template` sibling incident `AGENTS.md` documents.

With `focus_app` in `LOCAL_ONLY_KEYS`, the feature is inert until an operator
edits `settings.json` on the host. **That on-disk edit is the deliberate
local-operator action `input_enabled` exists to require** — the same property,
achieved with one key instead of two.

### 6.3 Why a separate `focus_enabled: false` is theater

The counter-proposal is `input_enabled`-style: a boolean, default `false`, also
`LOCAL_ONLY`. Rejected, because it gates nothing that `focus_app` does not
already gate:

- Both live in the same file, both require the same local access, both default
  to disabled. Two edits, one capability, no additional attacker excluded.
- `input_enabled`'s companion key, `input_allowed_sessions`, is not a duplicate
  on/off — it is a **scoping** fence answering "which sessions." Focus has no
  scope to narrow: one host, one configured app, no per-target dimension. There
  is no analogous second question to ask.
- `AGENTS.md` already records this project rejecting exactly this shape of
  addition on `/input`: *"deliberately NOT a second key (the council rejected
  that as theater)."*

Redundant fences are not free. They train operators to widen fences reflexively,
which is how the fences that matter get weakened.

### 6.4 Why the capability check comes before the configuration check

The response ordering in §3.2 puts `501` (platform) before `409` (unconfigured).
Deliberate: the platform answer is public and non-sensitive, while the
configuration answer is a fact about this host's local settings file. Answering
"unsupported" first means a caller on a Linux host learns nothing about whether
an operator configured anything. This mirrors the ordering discipline
`AGENTS.md` applies to `/input`, where the allowlist is checked **before**
existence so a rejection never leaks whether a session exists.

### 6.5 Why callers are NOT narrowed by class, unlike the terminal WS

The `/terminal/ws` fix (v0.37.0) narrowed on `bearer_only`. Focus must not,
for two reasons that are both structural:

1. **`muxplex-deck` authenticates with the federation Bearer key** — its config
   is `key_file`, "federation Bearer key" (`muxplex-deck/AGENTS.md`, Config).
   Denying `bearer_only` would break the exact client this capability is being
   moved out of.
2. **The federation proxy is `bearer_only` by construction** — every
   server-to-server call in `main.py` sends `Authorization: Bearer {remote_key}`
   and never a cookie, because a session cookie is signed with each host's own
   `_auth_secret` and does not verify cross-host. Denying `bearer_only` would
   make §5 unbuildable.

The terminal WS narrowing was correct there because the capability behind it was
RCE and an indistinguishable case had to be denied. Here the capability is
bounded by §6.1, and the same narrowing would cost both real clients.

### 6.6 Honest accounting of what a caller CAN still do

Stated plainly rather than left implied:

- **A raise storm.** Any credentialed caller can POST in a loop and make the
  target machine's foreground unusable. There is no rate limit in v1, and that
  is an argued position rather than an oversight: **the same credential can
  already `DELETE /api/sessions/{name}`** — destroying a live process tree that
  `AGENTS.md` calls "the product," "not recoverable," "hours of in-flight work."
  A capability that annoys is strictly milder than one that destroys, and
  rate-limiting only the milder one would be inconsistent theater. If a raise
  storm ever happens in practice, the honest fix is a per-process minimum
  interval returning a real `429` — never a silent drop.
- **App launch, not just raise.** §3.4. Bounded to the operator's own configured
  app.
- **Attention interruption.** This is the first muxplex capability that reaches
  out of software and into the physical attention of the person at the machine
  — it can steal focus mid-password-entry. That is a genuine category difference
  from every other endpoint, and it deserves to be named rather than absorbed.
  The consent mechanism for it is precisely §6.2's on-disk edit: an operator who
  writes `focus_app` into `settings.json` is consenting to "network clients may
  take my foreground." That consent is informed, deliberate, local, and
  revocable by deleting one line.
- **XSS in the PWA gains focus-grabbing.** Real, and mild — bounded to the
  operator's configured app, no data access, no execution beyond that app.
  Notably *unlike* the `/compose` endpoint this project designed and then
  rejected on exactly this reasoning, where the same XSS would have become
  unconditional RCE.

---

## 7. `muxplex-deck` — what it loses and how it keeps working

### 7.1 What changes

| Today | After |
|---|---|
| `main.py:1026` spawns a thread calling `focus.focus_app(self.focus_app_name)` | Calls `client.raise_focus()` against the configured `server_url` |
| `main.py:1173` `_do_connect` calls `focus.focus_app(...)` before the connect POST | Same position, same ordering rationale, different call |
| `focus.py` — macOS `open -a` + full Windows ctypes implementation | **Deleted in full** |
| `config.focus_app` | **Removed** (§4), with a loud migration warning |

### 7.2 The macOS path is subsumed exactly

`muxplex/focus.py` runs the same `["open", "-a", name]` argv, with the same
timeout, in a process on the same machine. Behavior is identical when the deck's
host and the server's host are the same machine — the normal deployment.

**One honest behavior change when they differ.** Today `focus_app` means "raise
the PWA on the machine my deck is plugged into." After the move it means "raise
the PWA on the machine running the muxplex I am driving." These diverge only
when the deck is plugged into a different machine than the server — and in that
case the new meaning is the more useful one, because the deck exists to drive
that server. It is still a semantic change and belongs in the deck's changelog.

### 7.3 Why the Windows implementation is deleted rather than kept

The tempting alternative is "keep `_focus_windows` for the one platform muxplex
cannot serve." Rejected:

- **Keeping it is the duplication this item exists to end**, just narrowed to
  one platform. Every future client that wants focus on Windows faces the same
  reimplementation question, and the deck's copy becomes the thing they port —
  the exact dynamic the backlog names.
- **A conditional client would have to branch on the server's response** ("if
  the server says 501 and my own host is Windows, do it locally"). That is a
  fallback, and this project's constraint is that a platform which cannot raise
  must fail loudly rather than quietly find another way.
- **The implementation being deleted is not confirmed to work.**
  `muxplex-deck/AGENTS.md` marks the ALT-tap fix **UNVERIFIED** on real Windows
  hardware, and the last real-hardware report (2026-07-28) showed
  `SetForegroundWindow` only flashing the taskbar icon. Deleting an unverified
  implementation to collapse a code path is a materially different trade than
  deleting a proven one.

**The regression, stated so it is not discovered:** on a Windows host running
muxplex under WSL, pressing `focus_app` will return `501
focus_unsupported_platform` and the window will not raise. This must appear in
the deck's changelog under a Removed/Changed heading, and the surfaced error
must name WSL specifically (§3.1's WSL platform string exists for this).

**The correct future fix, named but not built:** a WSL→Windows interop mechanism
*inside muxplex* (`powershell.exe` from the WSL server, driving the same Win32
sequence). That puts the capability in the one place it belongs and restores it
for every client at once, rather than for the deck alone.

### 7.4 Parity obligations — what must and must not change

Per `muxplex-deck/AGENTS.md` and `docs/API_SEMANTICS.md`:

- **The 19-action catalog does not change.** `focus_app` keeps its name and its
  `momentary` kind in both `muxplex-deck/src/muxplex_deck/controls.py`'s
  `ACTIONS` and `muxplex/frontend/deck/deck.js`'s `ACTION_CATALOG`. The golden
  parity assertion — `muxplex/frontend/tests/test_deck.mjs:950`, *"ACTION_CATALOG
  mirrors muxplex-deck controls.py's 19-action catalog exactly (name + kind)"* —
  needs **no edit**. Confirm it still passes; do not touch it.
- **The fixture cannot catch this change, and that is the known gap.** The
  backlog's own item 2 records the conclusion from `DECK_PARITY_ARCHITECTURE.md`:
  a shared module would not have caught the `controlKeyContent` bug, because both
  sides can hold the same definition and one can still fail to call it. Here both
  sides keep an identical catalog entry while their *implementations* diverge —
  precisely the class the fixture is blind to. The mitigation is a behavioral
  assertion on each side that the action issues `POST /api/focus` (§9.3), not a
  bigger fixture.
- **`muxplex_client` gains `raise_focus()` in BOTH `sync_client.py` and
  `async_client.py`.** The client is version-locked to the server
  (`test_client_contract.py::test_client_version_matches_server_version`), so
  `muxplex/client/pyproject.toml`'s version bumps with the server's — and per
  the v0.31.1 incident recorded in the changelog, the suite runs **after** the
  bump, since that parity test is the one assertion a pre-bump run cannot
  exercise.
- **`muxplex_client` is vendored into `muxplex-deck`.** The deck picks up the
  new method through its normal dependency bump; it must not hand-roll an httpx
  call to `/api/focus`.

### 7.5 Soft deck

`deck.js:2455`'s `focus_app` case replaces its `console.info` no-op with a
same-origin `postJSON('/api/focus')`, using the existing helper (`deck.js:1770`).
Errors surface on the deck's own status surface — the deck is the status display
(deck AGENTS.md, "Defer UI polish until the pipe is proven"); a `501` must be
visible there, never swallowed.

Optimistic repaint is unaffected: focus has no local UI state to update, so
there is no highlight to paint ahead of the response.

---

## 8. Documentation obligations

A change is not done when the code lands.

| File | What must be added |
|---|---|
| `muxplex/docs/API_SEMANTICS.md` | A `POST /api/focus` section: the no-target rule (§6.1) and why; launch-if-not-running (§3.4); the three discriminators; the check ordering rationale (§6.4); `GET /api/instance-info`'s `focus` block; the platform table (§2). |
| `muxplex/AGENTS.md` | A short section stating `focus_app` is `LOCAL_ONLY` for the existing command/path rule and must never become PATCHable or `SYNCABLE`; and that the endpoint takes no target — with the reason, since a future contributor "adding flexibility" is the realistic way this breaks. |
| `muxplex/settings.py` | Extend `LOCAL_ONLY_KEYS`' comment block with `focus_app` and its one-line rationale, in the established style. |
| `muxplex/docs/AGENT_GUIDE.md` | The endpoint, its `409` on a default install, and that raise is bounded to the operator's configured app. |
| `muxplex/README.md` | `focus_app` in the settings reference, with the platform table and the plain statement that Wayland/Windows are unsupported. |
| `muxplex/docs/BACKLOG.md` | Delete item 3. Per the file's own header, a graduated item's entry is removed. |
| `muxplex-deck/AGENTS.md` | Record the deletion of `focus.py`, the Windows regression (§7.3), and the config migration — including that the hard-won Windows research is being removed deliberately and where to find it in history. |
| `muxplex-deck/README.md` | Remove `focus_app` from the config reference; point at the server's setting. |
| Both `CHANGELOG.md` | At release time, by the owner. `muxplex-deck`'s entry needs an explicit **Removed** heading for the Windows path — a regression discovered by a user is worse than one announced. |

---

## 9. Verification

### 9.1 The one mandatory pre-implementation experiment

**Everything in §3 rests on an unproven assumption: that muxplex's launchd agent,
in the `gui/$UID` domain, can successfully run `open -a`.** §2.1's evidence is
strong (the deck's own `gui/$UID` agent does exactly this) but it is inference,
not measurement.

Prove it before writing the endpoint:

1. On the owner's Mac, with muxplex running as its installed launchd service (not
   a foreground `muxplex serve`), reach the server process and have it execute
   `["open", "-a", "<the PWA bundle name>"]`.
2. Assert the PWA window actually comes to the foreground, observed by a human
   at the machine.
3. Record the exit code and any stderr.

If this fails, macOS joins the 501 list and **there is no v1** — the whole item
collapses to "no platform can do this from a service context," which is a result
worth knowing in an afternoon rather than after the endpoint is built. This is
the same discipline as the project's `KillMode` canary: prove the behavior, never
read the directive back.

### 9.2 Server tests (`muxplex/tests/`)

| Test | Asserts |
|---|---|
| `test_focus_endpoint_accepts_no_target` | Posting `{"app": "Calculator"}` does not change which app is invoked. **Guards §6.1 — the single most important test here.** |
| `test_focus_app_is_local_only` | `focus_app in settings.LOCAL_ONLY_KEYS`; `PATCH /api/settings` with `focus_app` writes nothing and logs a warning; `focus_app not in SYNCABLE_KEYS`. |
| `test_focus_app_not_applied_by_federation_sync` | `apply_synced_settings` with a `focus_app` in the payload leaves the local value untouched. |
| `test_unsupported_platform_never_returns_200` | For each non-darwin platform string, status is `501` with `focus_unsupported_platform: true`. **Guards the no-silent-no-op constraint.** |
| `test_unconfigured_returns_409_not_200` | Empty, whitespace, non-string, and missing `focus_app` each produce `409 focus_not_configured`. |
| `test_platform_checked_before_configuration` | A non-darwin platform with an empty `focus_app` returns `501`, not `409`. Guards §6.4's leak ordering. |
| `test_focus_uses_argv_never_shell` | The macOS path calls `create_subprocess_exec` with exactly `("open", "-a", <name>)`. Shaped after `test_input.py:316`, including a hostile `focus_app` value. |
| `test_mechanism_failure_surfaces_stderr` | A non-zero `open` exit produces `502 focus_failed` with the real stderr in `detail`, never a `200`. |
| `test_instance_info_focus_block` | The `focus` block is present and correct on both a supported and an unsupported platform, and does **not** contain the `focus_app` value. |

**Safety rails apply unchanged.** `conftest.py`'s autouse `SETTINGS_PATH`
redirect covers the new key with no change. No test may execute a real `open`;
the subprocess spawn is faked, in keeping with the deck's own `recording_run`
pattern. Run via `make test` (DTU), never on a host serving muxplex.

### 9.3 Client tests

- `muxplex-deck/tests/` — pressing a `focus_app`-bound key issues exactly one
  `POST /api/focus` and no local subprocess. Assert `focus.py` is gone (an import
  guard, so a future revival is visible in review). Assert `doctor` warns on a
  legacy `focus_app` in the deck config.
- `muxplex/frontend/tests/test_deck.mjs` — the `focus_app` case calls the
  same-origin post helper; the existing catalog parity test at `:950` still
  passes untouched.
- `muxplex/client/tests/` — `raise_focus()` exists on both the sync and async
  clients and targets `/api/focus`.

### 9.4 End-to-end evidence for the reported bug

The bug is "the owner's phone cannot raise muxplex on his Mac." The evidence
must match how it is actually encountered, not a unit test:

1. Open the soft deck on the phone, served by the Mac's muxplex.
2. Press a `focus_app`-bound key.
3. The Mac's PWA window comes to the foreground, observed by a human.
4. With `focus_app` unset, the same press surfaces a visible `409` on the deck —
   not silence.

---

## 10. Summary of decisions

| Question the backlog asked | Decision | Section |
|---|---|---|
| Where does fan-out happen? | Neither, in v1 — the reported bug is same-origin and needs no fan-out. Phase 2 is a targeted proxy mirroring `bell/clear`; broadcast is rejected as the more expensive option. | §0.3, §1.2, §5 |
| Should this be fenced? | No separate fence. The endpoint accepts no target, and `focus_app` is `LOCAL_ONLY` — which is the deliberate local-operator action `input_enabled` exists to require, achieved with one key instead of two. A `focus_enabled` boolean would be theater. | §6 |
| What stays per-device? | `focus_app`, in muxplex's `settings.json`, `LOCAL_ONLY` and never federated. Removed from the deck's config. | §4, §3.5 |
| Is focus an action or a consequence? | Action, always. Never a side effect of `/connect`. | §0.4 |
| What does `muxplex-deck` keep? | Nothing. `focus.py` is deleted in full. macOS is subsumed exactly; Windows is a stated, changelogged regression with the correct future fix named. | §7 |
| Which platforms work? | macOS only — and only because muxplex's launchd agent lives in the `gui/$UID` Aqua domain. Wayland cannot, structurally. Windows has no muxplex to move to. Linux/X11 and WSL are deferred, not impossible. | §2 |
