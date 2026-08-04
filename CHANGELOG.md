## v0.35.0 (2026-08-04)

### Changed

- **`upgrade` and `doctor` are now install-source-aware, and `upgrade` can no longer silently replace what you installed with something else.** PEP 610 has the installer write a `direct_url.json` beside every non-PyPI install, recording where that install actually came from. muxplex read two fields out of that file, ignored the rest, and then reconstructed an install target from assumptions instead of from the record — and every time an assumption was wrong, `upgrade` installed something the user had never asked for, on top of what they had. The recorded origin is now used **verbatim**: whatever URL, ref, or path is in that file is what gets installed. A fork or a mirror upgrades from *its* remote, not from canonical upstream.
  - **Five install shapes are recognized, and the classification came from probing what each one actually writes** rather than from reading the spec and reasoning about it: `pypi` writes no `direct_url.json` at all, `git` writes `vcs_info`, an editable install writes `dir_info` with `editable: true`, a **non-editable local directory** install writes `dir_info` with no `editable`, and a **local wheel or sdist** writes `archive_info`. The last two look nothing alike in the file and were both being misread (see Fixed).
  - **A git pin keeps its meaning.** `vcs_info.requested_revision` is now read and classified against the remote: a **tag** pin tracks the latest tag, a **branch** pin tracks that branch's HEAD, **no ref** tracks the default branch's HEAD, and an **exact commit** pin never moves on its own. The ref you pinned is the ref that is compared and the ref that is installed.
  - **Never claims an update when no check happened.** Every path that genuinely cannot check — a network failure, an unrecognized source, an exact-commit pin, a local directory or archive with no remote to ask — now returns `update_available=False` with a message that begins `not checkable` and names the reason. There is no longer any path that reports an update it did not observe.
  - **A source change is refused.** `_target_matches_source()` re-derives the shape of the computed install target from the recorded source and checks the two agree, immediately before the target is handed to the installer. If they disagree, `upgrade` prints both and stops; `--force` is the only way past, and it overrides that gate alone. Editable installs refuse unconditionally — reinstalling over a checkout someone is actively working in is not an upgrade, it is a deletion.
  - **`doctor` reports provenance as information, not as a problem.** It prints `↳ from <source>` in green, because running from git, from a fork, or from a local build is a deliberate choice rather than a misconfiguration. The warning marker is now spent only on `not checkable` — a genuinely degraded capability, meaning nothing is watching this install's version — instead of on the fact that you did not install from PyPI.

### Added

- **Shift+Enter and Ctrl+Enter insert a newline instead of submitting.** Every desktop chat app has trained people to expect this, and terminal apps have never had it, for a real reason: the legacy key encoding has no field for a modifier on Enter, so `Enter`, `Shift+Enter` and `Ctrl+Enter` are byte-identical `0x0D` and no TUI can tell them apart. The workaround TUIs settled on is `Ctrl+J` (`0x0A`), which genuinely is distinct. But muxplex is not a legacy terminal — it is xterm.js in a browser, where the `KeyboardEvent` carries the modifier already. `terminal.js` intercepts the chord and sends the real kitty-keyboard-protocol CSI-u encoding (`\x1b[13;2u` / `\x1b[13;5u`) over the existing ttyd input frame, and the shipped tmux config binds `S-Enter` and `C-Enter` to `send-keys C-j` so apps that only speak the legacy encoding still get something they understand. Apps that read CSI-u natively never reach those binds. Deliberately **not** a bare `\n`: that is indistinguishable from `Ctrl+J` and would lie to any app that wants to know which key was actually pressed. `Alt+Enter` and `Cmd+Enter` fall through unchanged — `Alt+Enter` already has a working legacy `ESC CR` encoding that apps rely on. The tmux config load sentinel moves `base-2` → `base-3`, so a running server can prove it re-sourced the new config.
  - **The first working version still submitted the line anyway, and finding out required looking at bytes rather than at behavior.** "It inserted a newline and then submitted" is ambiguous — it does not distinguish "our handler never fired" from "our handler fired and something else fired too." A raw byte dumper in a live pane said `0a 0d`: our translated `C-j` *and* a stray `CR`. xterm.js's `attachCustomKeyEventHandler` returning `false` stops **xterm's own** key processing and does not call `preventDefault()`, so Enter still reached xterm's hidden textarea and came back around through `onData` a second time. The pre-existing branches in that handler (Ctrl+Shift+C, Ctrl+F) never hit this, because those chords produce no text input and so have no default action to suppress — which is exactly why it was easy to miss. Fixed by calling `e.preventDefault()` before `return false`, asserted by a regression test, and written into `AGENTS.md` so the next branch added to that handler does not repeat it.

### Fixed

- **A `uv tool install` of a git build was silently converted to PyPI on upgrade.** `_is_uv_managed` answers *which environment* to upgrade — the uv tools venv rather than a pip site-packages — and it answers that correctly. It was also being OR'd into the branch that decided *what to install*, so any uv-managed install resolved its target to the PyPI package name no matter where it had actually come from. The real case that surfaced it: a host installed from `git+https://github.com/bkrabach/muxplex@v0.34.0` was handed the PyPI package by `muxplex upgrade`. `_is_uv_managed` stays and is unchanged; it is simply no longer asked a question it was never answering.
  - The two halves of the command were already inconsistent, which made this worse for anyone on a fork. The update **check** used `info["url"]` — the fork's own remote — while the **install** used a hardcoded canonical URL, or `"muxplex"` when uv-managed. A fork user was therefore told "up to date" against their fork, and then handed upstream's code the moment they acted on it.
- **The pinned ref was discarded, so a tag-pinned install was compared against the wrong thing and upgraded onto the wrong thing.** `requested_revision` was never read. A pin to `@v0.34.0` was checked against the default branch's `HEAD`, which reported "update available" to someone already sitting on the newest release — and then moved them onto unreleased `main` while they believed they were following releases. Git refs now carry their intent through both the check and the install.
- **Local installs were classified `unknown`, and `unknown` was destructive.** `_get_install_info` recognized only `vcs_info` and `dir_info` + `editable`, so a **non-editable local directory install** and a **local wheel** both fell through to `unknown`. `_check_for_update` then had an unconditional `unknown → (True, "unknown install source — upgrading to be safe")` fallback, which is neither true nor safe: it permanently claimed an update existed, so `doctor` nagged forever about a version it had no way to check, and `upgrade` installed canonical upstream over the user's own local build. Doctor was nagging people into destroying their own builds. Both shapes are now classified from what they really write to `direct_url.json`, and neither is checkable, which is now something the code can say out loud.
- **A macOS-only test had been passing for the wrong reason, and never exercised the case it names.** `test_upgrade_exits_1_if_service_fails_to_restart` mocks `_have_systemctl=True` / `_have_launchctl=False` to drive `upgrade()` down the systemd dead-service path, but it never pinned `sys.platform` — so on a real macOS runner `upgrade()` took the darwin branch, never consulted the systemctl mocks at all, printed "skipping service management step", and returned normally. It passed regardless, because the ambient install info made `_installed_version_on_disk()` read back the same version `_get_install_info()` reported, so `_verify_version_moved()` always said "version did not change" and `SystemExit(1)` fired through the unrelated `_install_failed` branch instead. When `7badd8f` pinned the install source to a fake `pypi` / `0.1.0` — needed on its own, to stop these tests depending on this repo's editable dev install — the version check started succeeding, execution reached the block the test is actually about, and the gap surfaced on macOS for the first time. Fixed by pinning `sys.platform = "linux"`, the same pattern nine sibling `upgrade()`/`doctor()` tests already use; this one was simply not in that batch, because at the time it was still passing by accident.
- **Four ttyd tests failed on macOS CI for a reason that lived entirely in the fixtures.** `SUN_PATH_BUDGET` (102 bytes — macOS's limit, the tightest of the three qualified platforms) and `validate_socket_dir()`'s real bind probe are both correct; real usage is `~/.local/share/muxplex/ttyd/`, around 40 bytes, nowhere near the ceiling. The fixtures routed every ttyd test through pytest's `tmp_path`, which on the macOS runner resolves under `/private/var/folders/...` at roughly 114–120 bytes before a socket filename is appended at all. A new `short_socket_dir` fixture `mkdtemp`s under `/tmp` explicitly — deliberately not `tempfile.gettempdir()`, which macOS CI points at that same deep path via `$TMPDIR` — and `/tmp` is short on Linux, on macOS (a symlink to `/private/tmp`, still short after `.resolve()`), and on WSL alike, so no platform branch is needed. No product code changed.

### Verification

- **Both suites were run after the version bump**, per the v0.31.1 incident. The client/server version parity test (`test_client_contract.py::test_client_version_matches_server_version`) is the one assertion that can only fail once the bump exists, so a release that runs its suite before bumping never tests the thing the bump can break.
- The upgrade fixes were established against **real installs of each shape** rather than from the specification: what `uv` and `pip` actually wrote to `direct_url.json` for a git install, an editable install, a non-editable local directory install, a local wheel, and a PyPI install. That probe is what showed a local build and a local wheel were two different shapes both landing in `unknown` — reading the code would only have shown that neither was handled, not what each needed.
- The macOS platform-dispatch fix was confirmed by an in-process repro on a real Mac (ambient `darwin`, matching CI): `upgrade()` now raises `SystemExit(1)` through the intended `_service_restart_failed` path, not through the accidental `_install_failed` one it had been passing on.
- The macOS `sun_path` fixture fix was reproduced locally first, with a standalone script that rebuilds the runner's `tmp_path` shape and length: all four failures reproduce before the fix and pass after it, against unmodified product code.
- Shift+Enter was proved at the byte level against tmux 3.4 with the real edited `base.conf` — Shift+Enter CSI-u → `\n`, Ctrl+Enter → `\n`, plain Enter → `\r`, Ctrl+J → `\n`, Alt+Enter → `\x1b\r`, `a` → `a` — then end to end in a browser against a live pane.


## v0.34.0 (2026-08-04)

### Changed

- **One ttyd per session, on its own UNIX domain socket — two devices viewing two different sessions now simply works.** Until now muxplex ran exactly one `ttyd` for the whole server, on hardcoded TCP port 7682, with the tmux session name baked into its argv (`ttyd ... tmux attach -t <name>`). That single process *was* "the terminal", so a second device wanting a second session was not something the server could serve — it was a **conflict the server had to detect and refuse**, which is precisely what `POST /connect`'s 409 `terminal_conflict` existed for. Switching sessions meant killing that ttyd and respawning it with different argv, which yanked the terminal out from under whoever was already attached. `POST /connect` is now a single idempotent `ensure_ttyd(name)`: one ttyd per tmux session, each bound to its own socket under `~/.local/share/muxplex/ttyd/` (`MUXPLEX_TTYD_SOCKET_DIR` overrides), and the WebSocket proxy dials that socket with `unix_connect()` instead of `ws://localhost:7682/ws`. Connecting to session X never disturbs session Y. There is no contended resource left, so there is nothing to arbitrate.
  - **`AF_UNIX` is a strictly stronger fence than the `-i 127.0.0.1` bind it replaces.** Every ttyd is still `-W` with no `-c` credential — an unauthenticated, writable terminal — so the fence around it is load-bearing. A socket is guarded by filesystem permissions (0700 directory, uid-checked, symlink-refused) with no network namespace involved at all: there is no port to scan and no interface to misconfigure. `validate_socket_dir()` runs at startup, before anything else, and fails loud rather than degrading.
  - **Both WebSocket routes take an optional `?session=<name>`.** `WS /terminal/ws` and `WS /federation/{device_id}/terminal/ws` now name their target directly instead of reading the server's implicit "current" terminal; absent the parameter both fall back to `state["terminal_session"]` exactly as before, so the change is additive and an older client keeps working. The frontend sends it on both branches. The federation relay dials the remote's own `/terminal/ws` over HTTPS and never touches a ttyd socket, so it needed no socket handling at all — only to forward `?session=` upstream.
  - **A ttyd is a view, not the session, so it is reclaimed on resource grounds.** Relays are refcounted; `DELETE /api/sessions/current` kills only the caller's own session's ttyd, and only when `relay_count()` for it is zero — a structural check that is strictly stronger than the group-ownership claim it replaces, because it also covers two devices in the *same* group co-viewing one session. Idle ttyds are reaped after 60s by a pass riding the existing ~1s poll cycle, `MAX_TTYDS = 32` is a backstop against a reaper bug rather than against users, and orphans from a previous process are reaped at startup only after their identity is confirmed against a fresh `ps` snapshot.
  - **`POST /connect` can now fail honestly.** It returns 500 on a spawn error and 503 at the capacity ceiling. The old endpoint never verified the spawn at all, so it could — and did — return 200 for a terminal that did not exist.
  - **`ttyd_port` (7682) survives in `/connect`'s response as a legacy wire-only field.** Nothing binds it any more, but `muxplex_client.parse_connect_result()` requires an int with no default and is vendored into muxplex-deck. Returning `null` or removing it would break a client this repo's tests cannot see.

- **The `lsof` port sweep is deleted.** `_kill_pids_on_port()` ran `lsof -ti :7682` and signalled **every PID it found**, without ever checking what those processes were — it existed only because it was the sole thing that could reap a ttyd whose PID was never recorded. That is the same accident class as the `KillMode` incident this repo already documents, the one that destroyed 44 live tmux sessions: act on a process you have not identified and you eventually kill something you did not mean to. The safety net it provided is replaced rather than merely removed — `reap_orphan_ttyds()` confirms identity against a fresh `ps` snapshot before signalling anything, and never signals an unconfirmed PID. `grep -c lsof muxplex/ttyd.py` now returns 0, and a source-level test asserts the string cannot come back.

- **Three platform constraints are enforced in code, not just written down.** Each was learned from a *silent* failure across the Linux / macOS / WSL2 spikes in `scripts/`, and in every case ttyd stayed **alive and non-functional** rather than erroring — which is why none of them can be caught by a liveness check:
  - **The socket path must end in `.sock`.** ttyd's UNIX-socket detection is suffix-based and a non-`.sock` path does not error: it silently falls back to TCP **7681** on `INADDR_ANY`, reopening the exact unauthenticated-writable-terminal exposure the old `-i 127.0.0.1` bind was added to close. `ttyd.SOCKET_SUFFIX` is why the path is never hand-built, and why paths are hashed and range-checked against a 102-byte `sun_path` budget (macOS's limit, the tightest of the three qualified platforms) rather than concatenated from a session name.
  - **The socket directory must be a Linux-native filesystem.** On a WSL DrvFs mount `bind()` fails `ENOTSUP`, and ttyd does not treat that as fatal — it busy-retries roughly every 10ms, indefinitely, with no backoff and no self-termination: alive, burning CPU, bound to nothing. `validate_socket_dir()` refuses such a directory at startup with the right diagnosis instead of leaving that process spinning.
  - **A socket file proves a bind happened at spawn; it never proves liveness.** A `SIGKILL`ed ttyd leaves its socket behind forever and `Path.exists()` answers `True` for it. `socket_is_live()` is therefore a real `AF_UNIX connect()`. Porting it as an existence check reintroduces the reconnect-bounce bug the pre-accept liveness check was written to fix.

- **Guard decisions, recorded because they are the kind of thing re-litigated in six months.** 409 `terminal_conflict` is **deleted** — with one ttyd per session it cannot fire. WS **4409 is kept but redefined narrower**: it now means "you asked to attach to a session your own group has not selected," a per-request consistency check, not "another group holds the one terminal." 4404 widens to cover a missing, invalid, or unknown session. `state["terminal_group"]`'s **server logic is deleted** while the **wire field is retained** as informational provenance — no server behavior branches on it any more, and subtracting a documented public field buys nothing. The frontend's conflict overlay loses its Take-over button accordingly — there is no longer a terminal to take over — and, per the reconnect-loop fix below, now does nothing but display; getting back in sync is the poll loop's job, not the overlay's.

### Added

- **`muxplex commands list|add|remove`** — the local-operator write path for `session_commands`, validated through the same `resolve_session_commands()` V1–V7 rules the server applies rather than a second reimplementation, and written via `save_settings()`. This is not a fence bypass: `LOCAL_ONLY_KEYS` is enforced in `patch_settings()` (the API's write path) and deliberately not in `save_settings()` (the local-operator path). A CLI subcommand invoked by the human at the keyboard is the intended writer, no different in kind from hand-editing `settings.json`.
- **Settings > Commands now shows the feature to the person who has never heard of it**, and a Duplicate/copy authoring aid replaces the wall. Every resolved pair is rendered including the built-in `default` — previously `default` was filtered out and the whole field hid itself when no extra pairs existed, so the user who most needed to discover the feature saw nothing at all. Settings re-fetches on every open, closing the apply-then-verify loop without a page reload. Malformed pairs are surfaced where they are actually seen: a non-selectable "N pairs failed to load" row in the New Session picker, count badges on the settings gear and the Commands tab that fire before Settings is ever opened, and `DELETE /api/sessions/{name}`'s 409 `unknown_command_id` now points at Settings > Commands alongside the `command_id` it already named. Each row gets **Duplicate…** and **Copy command**: Duplicate opens an inline composer with editable id/label/create/delete fields and a live `muxplex commands add` line to copy and paste into any shell. **There is no Save button anywhere in that composer, by design** — `session_commands` holds arbitrary shell the server executes, and `AuthMiddleware`'s loopback source-IP bypass precedes every credential check with no CSRF defense, so any endpoint meaning "define a pair" would be RCE reachable from a page in the operator's own browser. The composer's client-side checks are labeled advisory; the server's `GET /api/session-commands` `errors[]` after apply-and-reload remains the authority.

### Requirements

- **`websockets>=14.0`** (was `>=11.0`). The proxy's `unix_connect()` lives in `websockets.asyncio.client` and does not exist below 14.0 — below that floor the import fails outright. This is a hard API requirement, deliberately not the version-pinning approach `AGENTS.md`'s uvicorn/websockets incident rejected: that one forced a single implementation to patch one known bug, and `test-latest-deps` remains the general defense against dependency drift.

### Security

- **An orphaned legacy ttyd on port 7682 is now always reported, even with no PID file.** `reap_legacy_ttyd()` returned early when `LEGACY_TTYD_PID_PATH` was absent, before ever probing the port — which made the loud "UNAUTHENTICATED WRITABLE TERMINAL" report unreachable for exactly the scenario it exists to catch: a pre-upgrade ttyd (`-W`, no credential) still bound to 7682 whose PID file was lost to a crash, a manual kill, or a state-dir wipe. Live DTU verification confirmed such a ttyd was neither swept nor reported; it could sit there indefinitely with nothing said. The detect-and-report check now runs unconditionally after every branch. It deliberately **reports without reaping** on that path: with no PID file there is no recorded identity to confirm, and killing whatever happens to be listening on a hardcoded port *because it is on that port* is the exact sweep pattern this release deletes.

### Fixed

- **The per-device sync-group terminal guard has been silently non-functional since it shipped, and this is the release that found out.** If you turned a device off the shared session selection (Settings > Devices, or the header toggle), the terminal-side guard that was supposed to keep that device from attaching to a session its own group had not selected **never ran at all**. `openTerminal()` called `connectWebSocket(sessionName, remoteId)` and simply omitted the third argument, so `ownDeviceId` was `undefined` for every local browser client and the local branch's `if (ownDeviceId)` guard never appended `&device_id=` to the `/terminal/ws` URL. Server-side (`main.py`'s `terminal_ws_proxy`), an absent `device_id` leaves `group` as `None`, and the entire per-device consistency block — including the WS 4409 path — is skipped outright. Not narrowed, not degraded: skipped.
  - **Be clear about the duration.** The missing argument was present from the moment `ownDeviceId` was added to both function signatures, in `33eaf80` ("per-device sync groups with single-owner terminal guard"), which shipped in **v0.31.3**. It has therefore been broken in every tagged release since: v0.31.3, v0.31.4, v0.31.5, v0.31.6, v0.32.0, v0.33.0. It was not introduced by the per-session-ttyd work, and none of that work made it worse.
  - **What changed is only that something finally looked.** The rewrite added the first test that asserts the local branch's actual `/terminal/ws` URL, and it failed. Both sides of this were tested the whole time, and the seam between them was not: `test_ws_proxy.py` builds the `device_id` query parameter itself in its `_ws_url()` helper, so it proved the server correctly handles a parameter no browser was sending; and `test_terminal.mjs` called `openTerminal()` in 37 places without the string `device_id` appearing in the file even once — at `33eaf80`, at v0.33.0, and at every tag in between. Nothing asserted that the front end produced the input the back end was being tested against. Fixed by forwarding the argument at the single call site in `terminal.js`.
- **The terminal conflict overlay no longer reconnects in an unbounded loop.** `_showTerminalConflictOverlay()` re-POSTed `/connect` and then called `connectWebSocket()` unconditionally, regardless of that POST's outcome. `_reconnectAttempts` is module-level and is cleared only by a successful data message or a fresh `openTerminal()`, so a second 409/4409 re-entered the same function through `connect()`'s own escalation branch — with no `setTimeout`, no cap, and nothing gating the recursion. The result was a pure promise-microtask loop (overlay → fetch → connectWebSocket → escalation → overlay → …) that ran until V8's heap was exhausted: in the frontend suite, about six minutes and a `SIGABRT`; in a browser, an unthrottled request storm aimed at the server. It was introduced in this release's own work, when the Take-over button's user-gesture gate was removed while the reconnect kept firing automatically.
  - The underlying mistake is a semantics mismatch this release created. WS 4409 no longer means "another device holds the one shared terminal" — that resource is gone — it means "this device asked to attach to a session its own group has not selected," a state desync rather than a transient conflict. Retrying the identical request cannot resolve a desync, and here it would actively make things worse: `connect_session()` unconditionally writes `{active_session: name}` for the group, so a stale client re-POSTing `/connect` would silently overwrite whatever the other device had just correctly selected — fighting the very guard the code path exists to enforce.
  - **The fix is structural rather than a cap or a backoff.** The overlay now displays and stops: zero network calls, zero timers, so no server response can make it recurse, no matter how it is provoked. Recovery goes through the channel that already exists and is already correct — `app.js`'s poll loop picks up the group's real `active_session` on its next tick and calls `openTerminal()` with the right target, resetting all reconnect state cleanly; re-selecting a session does the same thing immediately. The client-side HTTP 409 `terminal_conflict` branch is kept as a version-tolerant no-op: this server can no longer send it, but an older or federated peer might, and it funnels into the same, now-safe, overlay.
- **`muxplex config set`/`config reset` no longer claim success while writing nothing.** Both went through `patch_settings()`, which silently skips any `settings.LOCAL_ONLY_KEYS` key so a Bearer-key-holding remote caller can never widen one of those fences — correct behavior for `PATCH /api/settings`, and a lie when the caller is the local operator at the keyboard, who has every right to change these keys. The CLI printed `<key>: <value>` unconditionally regardless. Both now check `LOCAL_ONLY_KEYS` first and exit non-zero naming the actual escape hatch (edit `settings.json`, or `muxplex commands add` for `session_commands`). One check covers all eight fenced keys.
- **The socket-directory diagnostics stop telling WSL operators to run an impossible `chmod`.** `validate_socket_dir()` ran its ownership and mode checks before the WSL/DrvFs check. On a default, metadata-less DrvFs mount `chmod()` is a **silent no-op** — verified directly on a real WSL2 host: `chmod(0o755)` then `chmod(0o700)` on a fresh `/mnt/c` directory both return success and both leave the mode at `0o777`. So the function's own corrective `chmod(0o700)` did nothing, the mode check then fired, and the operator was told to do the one thing the process had just tried and failed to do — while the accurate diagnosis (`AF_UNIX bind()` fails `ENOTSUP` on DrvFs; ttyd busy-retries forever without binding) sat unreachable a few lines below. The same mount reports a synthetic mount-wide uid rather than real ownership, so the ownership check had the identical hazard, equally unfixable by `chown`. Both now run *after* the DrvFs check. The symlink check reads the file-type bit, which `chmod`/`chown` cannot affect, so it is unaffected and stays first. The mode message is also generalized: since the function always calls `chmod(0o700)` immediately beforehand, reaching that check with group/other bits still set is proof the `chmod` had no effect on **any** filesystem, so it now says that and points at relocating the directory. The safety property is unchanged throughout — the directory is refused either way. This was found by testing on a real WSL2 host; the existing DrvFs test modeled a directory whose mode had already become `0o700`, as if the `chmod` had worked, so the mode check was never in its path and it could not have caught this.

### Documentation

- The per-session-ttyd and command-pairs-UI design records, the named-command-pairs implementation record, and the terminal-config ownership record all lived at the root of a throwaway cross-repo workspace and existed nowhere else. All four are preserved under `docs/plans/` and `docs/`, bodies intact, headers reconciled against what actually shipped. The source tree cites two of them by their workspace filenames (`PER_SESSION_TTYD_SPEC.md`, `COMMAND_PAIRS_UI_DESIGN.md`) from 36 places, so each header records that name.
- `AGENTS.md`, `docs/API_SEMANTICS.md`, and `docs/AGENT_GUIDE.md` are rewritten for the per-session architecture. The `0.0.0.0` exposure incident and the 4409-never-reached-the-wire writeup are kept verbatim — they remain true records of what happened.
- `scripts/README.md` records **WSL2 as GO**, matching Linux and macOS on every question, which is what qualified all three target platforms for this architecture.
- `CHANGELOG.md` gained the v0.33.0 named-command-pairs entry, which shipped inside that artifact with no coverage at all. `docs/BACKLOG.md` records the open question of where a device label belongs on a preview tile.

### Verification

- Full Python suite green in the Digital Twin Universe (`make test`), **run after the version bump** — 1969 passed, 4 skipped, 28 deselected in 92s — including `test_client_contract.py::test_client_version_matches_server_version`, which asserts `client/pyproject.toml`'s version equals the root's and can only catch a mismatch once the bump exists. v0.31.1 shipped a mismatch precisely because the suite ran before the bump and was never re-run after.
- Frontend suite green after the bump: `node --test tests/*.mjs` → 717 tests, 717 pass, 0 fail, 3.2s. That last number is itself part of the proof for the reconnect-loop fix above: before it, this same command did not finish at all — it consumed the V8 heap for roughly six minutes and aborted.
- The §12.5 acceptance test is real end to end: real tmux, real ttyd, real UNIX sockets, two devices on two sessions through the real ASGI app, concurrent WebSockets proven not to cross session-unique in-pane markers, then A's teardown proven not to disturb B. Mocked suites can prove the wiring; only this can prove the two terminals are actually independent.
- **Sessions survive the upgrade, proven rather than asserted.** A canary process was placed in the service's own cgroup and the canary itself was validated by a negative control first — it dies under `KillMode=mixed` and survives under `process`, so a survival result means something. Then a real `muxplex upgrade` was run end to end: identical session-creation timestamps, identical tmux server PID, and identical in-pane markers before and after.
- `reap_legacy_ttyd()`'s gap was found by live DTU verification of the real function, not by reading it, and its regression test was confirmed failing before the fix and passing after.
- The DrvFs `chmod` no-op was measured on a real WSL2 host rather than reasoned about, and both new tests fail against the old check ordering and pass against the fix.
- This release was cut twice. The first attempt was prepared and then dropped unpushed because the frontend suite was red — the reconnect loop was OOM-ing it, and the `device_id` seam above was failing a real assertion. Both are fixed in commits that precede this one, so the release sits on top of them rather than over them.

### Upgrading

**On systemd hosts, check `KillMode` before you upgrade:**

```
systemctl --user show muxplex.service -p KillMode
```

It must report `process`. `muxplex upgrade` regenerates the safe unit, but it does so **after** its stop step — so a host still carrying a pre-v0.24.0 unit, or a hand-edited one, with `mixed` or `control-group` would have its tmux server SIGKILLed by that stop, taking every live session with it. muxplex auto-spawns the tmux server when none is running, which is how that server ends up in muxplex's cgroup looking like nothing at all. This is confirmed behavior, observed in testing. It is **not new in this release** — `service.py` and `cli.py`'s `upgrade()` are byte-identical to v0.33.0 — but this is the release worth saying it out loud in, because it is also the release in which the per-session ttyd rewrite makes people upgrade.

The upgrade otherwise needs nothing: socket directories are created on demand, the pre-upgrade single ttyd on 7682 is reaped at startup once its identity is confirmed (and reported, never blind-swept, if it cannot be), and a client that sends no `?session=` gets the previous fallback behavior unchanged.


## v0.33.0 (2026-08-02)

### Added

- **Named session command pairs — more than one way to create a session.** Until now there was exactly one: `new_session_template` created every session on the host and `delete_session_template` killed it. That is the wrong shape as soon as you want a workspace session rooted in one directory and a scratch shell in another, or a genuinely different creation command for some sessions. A new `session_commands` list holds additional named pairs, each with an `id`, a `label`, a create command and a delete command. The two singular keys fold in automatically as the reserved `default` pair — always first in the resolved list, and never claimable by a config entry — so a settings file that has never heard of this feature resolves to a one-element list, and a client that sends no `command_id` runs the same code path with the same template it ran before.
  - **Which pair created a session is recorded at create time**, and delete looks that record up instead of asking the caller. That is what makes this a *pair* rather than two unrelated lists: kill a session that was created by `amplifier-workspace` and you get `amplifier-workspace`'s teardown, without anyone having to remember which command made it three days ago. `DELETE /api/sessions/{name}` takes no `command_id` at all, and that is deliberate — a caller-chosen kill command is a caller-chosen shell command.
  - **The record lives in the manifest, not `state.json`, and that placement is the whole trick.** The manifest's `sessions` map is reaped every ~2s against live tmux, and `spawn_session_command` has a documented branch that returns success *before* the session is visible to an enumeration — a record written into a reaped map would be deleted inside that window, and the session would later be torn down by the wrong pair with nothing in any log to say why. `created_with` is therefore a new top-level map (manifest schema 1 → 2) that reaping never touches.
  - **`muxplex restore` is pair-aware.** Without that, a restore after a reboot would recreate an `amplifier-workspace` session as a bare `tmux new-session` — one window, wrong cwd, the exact "looks restored and isn't" failure this repo's own recovery notes warn about. A recorded id that no longer resolves is a hard `fail` in the restore report, naming the missing pair and the file to fix, never a silent substitution of the default.
  - **Registering the key in `DEFAULT_SETTINGS` was load-bearing, not bookkeeping.** `save_settings()` merges only keys it knows about and drops the rest, so an unregistered `session_commands` would have been erased by the next write of *any* setting — a hand-edited list silently deleted the first time someone toggled a display preference in the UI.
- **`GET /api/session-commands`**, returning the resolved, ordered, validated list plus one human-readable string per rejected entry. The fold and the validation resolve once on the server rather than being re-derived by each of PWA, sidecar, and agents — the standing answer in `AGENTS.md` for any rule a client would otherwise have to re-implement. `POST /api/sessions` gains an optional `command_id`, rejected with 400 before any subprocess runs if it does not resolve. `DELETE /api/sessions/{name}` answers **409 `unknown_command_id`** and runs nothing when a session's recorded pair is no longer configured; `?force=true` is the explicit override that substitutes the default kill command and says so in the response. All additive, per this repo's API rule.
- **A command picker in the create UI, and a read-only list in Settings > Commands.** The picker appears only when two or more pairs are configured — below that the create control is byte-identical to what shipped before, which is what almost every install will see. Settings displays the additional pairs and any configuration errors without an editable control anywhere, and names `~/.config/muxplex/settings.json` as the place to manage them, because that is honestly the only place they can be managed (see Security below).
- **Settings > Terminal — the tmux config is now editable from the dashboard.** Until now the only way to pick a theme was `muxplex config set tmux_theme`, which the target audience will never do. The new tab shows install status, a theme picker, a copy-mode choice, and the generated config behind a "Show the generated config" disclosure. Changes apply to a running tmux server immediately — a setting that appears to do nothing until restart is a bug, not a deferral.
- **`GET`/`PATCH /api/tmux-config`.** API first, per this repo's own rule; the tab is a client of it, not a parallel implementation. GET returns install status, the current theme and copy mode, the available themes, and a preview of the rendered config.
- **New setting `tmux_copy_mode`** — `desktop` (default) or `vi`. Selecting `vi` renders an extra `30-copy-mode.conf` fragment; selecting `desktop` removes it. Machine-scoped like `tmux_theme`, so it does not sync between devices.

### Security

- **The API may list and select a command pair; it can never define one.** `session_commands` is in `settings.LOCAL_ONLY_KEYS` and deliberately absent from `SYNCABLE_KEYS` — `PATCH /api/settings` ignores it, and federation sync never carries it. These are arbitrary shell commands the server itself executes, and the federation Bearer key is the same credential handed to remote agents, so a PATCHable `session_commands` would let a Bearer-key holder define a pair *and* select it at create time: the identical RCE that fence closed in v0.31.4 for `new_session_template`, with one extra layer of indirection. The cost is real and accepted: managing pairs means editing `~/.config/muxplex/settings.json` on the host, there is no editable control in Settings, and there is not going to be one.
- **The API's tmux vocabulary is closed, by construction.** tmux config can carry `run-shell` and `default-command` — arbitrary code execution — and the API bearer key is the same credential handed to remote agents. So there is no free-text directive field and there will not be one: `theme` is validated against the shipped theme list and `copy_mode` against exactly two values, and anything else is rejected with 400 before it reaches disk. Verified against a live server with four injection attempts (a `run-shell` payload, a newline-smuggled `default-command`, a path-traversal theme, and a plausible-but-invalid enum value) — all four rejected, nothing written.

### Fixed

- **The config preview no longer looks broken.** It was a `<textarea>` 170px tall holding ~3600px of text with no visible scrollbar. Two rounds of adding a fade and a styled scrollbar changed nothing, and the measurement explains why: `offsetWidth - clientWidth` came back 2px, not the 12px the rule asked for — this platform paints overlay scrollbars, which reserve no gutter and ignore `::-webkit-scrollbar` outright. The fix was not a third styling attempt. The dialog body already scrolls, so an inner scroll box was a second scroll container that put the interesting edge below the dialog's own fold; removing it removes the thing that needed an affordance. The preview also now shows directives only — 116 of its 162 lines were comment or blank, 71% of what a reader scrolled past answering a question they had not asked. 162 lines to 46. The template files keep every comment.

### Verification

- Full suite green in the Digital Twin Universe (1845 passed) on the released tree.
- The API was exercised against a running server, not asserted from source: GET shape, valid PATCH in both directions, fragment appearing and being removed, the rendered vi fragment loading into a real tmux server, and the four injection attempts above.
- The tab was driven in a real browser across three rounds. The first two found real defects that were fixed rather than explained away.
- Command pairs are proved against a real tmux server, not mocks. Unit tests with mocked subprocesses prove the *wiring*; they cannot prove that pair B's teardown ran and pair A's did not. `tests/test_command_pairs_integration.py` does, with marker files written by real create and delete commands on an isolated tmux socket — the one claim the mocked suites structurally cannot make.

### Note on the command-pairs entry

Command pairs shipped inside the v0.33.0 artifact and had no changelog coverage at all until this entry was added after the fact. Two sessions were working and releasing from this branch at the same time; each wrote up its own workstream and neither noticed the other's was missing from the file.

The write-up is added to this section rather than carried into a new release because v0.33.0 is factually the version that introduced the feature — all seven of its commits fall in `git log v0.32.0..v0.33.0`, and none straddle the tag. PyPI artifacts are immutable, so cutting a v0.34.0 to hold nothing but a version bump and this text would credit the feature to a release that does not contain it — the same misattribution the v0.31.6 note below exists to correct.


## v0.32.0 (2026-08-02)

### Changed

- **The default tmux config now targets people who never asked to learn tmux.** muxplex users drive sessions from the web dashboard — creating windows, switching sessions, splitting panes are all things the UI does — and almost nobody arrives as a tmux user. The default was still shaped like a tmux user's config. It now optimises for the terminal *content* behaving the way a desktop app behaves, and not for teaching keybindings. The bar applied to every line: would someone who has never heard of tmux be surprised by it?
  - **Copy mode is `emacs`, not `vi`.** In vi copy-mode the arrow keys are the least useful way to move, selection is modal (`v` to start, `y` to finish), and nothing on screen indicates a mode exists. In emacs copy-mode the arrows, PageUp/PageDown, and Home/End all do the obvious thing — which is what someone reaching for the keyboard after scrolling tries first.
  - **`Ctrl+C` copies the selection**, and **`Esc` leaves copy mode.** Scrolling up silently enters copy mode, so there has to be an obvious way back out; without it, "my typing stopped working" is the common report. And Ctrl+C is the first thing anyone tries after highlighting text — the alternative was `Alt+w`, which nobody guesses.
  - **Double-click selects a useful word.** tmux's default separators break on `/ . - _`, so double-clicking a path, a filename, or a package name grabbed a fragment instead of the thing you pointed at. `word-separators` now keeps those intact.
  - **Vim-style `Alt+hjkl` pane navigation is no longer a default.** It is muscle memory this audience does not have. `Alt+arrows` remains — discoverable by trying it, and the direction is self-evident. Anyone who wants hjkl adds four lines to `90-local.conf`.
  - Unchanged and deliberately so: mouse on, 50k scrollback, 1-based window/pane numbering, `renumber-windows` (close a middle window and the rest close the gap, like browser tabs), and the terminal/colour/clipboard correctness settings.

**Upgrading:** `90-local.conf` is loaded last and is never written by muxplex, so anything you have set there already wins over all of this. If you were relying on the old vi copy-mode or `Alt+hjkl` defaults, put them back with:

```
setw -g mode-keys vi
bind -T copy-mode-vi v send -X begin-selection
bind -T copy-mode-vi y send -X copy-selection-and-cancel
bind -n M-h select-pane -L
bind -n M-l select-pane -R
bind -n M-k select-pane -U
bind -n M-j select-pane -D
```

### Verification

- Full suite green in the Digital Twin Universe, run after the version bump.
- The rendered config was loaded into a real tmux server and every changed option read back from it — `mode-keys emacs`, `word-separators`, and the three new `copy-mode` bindings — rather than asserted from the template text.
- Verified live on a server with 51 running sessions: the new base loaded, a local override still won, and the server pid was unchanged throughout.


## v0.31.6 (2026-08-02)

### Bug Fixes

- **`muxplex setup-tls` no longer fails outright on a long hostname.** X.509 caps a CommonName at 64 characters (RFC 5280's `ub-common-name`), and `cryptography` enforces that by raising rather than truncating — so on any host whose name exceeds the limit, certificate generation died with a bare `ValueError: Attribute's length must be >= 1 and <= 64, but it was 87` thrown from deep inside the library, with nothing in the message naming the hostname as the cause. This is not a corner case: a GitHub macOS runner reports a 67-character hostname, and long Tailscale names, corporate FQDNs, and cloud instance names all reach it. For those users `setup-tls` simply did not work. The CommonName is now capped at all three cert-generation sites (`generate_self_signed`, `generate_local_ca`, `generate_leaf_signed_by_ca`). Truncating is safe here for a specific reason: the **full, untruncated hostname is still written into subjectAltName**, and SAN — not CN — is what every modern TLS client actually validates against, RFC 6125 having superseded RFC 2818. A shortened CN costs nothing a client will ever notice; the alternative cost the user their certificate.

### Testing

- **The launchd tests now exercise real `launchctl`, not mocks.** Every launchd test until now stubbed `subprocess.run`, so they verified the *shape* of the calls and nothing about launchd's behaviour -- which is how v0.31.1, v0.31.2, and v0.31.3 each shipped a macOS-only bug that a human, not CI, found in production. Four new tests run against real launchd on the macOS runner using a throwaway `com.muxplex.selftest` label with fixture-finalizer cleanup, and skip on Linux. The fixture detail is load-bearing and came from probing a real Mac rather than reasoning about it: a plain `/bin/sleep` job dies instantly, so `bootout` looks *synchronous* and a test built on it passes while proving nothing. Measured on real hardware -- plain sleep: no race to catch; SIGTERM-trapping job: still loaded ~2.25s after bootout. The fixture therefore traps SIGTERM and takes a moment to exit, like the real server draining connections, and the race test additionally asserts that the window *was* observable so it cannot quietly decay into a tautology.
- **The macOS CI job passes for the first time since it was added.** It had been red on every commit since introduction (`b7cf04b`, `7b076c6`, `225dba7`) -- nine failures. A permanently-red job protects nothing, so the new launchd coverage would have gated nothing either. Six of the nine were the CommonName bug above. The other three were tests that quietly meant something different depending on which runner picked them up: two asserted the systemd/Linux service branch without pinning `sys.platform`, and one mocked `create_subprocess_shell` while leaving `create_subprocess_exec` live, so it really exec'd `tmux` and depended on a binary and a live server it was not trying to assert.

### Note on v0.31.5

The `v0.31.5` tag and the artifact published to PyPI point at `225dba7`. The CommonName fix landed *after* that tag, so it was never in the released v0.31.5 despite the CHANGELOG briefly crediting it there. PyPI artifacts are immutable, so the entry has been moved here rather than the release re-cut.


## v0.31.5 (2026-08-02)

### Bug Fixes

- **The sync-group toggle in the header visibly changes state again when you press it.** The header button that switches a device between following this server's shared session/view selection and running independently did everything correctly except look different: `renderSyncGroupControls()` applied `header-btn--active`, flipped `aria-pressed`, and updated the tooltip — and `.header-btn--active` had no rule in `style.css` at all, so nothing rendered. A user on v0.31.4 reported the toggle as visually broken, and it was. The JavaScript was right in every respect; the class it toggled simply did not exist in the stylesheet. This shipped in v0.31.4 alongside sync groups and went out undetected. Fixed by adding the rule — filled `--accent-dim` background, `--accent` border and icon colour, plus a hover variant — reusing the accent tokens `.header-btn:hover` and `.settings-tab--active` already use rather than introducing a new colour. Filled background *and* solid border, not just a colour swap, so the two states stay distinguishable without relying on hue alone; both the collapsed and expanded header buttons pick it up, since both carry `.header-btn`.
  - **No existing suite could have caught this, by construction.** `test_frontend_js.py` checks JS source, `test_frontend_html.py` checks markup, and `frontend/tests/*.mjs` check JS behaviour — none of them cross-checks a class name the JS applies against the stylesheet that is supposed to render it. A new `tests/test_css_class_definitions.mjs` now does: it extracts every string-literal class name applied via `classList.add/remove/toggle(...)` or a literal `className = '...'` assignment in `app.js`, `terminal.js`, and `deck/deck.js`, and asserts each one resolves in the stylesheet the same page actually loads (`style.css` for the app, `deck/deck.css` for the deck). Only the class-name argument position is read, so `classList.toggle('is-stale', staleness === 'warn')` does not misfire on `'warn'`. Proved against the real bug: with the `header-btn--active` rule removed it fails naming that exact class, and passes once restored. This is the second cross-file blind spot this one feature has produced — the first was the `app.js`/`terminal.js` global collision now guarded by `test_shared_scope.mjs`.
- **`/terminal/ws` rejections now arrive as the close codes they always claimed.** v0.31.4 corrected the documentation about this without fixing the behaviour, and this release fixes it. The `device_id` guard closed the WebSocket with 4404 (unknown device) and 4409 (terminal claimed by a different sync group) *before* calling `websocket.accept()`, and a pre-accept close never produces a real WebSocket close frame — the connection was never upgraded — so it went over the wire as a bare `HTTP/1.1 403 Forbidden` with an empty body. A real browser reports `1006` for any failed opening handshake and cannot see 4404/4409 at all, which left `terminal.js`'s 4409 branch unreachable in production; only `test_terminal.mjs`'s synthetic close event ever exercised it. Fixed by accepting the handshake and then immediately closing with the code — completing the handshake is the only way a real close frame carrying a real code can exist to be reported to a browser. Evaluated and rejected: uvicorn's WebSocket-denial-response extension, which would let the rejection carry the existing HTTP-level JSON body without accepting; the WHATWG WebSocket API never exposes a failed handshake's status or body to JavaScript, so that helps a script client reading raw HTTP and does nothing for a browser. Trade-off accepted deliberately: the browser's `open` event now fires briefly before `close` follows, because the handshake genuinely completes. The refusal is not weakened — the helper returns immediately after `close()` without ever touching ttyd or the relay loop, so no session data crosses that connection regardless of what the client sends afterwards.
- **An open federation terminal relay no longer hangs shutdown.** `federation_terminal_ws_proxy` carried two defects that its local-terminal sibling had already fixed. It relayed with `asyncio.gather()` over both directions, which waits for *both* to finish: when the browser disconnects, the remote→client direction keeps streaming from a still-live remote ttyd, the handler never returns, and uvicorn's "waiting for connections to close" phase blocks until systemd SIGKILLs the process at the stop timeout. Separately, it never registered its task in `_ws_proxy_tasks`, so lifespan shutdown could not find it to cancel — the shutdown count silently undercounted, and every open federation relay survived the cancellation pass untouched. With N concurrent relays this was a reliable hang, not an edge case. Both fixed by mirroring `terminal_ws_proxy`: `asyncio.wait(FIRST_COMPLETED)` plus cancel-the-other-direction, an explicit `websocket.disconnect` check rather than relying on a `RuntimeError` from a second `receive()`, and register/deregister in `_ws_proxy_tasks`.
- **`muxplex upgrade` no longer reports success for an upgrade that did nothing.** Observed on a real machine: three consecutive upgrades printed `Status: update available (v0.31.2 → v0.31.3)` followed by `Installed successfully` and `Service started`, while the tool environment never left v0.31.2. The install target is unpinned (`muxplex`, meaning "latest"), so uv answered it from a cached PyPI index that predated the release, resolved "latest" to the version already installed, reinstalled it, and exited 0. The code checked the exit code — which asks *did the installer do what I asked*, not *did the version change*. Those two come apart precisely here, and nothing in the exit code distinguishes them. Two changes: the upgrade now passes `--refresh`, so a stale index cannot silently pin you to the version you already have; and the version is read back **from disk in a fresh interpreter** and compared against what you started on. An upgrade known to be available that leaves the version unchanged is now a loud failure carrying the exact command that fixes it, rather than a green message. A `--force` reinstall of the current version is still a legitimate no-op.
  - The fresh interpreter matters: `importlib.metadata` resolves and caches at import time, and the process asking the question *is* the old build. Asking it in-process returns the version you started with regardless of what just landed on disk — the same blind spot one level up.

### Infrastructure

- **CI now runs on macOS.** Every job in `ci.yml` ran on `ubuntu-latest` only, despite muxplex targeting macOS, WSL, and Linux — and that gap produced three consecutive production incidents caught by a human rather than by CI: v0.31.1 (`muxplex update` crashing on a `launchctl bootstrap` race), v0.31.2 (`service restart` silently doing nothing — itself a regression from the v0.31.1 fix), and v0.31.3 (doctor reporting a tunnel as the local server). v0.31.2's own changelog said it plainly: the launchd tests exercise the logic, not `launchctl`, because the suite only ran in a Linux container. A new `test-macos` job (macos-latest / arm64, Python 3.13 only, with tmux and ttyd installed via Homebrew) closes that. Deliberately one job rather than a matrix axis on the existing one: macOS runners cost roughly 10x Linux minutes, and what was missing was platform coverage, not more Python versions. It paid for itself immediately — the X.509 CommonName failure above was found by this job rather than by a user, along with three tests whose results depended on the runner rather than on the thing they claimed to assert. Four real-`launchctl` tests now exercise actual launchd on macOS instead of subprocess mocks; they skip on Linux, which is why the DTU run below reports 4 skipped where v0.31.4 reported none.

### Documentation

- **`docs/AGENT_GUIDE.md` now covers TLS trust bootstrap.** The guide is what agents and scripts get pointed at, and its quick-start uses an `https://` URL — but it never mentioned `/api/ca`, `/ca.crt`, `/setup`, `--cacert`, or verification at all. An agent following it exactly against a TLS-enabled instance got `curl: (60) SSL certificate problem: unable to get local issuer certificate` and found no answer anywhere in the document; that failure happens before the auth middleware runs, so none of the five documented acceptance branches could help. Added: the three unauthenticated endpoints and why they must be auth-exempt (a client cannot authenticate over TLS it does not yet trust), verified bootstrap recipes for localhost and remote, muxplex-client's `ca_file=` argument, what to do when `/api/ca` 404s because the server used a different `setup-tls` method — and an explicit instruction not to silently disable verification in response.

### Verification

- 1822 Python tests passed, 4 skipped, 15 deselected in the Digital Twin Universe (`make test`) — run *after* the version bump, per the v0.31.1 incident. The 4 skips are the new real-`launchctl` tests, which require macOS and are covered by the new CI job instead.
- 698 frontend tests passed (`node --test tests/*.mjs`), including the new `test_css_class_definitions.mjs`, which was proved to catch the bug it targets: removing the `header-btn--active` rule fails it by name, restoring it passes.
- CI green on `add0d48`: all five jobs, nine matrix checks, including the new macOS (arm64) job.
- 9 new TLS tests cover CN truncation at all three cert-generation sites. Their subjectAltName assertions are load-bearing rather than incidental: a future change that truncated the SAN too would sail past a success-only test while silently breaking certificate validity, so the full untruncated hostname is asserted present explicitly. Proved to guard the regression — reverting `_common_name()` to a pass-through fails all 9 with the original `ValueError: Attribute's length must be >= 1 and <= 64, but it was 87`.
- The `/terminal/ws` close-code fix was verified by raw-socket probe in the DTU, not only through `TestClient` — which operates on ASGI messages and never serializes real bytes, and is precisely why the original defect went unnoticed. Pre-fix the wire showed `HTTP/1.1 403 Forbidden`; post-fix it shows `HTTP/1.1 101 Switching Protocols` followed by a real close frame carrying 4409, with a non-owning device still fully refused.
- Five new upgrade tests: the unchanged-version failure (including that it prints the `--refresh` command), the legitimate `--force` no-op, the successful move, an unreadable version treated as failure rather than success, and that the version is read by shelling out rather than importing in-process. Verified on the affected machine by reproducing the original condition, not only by asserting the new code path.


## v0.31.4 (2026-08-01)

### Security

- **ttyd no longer binds to every interface.** `spawn_ttyd()` execs ttyd with `-W` (writable) and no `-c` credential, but until now it also had no `-i` bind flag, so it defaulted to `INADDR_ANY` (`0.0.0.0:7682`). Confirmed live: another host on the LAN, and separately over Tailscale, both got a real ttyd terminal client back (`200`, full HTML), and `GET /token` returned `{"token": ""}` — no credential configured at all. Anyone who could reach the port could type into whatever tmux session was currently attached, with zero interaction with muxplex's own auth stack — `_ws_auth_check`, the cookie/Bearer middleware, TLS never entered the picture. Fixed by adding `-i 127.0.0.1` to the spawn argv, so ttyd now binds loopback only; all legitimate access already went through muxplex's authenticated `WS /terminal/ws` proxy, which dials `127.0.0.1` directly. **This closes at bind time, not retroactively — anyone running muxplex as a service must restart it (`muxplex service restart`) to pick it up.** Not configurable: there is no settings key for this, and if one is ever added it must live in `settings.LOCAL_ONLY_KEYS`, never `SYNCABLE_KEYS` — the federation Bearer key is the same credential handed to remote callers, and a PATCHable bind address would let any of them widen ttyd's exposure right back open.

### Added

- **A device can opt out of the shared session/view selection.** Until now every browser tab and every device converged on the same server-global `active_session`/`active_remote_id`/`active_view` — switch sessions on your phone and your laptop switched too. A device can now join its own private sync group instead (toggle it from the header or Settings > Devices), while the single shared ttyd process stays a safely-arbitrated, single-claim resource regardless of grouping: taking over a different group's live terminal is refused (`409 terminal_conflict`) unless the caller passes `?takeover=true`. Clients that send no `device_id` — muxplex-deck, `muxplex_client`, any existing browser session — are unaffected; omitting it resolves to the `global` group, identical to today's behavior.

### Bug Fixes

- **The interactive terminal pane came back after rendering nothing in v0.31.3.** This release's own sync-groups change introduced the regression, and a user's browser console caught it, not CI. `app.js` declared a top-level `function _ownDeviceId()` (a getter); `terminal.js`, in the same commit, separately declared a top-level `let _ownDeviceId = ''` (private module state). Both load as classic `<script>` tags in `index.html`, which share one global scope — a second script cannot redeclare a binding the first one created there. The browser threw `Uncaught SyntaxError: Identifier '_ownDeviceId' has already been declared` while parsing `terminal.js`, so `terminal.js` never executed at all: the interactive terminal pane rendered nothing, while the grid and previews (all in `app.js`) kept working since that file parsed fine on its own. Each file's own test suite (`test_app.mjs`, `test_terminal.mjs`) loads only that one file in isolation, so neither could ever see the collision — a per-file unit test cannot catch a cross-file collision by construction, no matter how thorough. Fixed by renaming `terminal.js`'s private state to `_termOwnDeviceId`. A new `tests/test_shared_scope.mjs` now parses every non-vendor `<script src=...>` tag out of `index.html` and evaluates them, in order, into one shared context, failing on any collision — any future frontend script is covered the moment it's added to `index.html`.
- **`muxplex update` no longer prints a false "not serving" warning right after a successful restart.** It restarted the service and immediately ran `doctor()` to verify, but the only thing checked was `systemctl --user is-active` — true the instant the process starts, not once uvicorn has actually finished loading settings and binding the configured host:port. Calling `doctor()` in that gap raced a server that was in fact healthy, and a manual `muxplex doctor` moments later showed nothing wrong. `update` now polls the same probe doctor's own "Running:" check uses, on a short interval with a generous ceiling, before verifying. If the service genuinely never comes up in time, that's reported as a real failure — no suppression, no flag to skip verification.
- **Corrected: `/terminal/ws` does not actually close with WS codes 4409/4404.** `docs/API_SEMANTICS.md` and code comments claimed the sync-groups conflict/unknown-device cases closed the WebSocket with those codes. Live raw-socket verification showed otherwise: both `close()` calls happen before `websocket.accept()`, so per ASGI/uvicorn semantics they never produce a real WebSocket close frame — the connection was never upgraded. They serialize as a bare HTTP 403 handshake rejection instead (confirmed: `HTTP/1.1 403 Forbidden`, empty body); a real browser reports close code 1006, never 4409/4404. The existing test assertions were correct at the layer they test — Starlette's `TestClient` operates on ASGI messages and never serializes real bytes, so it couldn't see this gap — but the docs and comments were wrong about what ships over the wire. No behavior change: the real, working recovery path is the ordinary HTTP `409 terminal_conflict` body on the `/connect` escalation POST, which is unaffected. Also added the `docs/AGENT_GUIDE.md` §4 coverage of the `device_id` opt-out and the `409 terminal_conflict` response that the sync-groups spec required and the original change had missed.

### Verification

- 1804 Python tests passed, 15 deselected, in the Digital Twin Universe (`make test`) -- run *after* the version bump, per the v0.31.1 incident above the fold in this same file.
- 696 frontend tests passed (`node --test tests/*.mjs`), including the new `test_shared_scope.mjs` (proved to actually catch this class of bug: reverting the rename reproduces the exact production `SyntaxError`, restoring it passes).
- ttyd's loopback bind reasserted by test (`-i 127.0.0.1` present in the spawned argv) and by the live probe described above.
- **Anyone running muxplex as a service must restart it** (`muxplex service restart`) to pick up the ttyd fix — the running process keeps serving on the old, LAN/Tailscale-reachable bind until it does.


## v0.31.3 (2026-08-01)

### Bug Fixes

- **`muxplex doctor` no longer reports a dead service as running.** `Service: launchd agent running` was decided by two things that both lie: `launchctl print` returning 0 (which only means the *label is loaded*, not that anything is alive) and a probe of the configured port (which any port-forward will answer). On the machine that surfaced this, the job had **no pid and a last exit status of 1**, relaunching in a loop with 15 MB of stderr — and doctor showed a green check the whole time. It now asks launchd for the actual pid and reports `LOADED BUT NOT RUNNING — last exit status N`, pointing at `/tmp/muxplex.err`. A health check that reads green on a crash-looping service is worse than no health check.
- **`Running: vX` now proves the server it found is actually this host's.** `localhost:PORT` is not evidence of locality: an `ssh -N -L 8088:127.0.0.1:8088 otherhost` tunnel makes another machine's muxplex answer on your own loopback, indistinguishable by probing alone. Doctor was reaching a *remote* muxplex through exactly such a tunnel and reporting its version as the local running version — which read as an ordinary stale install and sent a real investigation chasing a service that had in fact never started. The probe now compares the returned `device_id` against this install's own, and when they differ says so plainly, naming the other machine and pointing at `lsof -nP -iTCP:PORT -sTCP:LISTEN`.
- **The port guard now names a tunnel a tunnel.** When something already holds the serve port, muxplex probes it and refuses to kill a healthy peer — correct, but the message said *"port 8088 is already served by a healthy muxplex … Refusing to terminate it"* even when the "healthy muxplex" was a forwarded connection to a different machine. That framing implies a local server worth protecting. It now distinguishes the two: a foreign `device_id` produces an error that says the port is forwarded, shows the likely `ssh -L` shape, explains that this host's muxplex cannot bind while it is up, and declines to kill it on the grounds that the holder is probably the tunnel rather than a server.

### Verification

- Full suite green in the Digital Twin Universe, run after the version bump.
- Five new tests: `launchctl list` parsing for the crash-looping (`-` pid, exit 1), healthy, and unregistered cases; and device-identity matching, including that a missing or absent `device_id` reads as *cannot tell* rather than *ours*.
- Verified on the affected Mac end to end, not just in the container: the tunnel was removed, the launchd job took the port and started (pid 4779), and the host now serves its own build instead of a remote one.


## v0.31.2 (2026-08-01)

### Bug Fixes

- **`muxplex service restart` actually restarts the service again on macOS.** This was a regression introduced in v0.31.1, and it is the worse kind: v0.31.0 crashed loudly on `launchctl bootstrap` exit 5, and the v0.31.1 fix for that traded the crash for a silent lie. `launchctl bootout` returns *before* the job is gone, so `restart` booted the old job out, immediately bootstrapped, got exit 5 because the old job was still tearing down, then checked "is it loaded?" -- saw the **old** job still loaded -- and reported success. The old process kept serving. Running `muxplex service restart` twice produced no output and no error either time, while `muxplex doctor` went on reporting a version from two releases earlier as the running one. Two changes fix it: `bootout` now waits for launchd to confirm the job is actually gone before anything bootstraps on top of it, and "already loaded" now counts as success only for `service start`, whose job is to make sure something is running. For `install` and `restart` -- which have just removed the old job on purpose -- a surviving job means the replacement failed, and that now fails loudly with the plist path and the manual commands.
- **`muxplex doctor` no longer tells macOS users to run `systemctl`.** The stale-version warning and the port-conflict error both printed `systemctl --user restart muxplex` unconditionally, which is meaningless on a machine where launchd is the service manager -- and it appeared right next to a line correctly reporting `Service: launchd agent running`. Both now print `muxplex service restart`, which is correct on every platform, so there is nothing to branch on.

### Verification

- Full suite green in the Digital Twin Universe, run *after* the version bump.
- Three new tests cover the regression directly: that `install`/`restart` refuse to call a surviving old job a success, that `start` still accepts an already-running service, and that `bootout` polls until the job is really gone rather than returning early.
- Honest limit, unchanged from v0.31.1: these tests exercise the logic, not `launchctl`. That binary is macOS-only and this suite runs in a Linux container, so the real launchd behaviour is verified by the shape of the calls, not by running them. The race and the false-success path are both provable in the code; whether they are the *whole* story on any given Mac is not something this suite can attest.


## v0.31.1 (2026-08-01)

### Bug Fixes

- **The status bar no longer shows your session name twice.** `status-right` displayed `#{b:pane_current_path}` -- the basename of the current directory. amplifier-workspace derives tmux session names *from* the directory basename, so for any workspace session that rendered the exact string already shown in the session badge on the left. The same name, twice, costing about 22 columns that the window list wanted. Removed rather than shortened: the badge on the left already answers "where am I", and `status-right-length` drops 60 to 40, so the clickable window list gains again.
- **The clock shows AM/PM again.** It silently became 24-hour (`%H:%M`) in the v0.31.0 theme rewrite. Restored to `%I:%M %p`.
- **`muxplex update` no longer crashes on macOS with a launchd traceback.** The upgrade installed correctly, printed "Service started", and then died with a raw `CalledProcessError` on `launchctl bootstrap gui/501 ... exit status 5`. `launchctl bootout` returns *before* the job is actually gone, so a bootstrap issued into that window fails with exit 5 (EIO). That is a race, not a failure -- and `check=True` turned a path that had in fact succeeded into a traceback out of subprocess internals. Bootstrap now retries through the teardown window, treats an already-loaded service as success (a running service is the only outcome the caller cares about), refuses to retry genuine errors, and on real failure raises an actionable message carrying launchd's own stderr plus the manual command and the `muxplex serve` fallback. `muxplex service start` had the identical crash and now shares the same path.
- **`muxplex-client` version matches the server again.** v0.31.0 shipped with `client/pyproject.toml` still at 0.30.1. The repo has a test that exists precisely to catch this, and it did not, because the release ran the suite *before* the version bump and never re-ran it after. The test was right; the process was wrong.

### Verification

- 1729 tests passed in the Digital Twin Universe, 0 failed -- run *after* the version bump this time.
- Four new tests cover the launchd bootstrap retry, the already-loaded case, loud failure on a genuine error, and no-retry-on-non-race-errors. They exercise the logic, not `launchctl` -- that binary is macOS-only and this suite runs in a Linux container, so the real launchd behaviour is verified by the shape of the calls, not by running them. Stated plainly rather than implied.
- Fixing the launchd path surfaced that 24 test mocks returned `None` from `subprocess.run`, which always returns a `CompletedProcess` in reality. The mocks were lying about the contract and got away with it while nothing read the result. Fixed the mocks rather than making production code defensive about a test artifact.


## v0.31.0 (2026-08-01)

### Added

- **muxplex now manages your tmux configuration.** `muxplex tmux install` ships an opinionated tmux config and wires it up by adding one guarded `source-file -q` line to your `~/.tmux.conf`. This exists because the package that used to own that config is being retired, and muxplex is the natural home -- it already owns tmux *behaviour* settings (`new_session_template`, `tmux_socket_dir`, `window_size_largest`) and has three co-equal configuration surfaces. The measured detail the whole design rests on: tmux >= 3.1 loads *every* user config in its search path, not just the first one found, and the later file wins conflicts. So the block installs into `~/.tmux.conf` -- the earliest file -- at the top, which puts muxplex first in the chain and means anything you have set, anywhere, overrides it. That is deliberately the opposite of the conda/rustup/nvm convention: they install last because they want to win, muxplex installs first because it wants to lose. Everything muxplex generates lives in `~/.config/muxplex/tmux.d/`; `90-local.conf` is created once and never written again, so it is yours and it loads last. Because this is the only place muxplex writes a file it did not create, every write is backed up first, written atomically, verified by re-reading the file *and* by starting a throwaway tmux server on a private socket, applied to your running server so you see it immediately, and removable with `muxplex tmux uninstall`, which restores the file byte-for-byte. A symlinked tmux.conf -- usually a tracked dotfiles repo -- is refused unless you pass `--allow-symlink`, and even then the symlink is resolved first so it is never replaced by a regular file.
- **A `tmux_theme` setting**, defaulting to `brand`, with `steel` and `catppuccin-mocha` as alternatives. `brand` is built from muxplex's own UI tokens, so a window that rings a bell turns the same amber in your terminal that its tile turns in this dashboard -- one signal, one colour, two surfaces. Deliberately not federation-syncable: it renders to a file on this host, exactly as machine-scoped as `tmux_socket_dir`.

### Bug Fixes

- **Clicking a window label in the status bar switches to that window again.** The `MouseDown1Status` binding and `status-format[0]` were byte-identical to tmux's defaults the whole time -- the binding was never the problem. The cause was an unbounded `#{pane_current_path}` in `status-right`. tmux gives the window list whatever columns `status-left` and `status-right` do not take, so a deep path pushed windows behind the `>` truncation marker, and a window that is not on screen has no mouse range to click. `status-right` now uses `#{b:pane_current_path}` and `status-right-length` drops from 120 to 60, returning 17 columns of clickable window labels on a 179-column client. Worth stating plainly because it is the kind of bug that sends you looking in exactly the wrong place: nothing about the mouse was broken; the thing you were trying to click had been pushed off the bar.
- **Every status-bar segment now paints its own background.** Only the session badge looked padded before. A terminal cell's background fills the whole character cell including the line-height leading, so a segment with a background reads as a padded cell and one without reads as bare text floating on the bar -- which looks exactly like inconsistent vertical padding. The window cells sat on a colour 10 RGB-steps from the bar (invisible in practice) and the `status-right` path painted no background at all. All three shipped themes were affected and all three are fixed.

### Verification

- 1725 tests passed in the Digital Twin Universe (`make test`), up from 1721 -- 28 of them cover the tmux config feature, all exercised against a real tmux server on private sockets with paths redirected to a sandbox.
- The four new regression tests immediately caught that `steel` and `catppuccin-mocha` carried both status-bar bugs too; the assertions are structural (bounded left+right column budget, no unbounded path format, every segment paints a background, window cell backgrounds at least 40 RGB-steps from the bar) so a future theme cannot reintroduce either bug.
- `ruff check` and `pyright` clean on all new and changed files.
- Installed and running on a live server with 51 sessions throughout: session count and server PID unchanged across every step, including the live theme reapply.


## v0.30.1 (2026-07-30)

### Bug Fixes

- **The soft deck's grid no longer collides with the dial and touch strips.** The grid was being sized correctly for the space above the strips but then centred against the full viewport, so it sat too low and its bottom row disappeared behind them — 50px of overlap with dials on a phone in landscape. Separately, the two strips both anchored to the bottom of the screen rather than stacking, so with dials and the touch strip both enabled the strip painted over the lower part of every dial. A code comment claimed that stacking already happened; it never did, and that comment is corrected. Contrary to the original report this was not limited to having both enabled — dials-only and strip-only were already overlapping at phone-landscape sizes and only looked correct on larger screens, where extra letterboxing happened to hide it. Worth noting: this was invisible to overflow-based checks because `position: fixed` elements never enlarge document flow — `scrollHeight` equalled `clientHeight` in every broken configuration. The new tests compare element rectangles directly instead.

### Verification

- 685 frontend tests passed (Node 22, baseline 680 + 5 new).
- No Python changes (frontend + CSS only).
- All 15 dial/strip × viewport combinations tested in real Chromium against a scratch instance: every grid/strip and strip/strip overlap value is ≤ 0 (negative = letterbox slack). Screenshot at 844×390 with both enabled confirmed visually clean.


## v0.30.0 (2026-07-30)

### Added

- **The soft deck's touch strip now shows live status, like the Stream Deck+ does.** On the hardware, that strip is an LCD the sidecar repaints on every poll with the current view, page, session count and active session — and its touch input is deliberately inert, so it is purely a display. The soft deck's strip previously showed only static text naming what each zone was bound to, which told you how it was wired but never what the system was doing. It now carries the same live headline the hardware does. Hostname is omitted: `DESIGN_LAYOUT.md` §1 already ruled it out of the phone deck as install-constant rather than state, and that reasoning applies here unchanged. The strip's own tap, drag and swipe gestures are unaffected — they are a soft-deck-only addition, since the physical strip's touch does nothing — and the status line shares the strip's existing footprint rather than growing it.

### Verification

- 680 frontend tests passed (Node 22, baseline 671 + 9 new).
- No Python changes (frontend only).
- Live status line identical with dials present and absent, still populated when nothing is bound, and mid-drag it changed `all · p1/3 · 8 sessions · ACTIVE: sess-3` → `all · p2/3 · …` read back from the DOM.

## v0.29.0 (2026-07-30)

### Bug Fixes

- **You can now find the soft deck's settings.** v0.27.0 shipped them behind a 600ms motionless long-press on the VIEW key with nothing on screen to suggest it existed — the person who asked for the feature couldn't find it. Tapping VIEW already repaints the grid with the view picker, so Settings is now a key on that page: no permanent pixels, no permanent key slot, reached by a tap people already make. It is a real focusable button with an accessible name, so a screen reader can find it too — the old long-press was a timer on a pointer event and had no presence in the accessibility tree at all. The long-press survives as a shortcut, now with the same 8px movement tolerance the dials use and a filling ring while it arms, so a hold that fails tells you it failed instead of doing nothing. On a grid too small to carry control keys, or one with only a single free slot, the Settings key is omitted rather than displacing the picker's own job.

### Verification

- 671 frontend tests passed (Node 22, baseline 664 + 7 new).
- No Python changes (frontend + docs only).
- Soft deck settings now discoverable: tap VIEW to open picker, Settings key visible and tappable, long-press shortcut now has a filling ring and 8px movement tolerance (same as dials), settings panel opens. Accessible name verified via Playwright `aria_snapshot()`.

### Note on Design Documentation

The design documents were amended in this commit: `DESIGN_SOFTDECK.md` rejected long-press by name and `DESIGN_LAYOUT.md` said "no settings gear," and both now carry dated addenda recording what shipped and why. `DESIGN_LAYOUT.md`'s 52px header is marked specified-but-never-built.

## v0.28.0 (2026-07-30)

### Features

- **The soft deck now has an emulated touch strip.** Tap a zone, swipe left or right, or drag — each bindable separately, addressed as `strip.N.tap`, `strip.N.drag`, `strip.swipe.left`, and `strip.swipe.right`. Swipe and tap drive the same momentary actions the keys use, and drag emits the same signed ticks the dials do, so the existing 19-action catalog works on it unchanged. One genuinely new action, `brightness_set`, takes an absolute position along the strip — the touch strip's canonical use on the hardware, and the one thing the existing catalog had no way to express. It lives in its own table so the 19-action catalog that mirrors the Stream Deck sidecar stays byte-for-byte intact. Gestures are disambiguated with the same 8px/300ms tap threshold the dials already use, and a zone with a bound drag never also fires a swipe.

### Verification

- 664 frontend tests passed (Node 22, baseline 634 + 30 new).
- 1696 Python tests passed (no regression).
- All CI jobs green: Python 3.11/3.12/3.13 (muxplex and muxplex-client), test-latest-deps, and test-frontend.
- Touch strip verified on real Chromium at phone landscape (844×390): tap-to-single-fire on zone, swipe-left −10 / swipe-right +10 measured by CSS brightness delta, drag continuous and absolute (zone baseline to 80% → brightness 80%, verified mid-gesture).
- `brightness_set` action verified callable and delivering absolute position.

## v0.27.0 (2026-07-30)

### Features

- **Sort your session lists, including by attention.** The dashboard and sidebar now have a sort selector — manual, alphabetical, recent, and attention (bells first, then by activity) — matching the ordering the Stream Deck sidecar has had for a while. The choice is the existing server-synced `sort_order` setting, so it follows you across devices.

- **The soft deck has a settings menu.** Long-press the VIEW key to open it. Rebind any key from the full action catalog, override the grid (rows × columns) per device, add up to four emulated dials — drag to turn, tap to push — and set sort, poll interval, and brightness. Config is per-device with export/import, since a phone and a tablet genuinely want different layouts. `?settings=1` and `?reset=1` always work, so a configuration that locks you out of the UI is still recoverable.

### Bug Fixes

- **The sidebar was never applying your sort order at all.** It always rendered raw server order, silently disagreeing with the grid regardless of what the setting said. Both surfaces now share one ordering path.

### Verification

- 634 frontend tests passed (Node 22, baseline 592 + 42 new).
- 1696 Python tests passed (no regression).
- All 8 CI jobs green: Python 3.11/3.12/3.13 (muxplex and muxplex-client), test-latest-deps, and test-frontend.
- Soft deck settings verified on real Chromium at landscape (844×390): long-press opens settings, 3×5 override renders exactly 15 keys, dial count 2 renders 2 dials, dial tap fired `brightness_down`, 2-tick drag fired `brightness_cycle`, `?reset=1` restored defaults.
- Sort order verified to apply consistently to dashboard, sidebar, and grid.

## v0.26.1 (2026-07-30)

### Bug Fixes

- **The dashboard now respects your phone's rotation lock.** Its manifest declared `"orientation": "any"`, which reads like "no preference" but is not — per the Web App Manifest spec it means "allows the app to rotate freely to match the orientation of the device," and Android bakes that into the installed app as sensor-based rotation that applies *even when the user has locked rotation*. v0.26.0 fixed the dashboard opening stuck in landscape but left this in place, so it swapped one wrong behavior for another: free rotation that ignored the lock. The orientation preference is now omitted entirely, which is what leaves the device's own setting in charge. Browser tabs were never affected — manifest orientation only applies to an installed app. The deck's deliberate forced landscape is unchanged.

### Note for Existing Users

Anyone with the dashboard already installed must remove it from their home screen and re-add it — Android caches a PWA's manifest at install time, so a server-side change does not reach an already-installed icon.

### Verification

- 457 frontend tests passed (Node 22).
- Real hardware verified: device rotation lock now honored on the dashboard; deck's landscape lock is unchanged.

## v0.26.0 (2026-07-30)

### Bug Fixes

- **muxplex no longer runs the tmux server inside its own service cgroup.** When no tmux server is running, muxplex starts one — and until now that server became muxplex's child, which put it in the service's control group. Anything that stopped the service could then take every session on the machine with it. v0.24.0 shipped `KillMode=process` to stop systemd doing that, but that was a guard on the unit file, not a fix for the relationship: edit the unit, or run muxplex under a different supervisor, and the hazard came back. Session-spawning subprocesses now run in their own transient systemd scope, so the tmux server is never in muxplex's cgroup to begin with. Verified by deliberately setting `KillMode=mixed` and restarting: the sessions survive anyway. On macOS, and on Linux without a usable systemd user session, there is no such cgroup and nothing changes — and if the escape is expected but genuinely unavailable, it is logged loudly rather than passed over in silence.

- **The dashboard no longer opens sideways after you have visited the deck.** The deck deliberately forces landscape. Because the dashboard's scope covers the whole site, reaching the deck from inside it kept you in the same browsing context, and the deck's orientation lock could outlive the deck and follow you back. The dashboard now releases any inherited lock when it starts. The deck's forced landscape is unchanged.

### Verification

- 1692 Python tests passed (baseline 1676 + 16 new tests covering cgroup escape on mixed KillMode, escape unavailability logging, macOS no-op, and dashboard orientation release).
- cgroup escape verified in a DTU: tmux server moved from `.../muxplex.service` into `.../run-<id>.scope`, and sessions survived a `systemctl restart` with `KillMode=mixed` deliberately set — a test that fails fatally under the old code and passes under the new one.
- Dashboard orientation unlock verified on real hardware: visited deck (locked to landscape), returned to dashboard, confirmed dashboard immediately reverted to any-orientation behavior.
- 583 frontend tests passed (Node 22).

## v0.25.0 (2026-07-29)

### Features

- **`muxplex restore` now actually restores.** v0.24.0 taught muxplex to remember which sessions existed and to tell a deliberately-closed session apart from one lost when the tmux server died. This release acts on that: it recreates the lost ones, using the same `new_session_template` that created them in the first place, so a restored session comes back with the structure it had rather than a bare shell. Sessions you closed on purpose are never resurrected. Running it twice does nothing the second time. If some sessions fail to come back it names exactly which and exits non-zero rather than reporting a partial success as a win. It asks before creating anything — `--yes` skips the prompt for scripted use.
- **Restore runs in your shell, not inside the service.** Deliberate. muxplex spawns the tmux server as its own child when none is running, which is what let a service restart take out 44 sessions in the first place; creating sessions from a short-lived command in your own shell keeps restore out of that path entirely, and works whether or not the service is running.

### Verification

- 1676 Python tests passed (baseline 1655 + 21 new restore tests covering full restore with 45 sessions, tombstoned session immunity, idempotency, partial failure handling, and exit code verification).
- Restore verified on isolated tmux servers: full 45-session restore with all four windows and cwd asserted for every session, tombstoned session staying dead, idempotency (second run is no-op), and partial failure (names the failed session, exits non-zero).
- API endpoints for restore are not implemented — restore is CLI-only by choice. A one-tap restore button on a phone is not the right affordance for an operation that spawns dozens of processes, not until it has a proven track record.

## v0.24.0 (2026-07-29)

### Bug Fixes

- **The service unit shipped a setting that could destroy every session on the machine.** `muxplex service install` wrote `KillMode=mixed`, which tells systemd to SIGKILL every process in the service's control group whenever the service stops. Because muxplex starts the tmux server itself when none is running, that server became muxplex's child — so any restart, upgrade, or crash-loop was a mass kill of every session on the box. This is not hypothetical: it destroyed 44 live sessions on the author's machine on 2026-07-29. New installs now get `KillMode=process`, which signals only muxplex itself. **Anyone who installed the service before this version should reinstall it** — the old unit file is still on disk and still carries the old setting.

### Features

- **muxplex now remembers which sessions it has seen, so a lost tmux server is recoverable.** Until now tmux was the only record of what existed; when the server died, that knowledge died with it. muxplex keeps a small manifest and can tell the difference between a session you deliberately closed and one that vanished because the server underneath it went away — the first is never resurrected, the second becomes a restore candidate. `muxplex restore --dry-run` shows what a restore would do. **Restore execution is not in this release**; nothing is created, killed, or restarted by this change.

### Verification

- 1655 Python tests passed (1626 baseline + 29 new tests covering manifest recording and restore candidate selection).
- A real tmux server was killed in a DTU and the manifest survived it with the right sessions marked as restore candidates; a deliberate single-session kill was correctly tombstoned and would never be resurrected.
- Service unit verified to contain `KillMode=process` instead of `mixed`, and no other tmux-killing paths remain in the codebase besides user-initiated `delete_session_template`.

## v0.23.0 (2026-07-29)

### Bug Fixes

- **The deck's three control keys were blank.** `controlKeyContent` computed the right labels, was exported, and was covered by nine tests — but nothing ever called it. The renderer was handed the context where the content belonged, so every field came back undefined and painted as an empty string. The keys are now wired to the function that was always meant to fill them, and the render path was restructured so that a key face can only be painted from a computed plan — there is no longer a second route from state to the screen for that bug to hide in.

- **The deck listed sessions alphabetically instead of by attention.** It asked the server for a view without saying how to sort it, so the server fell back to alphabetical while the hardware decks defaulted to attention order. The two now agree.

- **Long session names were clipped without warning.** The deck left fitting to the browser, which trims a centred label from whichever side overflows and leaves no mark that anything was cut. Names are now measured and truncated the same way the hardware does it, so what you see is always a real prefix followed by an ellipsis.

### Features

- **The hidden view is now reachable from the deck.** It was possible to hide a session from a deck and then have no way to bring it back, because the server left `hidden` out of the browsable view list.

### Verification

- 1626 Python + 583 frontend tests passed (DTU and Node 22).
- All four fixes proven by reading the DOM in real Chromium at 915×412 against scratch instance.
- Golden fixture proven to fail on drift (perturbed value → red; restored → green).
- No remaining path from state to a painted key that bypasses the plan.

## v0.22.0 (2026-07-29)

### Features

- **The deck is now a software Stream Deck rather than a web page.** It draws a fixed grid of keys sized so every key is legible at arm's length, fits as many as the screen allows, and shows them all at once — there is no scrolling. Extra sessions move to further pages reached by dedicated previous and next keys, exactly as the hardware does, and choosing a view takes over the whole surface with a back key instead of sliding a panel over the grid. The key faces, the three-zone layout, and the control vocabulary are the same ones the physical decks use, so the two surfaces behave alike. On a phone in landscape this works out to a four-by-eight grid — the same geometry as the largest hardware deck.

- **Installed apps now carry proper names** — "Muxplex" and "Muxplex Deck" — instead of lowercase placeholders.

### Bug Fixes

- **The Android certificate instructions sent people to the wrong place.** Android offers two ways to install a certificate and only one of them accepts a certificate authority; the instructions did not say which, so following them produced a refusal with no explanation. The steps now name the correct choice, warn about the one that looks right and is not, and note that the browser must be fully closed and reopened rather than merely reloaded.

### Verification

- 1625 Python + 567 frontend tests passed.
- All eight CI jobs green: Python 3.11/3.12/3.13 (muxplex and muxplex-client), test-latest-deps, and test-frontend (node:test).
- Deck grid responsive to phone form factor: 4×8 keys on flagship (915×412), 3×7 on compact (780×360), no scroll and no overlap at either resolution.

## v0.21.0 (2026-07-29)

### Features

- **Installing the certificate is now a link, not a chore.** A new setup page at `/setup` offers the certificate authority for download with step-by-step instructions for the device you're on, and `/ca.crt` serves it with the content type Android recognises. Until the certificate is installed a browser marks the site untrusted, which — beyond the warning — silently prevents installing the deck as a real app, because browsers refuse to install from an origin they consider insecure. Both pages are reachable without logging in; only the public certificate is served, never the private key.

- **The deck now installs as a genuine fullscreen app in landscape.** It previously asked to be a standalone window, which is not enough for a browser to honour a fixed orientation — the deck now declares fullscreen, so it locks to landscape on its own instead of requiring the phone's rotation lock to be toggled by hand. A minimal service worker was added purely to satisfy the install requirement; it caches nothing, deliberately.

### Verification

- 1625 tests passed (baseline v0.20.1: 1604 passed, +21 new tests covering PWA setup and service worker integration).
- All eight CI jobs green: Python 3.11/3.12/3.13 (muxplex and muxplex-client), test-latest-deps, and test-frontend (node:test).
- Deck installability validated end-to-end on Android Edge: setup page accessible without login, CA certificate downloadable with correct MIME type, app installs fullscreen and locks to landscape, no scroll or dropdown menus, control grid responsive to phone form factor (3×7 on compact, 4×8 on flagship).

## v0.20.1 (2026-07-29)

### Bug Fixes

- **Deck tiles overlapped each other on a phone held upright.** Rows in the tile grid were sized from the longest unbroken word in each tile rather than from the tile's actual rendered size, so every tile was drawn taller than the space reserved for it and painted over the one below — leaving terminal previews stacked on top of each other and unreadable. Rows are now sized from the tiles themselves. Landscape and tablet layouts were unaffected and are unchanged.


## v0.20.0 (2026-07-29)

### Features

- **A new `/deck/` page turns a phone or tablet into a session switcher.** Propped beside the laptop, it shows the sessions in the current view as tiles; tapping one switches the laptop to that session. It can be installed to a phone's home screen as its own app. Scope is deliberately narrow — switch session and change view, nothing else.

- **The deck shows what it actually knows.** A tap shows a distinct in-flight state rather than claiming success before the server has answered, and reverts visibly if the request fails. If the server stops responding, the whole grid dims rather than continuing to present stale tiles as current.

- **The screen is kept awake while the deck is open**, with a control that reports the true state rather than an assumption, and can be toggled.

### Bug Fixes

- **Logging in no longer discards where you were going.** The login form always redirected to the site root, so a deep link was lost and — for anyone running the deck as an installed app — logging in ejected them out of their app into a browser tab showing the terminal. The intended destination is now preserved and validated as a same-origin path, rejecting absolute URLs, protocol-relative URLs, embedded schemes, and path traversal.

### Verification

- 1604 tests passed (baseline v0.19.0: 1412 passed, +192 new tests covering the deck route and authentication validation).
- All eight CI jobs green: Python 3.11/3.12/3.13 (muxplex and muxplex-client), test-latest-deps, and test-frontend (node:test).
- Deck functionality verified end-to-end in a real browser against a scratch-instance muxplex server: session tile tap confirmed active_session changed on the server, and visual state transitions (in-flight, success, error, stale-server dim) all verified.
- Link preservation validated: deep links like `/deck?view=2&session=work` survive login and redirect correctly.

## v0.19.0 (2026-07-26)

### Features

- **A first-class Python client, published as `muxplex-client`.** The Stream Deck sidecar had been hand-rolling 264 lines of httpx against four endpoints, and the Amplifier tool module planned next would have written a second copy — while AGENTS.md already carried a section titled "Semantics external clients re-implement today (change with care)," a standing admission that this duplication was a known drift hazard. The client ships as a separate distribution from the same repo and the same tag, depending only on httpx: consumers install a laptop-friendly package rather than an ASGI server, a PAM binding, and a `muxplex` console script that would collide with a real server's config directory. Sync and async clients share one I/O-free core holding all parsing, error mapping, and sentinel logic, so the two transports differ only in the shape of their awaits — both are genuinely required, since converting the deck's threaded HID architecture to async would be a rewrite while a synchronous call inside an Amplifier tool would block the event loop. The surface is twelve methods against roughly twenty-nine endpoints, deliberately excluding `PATCH /api/settings`: a convenience wrapper there is exactly the shape of the views-collapse incident, and doing it safely needs compare-and-swap plus 409 backstop discrimination built in, which is a v2 design rather than a v1 convenience. `run_shell_command()` composes the completion-sentinel convention over public primitives, carrying the digit-anchor rule that keeps tmux's input echo — which shows the literal unexpanded `$?` before the shell runs — from producing an instant false "done" with a bogus exit code.

- **A contract test that makes the second distribution safe.** Living in the server's own suite and driving the real ASGI app in-process, it asserts that the client's parsed fields match the raw endpoint JSON, that its key allowlist equals `terminal_input.ALLOWED_KEYS`, that its capture-depth constants match `sessions.py`, and that `Bell.needs_attention` agrees with the server's implementation across an eight-row truth table. This is what a separate repo structurally could not do: it catches a client/server break in the same pull request that introduces it, rather than against the last published server.

### Internal

- **The release path now knows the second distribution exists.** A bare `uv build` builds only the root package, so a tag would have published half the release behind a green workflow; the publish step now uses `--all-packages`. Root `testpaths` confined collection to the server's tests, so the client's suite would never have run in CI; a dedicated `test-client` job now runs it across all three supported Python versions. A contract test asserts both `pyproject.toml` versions match, so a release that bumps one and forgets the other fails in the same pull request instead of publishing mismatched wheels.

### Verification

- 1570 tests passed / 5 deselected in muxplex server suite (baseline v0.18.0: 1546 passed, +24 new tests for client integration). The new `muxplex-client` package runs 44 pure-contract tests across Python 3.11/3.12/3.13 in CI.
- All six CI jobs green on the feature commit: frontend (node:test), Python 3.11/3.12/3.13, test-latest-deps, and the new test-client matrix (3 versions).
- Contract test verified: `test_client_version_matches_server_version`, `test_client_fields_match_endpoints`, `test_client_allowlist_matches_server`, and `test_bell_needs_attention_consistency` all pass.
- Both distributions published to PyPI from the same v0.19.0 tag: muxplex and muxplex-client.


## v0.18.0 (2026-07-26)

### Bug Fixes

- **Accepted `/input` calls are now actually audited.** `uvicorn.run(..., log_level="info")` configures only uvicorn's own loggers; it never touched the root logger or any `muxplex.*` logger, so every module logger sat at WARNING with no handler attached. Every `_log.info` audit line for an accepted terminal-input call was silently discarded — while the *rejected*-input warning appeared normally, since Python's handler-of-last-resort surfaces WARNING and above. That asymmetry hid the defect. `configure_logging()` now scopes the `muxplex` package logger to INFO with a single idempotent handler, deliberately not configuring the root logger, which would drag every third-party dependency to INFO and bury the audit trail. This matters beyond tidiness: the agent guide documents this log as the operator's record of what their agents typed, and lists it as one of only three protections remaining in the wide-open `input_allowed_sessions: ["*"]` posture. The guarantee was documented but not delivered. Notably a test already covered this line and stayed green throughout, because it used `caplog.at_level(logging.INFO, logger="muxplex.main")` — forcing the very level it was meant to verify. The replacement starts from an unconfigured logger, asserts the broken state is real, then proves a record reaches an independently attached handler.

- **The tmux bell hook now self-heals.** Startup registration was wrapped in `except Exception: pass` with a comment promising the hook would be set on the first poll — but nothing anywhere re-registered it. The failure it names, tmux not yet running at startup, is the normal case on a fresh boot, so the most likely path left bells permanently dead with no error, no log, and no signal. Confirmed in a clean container: bell counts frozen until `/api/internal/setup-hooks` was called by hand. Registration is now a single shared helper called from startup, the poll cycle, and the manual endpoint, retried from the poll cycle only while genuinely unarmed — so it heals on the next 2-second cycle and costs a boolean read thereafter rather than a subprocess every cycle forever. Failures log at WARNING with the real tmux error, and `GET /api/instance-info` exposes `bell_hook_armed` so the state can be queried rather than inferred.

### Features

- **Caller-controlled read depth for pane snapshots.** `capture_pane()` hardcoded a 30-line window, so `seq 1 100` returned only lines 48 onward with no API able to recover the rest — a hard ceiling for any agent running `pytest -v`, a build, or anything else output-heavy. `POST /api/sessions/{name}/input` now accepts an optional `lines`, and a new `GET /api/sessions/{name}?lines=N` performs a live single-session capture without typing anything, covering the case the read-back structurally cannot: polling a long job started earlier. The default stays 30 so existing callers are unchanged; the maximum is 2000, and an out-of-range value is a 400 rather than a silent clamp, because a caller believing it received 5000 lines while getting 2000 is worse than an explicit rejection. tmux `history-limit` is now set explicitly per session, since sessions are created through an operator-configurable template and muxplex never controlled retained scrollback — without it a deep request could return fewer lines than asked for, a worse lie than the original honest ceiling. The bulk `GET /api/sessions` deliberately does *not* take a depth parameter: it serves one shared poll cache consumed simultaneously by the PWA, the Stream Deck sidecar, and agents, and a per-request override there would either fork that contract or fan out one live tmux call per session on every request.

### Documentation

- **The agent guide now documents proven unattended-operation patterns.** A completion-sentinel convention is the primary recommendation, verified end-to-end against a live instance for both a successful long command and a nonzero exit — `last_activity_at` alone cannot distinguish a command running silently from one finished at an idle prompt, and that ambiguity is the actual blocker on unattended operation. A bell-on-nonzero-exit convention is documented alongside it, with the explicit warning that nothing an agent naturally runs will ring a bell otherwise. The poll-cache guidance was corrected from a guessed "sleep ~3s" to the measured reality — a just-created session resolved on the third attempt at 0.3s spacing, under one second — with a retry loop rather than a blind sleep. AGENTS.md's copy of the same claim was corrected to match.

### Verification

- 1546 tests passed / 5 deselected in an isolated Digital Twin Universe container (baseline v0.17.0: 1522 passed, +24 new tests covering the audit-log fix, bell self-heal, and sentinel completion patterns).
- All five CI jobs green: frontend (node:test), Python 3.11/3.12/3.13, and test-latest-deps (mirrors user install behavior).
- Sentinel completion detection verified end-to-end against a live instance: `sleep 8 && echo` matched at 8.15s with exit_code=0, `sleep 3; false` matched at 3.03s with exit_code=1.
- Bell hook self-heal verified: fresh DTU with hook registration failure, then polled until hook healed on the next cycle.
- Audit log verified: 12 accepted `/input` calls → 12 matching INFO lines in audit log.

## v0.17.0 (2026-07-26)

### Bug Fixes

- **Selecting a session no longer snaps back to the previous one.** `openSession()` sets the local viewing session synchronously on a sidebar click, then fires `PATCH /api/state` fire-and-forget. A dedicated 1-second `/api/state` poll (added in `2f50c22`) meant a poll frequently landed in the window before that PATCH reached the server, read the server's still-stale `active_session`, concluded a *remote* device had switched away, and force-reopened the previous session. The 1s poll did not create the race; it made it common. A pending-write counter now tracks genuine local switches — incremented on a real user switch (not on `restoreState()` or the follow logic's own re-opens), decremented when that switch's PATCH actually settles rather than after a guessed timeout — and a divergent remote read is ignored while any local switch is in flight. Cross-device following is unchanged; that feature is the entire reason the PWA watches remote state.

- **TMUX_TMPDIR now propagates into the installed service environment.** A user who set `TMUX_TMPDIR` in their shell, ran `muxplex service install`, and never set the `tmux_socket_dir` setting got a service pointed at the default socket directory — `GET /api/sessions` returned an empty list with no error, and live sessions were simply invisible. Reproduced end-to-end in an isolated container: a real session under a custom socket dir was completely absent while `/api/instance-info` reported `/tmp/tmux-0`. The installer now bakes its own `TMUX_TMPDIR` into the systemd `Environment=` line and the launchd plist, mirroring the existing PATH propagation, and re-installs restart (systemd) or bootout-then-bootstrap (launchd) so a changed value actually applies. An explicit `tmux_socket_dir` setting remains authoritative; only the fallback changed.

### Documentation

- **A vendor-neutral agent usage guide.** `docs/AGENT_GUIDE.md` is a document any agent can be pointed at — Amplifier, Claude Code, Codex, or a shell script with curl. It carries the reasoning that is not derivable from the source: that the asset protected by the input fences is the operator's own live pane rather than the host, that the endpoint's intended caller holds the same Bearer key as its most capable attacker, why the allowlist check runs before the existence check (so a 403 cannot be used as an existence oracle), why glob matching casefolds both sides rather than relying on platform-varying `fnmatch`, and why an empty allowlist denies rather than permits. It states plainly that the security boundary is the allowlist and not the content — typing an executable line into a shell pane will run it, by design — and documents both the scoped and wide-open postures honestly, including which protections remain in each. It also documents the ~2s poll-cache race that makes a just-created session 404 on `/input`, as a known race with a retry pattern rather than a mystery.

### Internal

- Source-text assertion tripwires in the frontend test suite were deduped and documented.
- A doc claim in AGENTS.md asserting an end-to-end canary test that does not exist was corrected to cite the test that does (`test_input.py` `test_text_sent_literally_via_argv`, which asserts the exact argv). A doc claiming evidence it does not have is the same fabricated-attestation failure this repo's testing rails exist to prevent.

## v0.16.1 (2026-07-26)

### Bug Fixes

- **Autofill suppression extended from one input to every input where it belongs.** v0.15.1 fixed the new-session name field; the rest of the PWA's text inputs were still bare. Now covered: the "+ New View" name inputs in both the header and sidebar view dropdowns, the inline view-rename input, the remote-instance URL and display-name inputs, and — in `index.html` markup — the terminal search box and the device-name setting. The seven attributes (`autocomplete`, `autocorrect`, `autocapitalize`, `data-1p-ignore`, `data-lpignore`, `data-bwignore`, `data-form-type`) now live in one `AUTOFILL_SUPPRESSION_ATTRS` constant applied by one `_suppressAutofill()` helper, rather than being copy-pasted per call site. The two static inputs carry the attributes as literal markup rather than via the helper, deliberately: Chrome scans the DOM for autofill targets at parse time, before app.js runs, so markup is the only placement early enough to matter. That markup/JS duplication is held in sync by a test that derives its expectations from the exported constant, so adding an eighth vendor attribute without updating the markup fails CI instead of silently drifting.

  Three exclusions are deliberate and each is now pinned by a test, so a future "apply it everywhere" sweep cannot quietly break them: **login.html** (both inputs keep `autocomplete="username"` / `current-password` — it is the one form where password managers are wanted, and it was left byte-for-byte untouched), the **federation key** input in `_buildRemoteInstanceRow` (`type="password"` — a genuine secret a user may legitimately keep in their password manager), and the six **settings checkboxes** (autofill does not target checkboxes).

  Scope note: this sends every documented opt-out signal each vendor publishes. It does not, and cannot, prove a given password manager honors them — that is vendor behavior no test here can exercise.

### Internal

- **Four frontend assertions in `test_frontend_js.py` were pinning implementation shape, not behavior.** They regex-extracted the body of `_createSessionInput` and asserted the literal strings "autocomplete" and "spellcheck" appeared inside *that function*. Factoring the attributes into the shared `_suppressAutofill` helper moved those literals one call away and failed all four Python jobs, while the behavior was not merely intact but extended to five more inputs — a false negative, and the same class of stale-source-assertion breakage recorded in v0.13.0. They now follow the indirection and assert both halves of the chain: that the factory delegates to `_suppressAutofill()`, and that the helper/constant is what actually sets the attribute. The guarantee is unchanged; only the shape it is pinned to moved. Caught by CI, which is the only place the Python suite can run when the dev host is also serving muxplex.

## v0.16.0 (2026-07-26)

### Features

- **Surface the running version, not just the installed one.** `muxplex doctor` reported only the INSTALLED version, so a machine that had been upgraded but whose service had not been restarted looked perfectly healthy while still serving stale code — the exact situation that stranded a live server on 0.14.0 while 0.15.0 sat installed. Doctor now probes the locally-configured host and port and reports three distinct states: matching (`Running: v0.16.0 (matches installed)`), mismatched (naming both versions plus how to restart), and not serving (a normal state, worded so it can never be confused with staleness). The PWA gained a read-only Version field under Settings → Display, populated from the `/api/instance-info` fetch it already makes — no additional network call. Federated devices now carry `deviceVersion` through the existing federation path, surfaced in device-badge tooltips, the grouped sidebar's per-device header, and federation status tiles. A remote that is unreachable, too old to serve `/api/instance-info`, or returns a malformed body renders as "version unknown", deliberately formatted so it can never be mistaken for a real version — an unknown that looks like agreement is worse than no data. The remote probe runs concurrently with the existing `/api/sessions` poll, so it adds no latency, and it still works when that poll returns 401 because `/api/instance-info` is unauthenticated.

### Documentation

- **Recommend the PyPI install.** The README's `uvx` and `uv tool install` commands pointed at the git URL, predating publication to PyPI; both now use the published package. It also documents how to upgrade, and warns about the trap that prompted this: `uv tool upgrade` resolves strictly within the recorded requirement, so an install pinned to a tag (`...@v1.2.3`) reports "Nothing to upgrade" indefinitely — which is precisely how a local install sat on 0.14.0 while 0.15.0 was published. Unpinned git tracking remains documented for anyone wanting unreleased commits.

### Verification

- 1514 tests passed / 5 deselected in an isolated Digital Twin Universe container.
- 491 frontend tests via `node --test tests/*.mjs` (including the previously-undiscovered `test_terminal.mjs`).
- All five CI jobs green: frontend (node:test), Python 3.11/3.12/3.13, and test-latest-deps (mirrors user install behavior).
- Running vs installed version mismatch rendered end-to-end against real muxplex 0.14.0 server in container.

## v0.15.1 (2026-07-26)

### Bug Fixes

- **Browser and password-manager autofill triggering on the new-session name input.** The field already set `autocomplete="off"`, which is not sufficient for this field's shape: it is a bare, form-less text input whose placeholder reads "Session name", served from an origin that also hosts a real login form (`frontend/login.html`) — exactly the shape password managers heuristically classify as a username field, and one they ignore `autocomplete="off"` on by design. `_createSessionInput()` — the single factory feeding all three creation entry points (header `+`, sidebar `+ New`, and the mobile FAB overlay) — now also sends each vendor's documented per-field opt-out: `data-1p-ignore` (1Password), `data-lpignore` (LastPass), `data-bwignore` (Bitwarden), and `data-form-type="other"` (Dashlane). It additionally sets `autocorrect="off"` and `autocapitalize="off"` for the mobile PWA path, where iOS otherwise capitalizes and "corrects" tmux session names as they are typed. Wrapping the input in a `<form>` was considered and rejected: a form containing exactly one text input is the canonical username-first login step, so it would make the field a *larger* autofill target, and native submit-on-Enter would race the existing keydown handler and risk a full page reload in the installed PWA. A doc comment on the factory records why `autocomplete="off"` alone is insufficient, so the attributes are not later removed as redundant. Frontend-only; no API or Python changes. 436 frontend tests pass, including a new regression test pinning every suppression attribute.

## v0.15.0 (2026-07-26)

### Bug Fixes

- **Refuse to terminate a healthy muxplex when taking the port.** `_kill_stale_port_holder()` ran on every `muxplex serve` startup, called `lsof -ti :<port>`, and SIGTERMed whatever it found — unable to distinguish a stale holder from a healthy running server. Any second invocation of the startup path silently killed the live service, producing a clean graceful shutdown with no crash and no systemd `Stopping` line: nearly undiagnosable from logs. It now probes the holder via `GET /api/instance-info` first and kills only on positive evidence the holder is not serving; a healthy holder yields an actionable error naming the port and PID, and exit 1 instead of starting. `--force-take-port` restores the old unconditional behavior. A missing or erroring `lsof` still never blocks startup. The restart race is bounded: if an old instance is still draining, the new process exits non-zero and systemd retries after `RestartSec`.

- **Never accept() a WebSocket the client already abandoned.** `terminal_ws_proxy` performs real awaited work before accepting — killing and respawning ttyd, then waiting for it to bind. If the browser disconnected during that window, uvicorn's `connection_lost()` had already flipped its handshake-complete flag, so the subsequent `accept()` raised `RuntimeError: Expected ASGI message 'websocket.send' or 'websocket.close', but got 'websocket.accept'`, several times per hour in production. The wait is now raced against a disconnect watcher and `accept()` is skipped entirely when the client is already gone. Notably this only manifests on uvicorn's newer sansio WebSocket implementation, which is what a fresh `uv tool install` resolves — see Internal.

### Internal

- **Test-suite safety rails.** Running the suite on a host also serving muxplex destroyed real state twice in one day: a test overwrote a live `~/.config/muxplex/settings.json`, and six tests SIGTERMed the running server. Both were invisible from inside the suite, because a test that damages its host still passes. Four rails now close the class: a `pytest_sessionstart` guard that refuses to run when anything serves the default port, autouse isolation of `SETTINGS_PATH`, autouse neutering of `_kill_stale_port_holder` behind an explicit `@pytest.mark.allow_real_port_killer` opt-in, and `test_safety_rails.py` pinning all of it so removing a rail fails loudly. `make test` now runs the suite inside a Digital Twin Universe container, making the safe path the default path.

- **Dependency-drift CI job.** `uv.lock` pinned uvicorn 0.42.0 while a fresh `uv tool install` resolves 0.51.0 — and the WebSocket bug above existed only on the newer sansio implementation, so CI was green for weeks while production threw errors hourly. A new `test-latest-deps` job installs the way users install, ignoring the lockfile, so this class of drift fails CI instead of hiding.

## v0.14.0 (2026-07-25)

### Features

- **TLS bootstrap endpoint: GET /api/ca** — Clients that verify TLS against muxplex's locally-generated CA previously had no programmatic way to obtain it without SSH access, and the most natural approach (pointing ca_file at the server's own certificate) is reliably wrong: the server presents only the leaf certificate on the wire, producing "unable to get local issuer certificate" when a client tries to validate against it. GET /api/ca now returns the CA certificate PEM, unauthenticated (added to _AUTH_EXEMPT_PATHS alongside /api/instance-info) — a trust anchor is not a secret, and requiring auth would be circular since a client cannot authenticate over TLS it does not yet trust. The path is derived internally from settings.get_local_ca_cert_path(); no client-supplied path, query, or header reaches the filesystem. tls.get_local_ca_cert_bytes() fails closed: missing files, unreadable paths, unparseable content, missing BasicConstraints extension, or CA:FALSE all return None → 404 with a helpful detail. Never serves private key material. Addresses a recurrent TLS onboarding friction point.

## v0.13.0 (2026-07-25)

### Bug Fixes

- **Test-suite pollution of production settings** — `test_get_state_settings_updated_at_changes_after_settings_write` never redirected `SETTINGS_PATH` to a tmp directory, so running the test suite on a machine hosting a live muxplex instance read and wrote the real `~/.config/muxplex/settings.json`. On the dev box this repeatedly overwrote a production 8-view configuration with test fixture data — the exact `{"name": "Focus", "sessions": ["alpha"]}` payload from test_api.py that had to be recovered from backups. Test now monkeypatches SETTINGS_PATH into tmp_path like all sibling settings-writing tests. Also fixed three stale assertions in test_frontend_js.py that were matching literal `api('PATCH'` strings inside function bodies; v0.11.0 correctly refactored those call sites to route through `patchSettingsGuarded()`. Assertions now verify the guarded call; a new pinning test ensures end-to-end coverage of `/api/settings` is preserved rather than silenced. These three were the only real CI failures across 9 GitHub Actions job runs (3 commits × 3 Python versions).

- **Federation-aware stale-key pruning** — `prune_stale_keys()` built its live_keys set from local sessions only, while views entries are canonical `device_id:name` and routinely reference sessions on other devices. Every peer saw every other device's keys as dead and deleted them after grace period, then LWW-synced the deletion fleet-wide — a latent mutual eraser. Now the pruner takes optional `local_device_id` and `known_remote_device_ids`, and only prunes keys where the owning device's sessions are actually known: own-device keys are always evaluable, remote keys only when that device is currently reachable (checked via existing `_federation_cache`, gated on the same `fail_count` threshold used by `/api/federation/sessions`). Devices unreachable or unknown have their grace period clock reset (not paused) — a laptop offline for a week starts fresh when it returns instead of getting pruned on arrival. Legacy bare-name entries keep existing local evaluation. Also closed a gap: the poll-cycle prune bypassed v0.12.0's destructive-write backstop; it now runs `assess_views_destruction()` and refuses to persist if a mass prune would collapse views. Default-None params preserve legacy behavior exactly. 1480 tests passed; 435 frontend.

## v0.12.0 (2026-07-25)

### Bug Fixes

- **Catastrophic views destruction via stale clients and federation sync** — A PWA tab holding an hours-old snapshot of the settings blob could send 12 PATCHes in 7 minutes resubmitting a collapsed `views` array (8 views → 1), destroying your configuration; simultaneously an older federation peer could delete view members not stored locally, then LWW-broadcast the loss to every device. Root cause: `settings_updated_at` is a single scalar covering the whole syncable blob (views, sidebarOpen, fontSize, etc.), views is replaced wholesale (never merged) by both `patch_settings()` and `apply_synced_settings()`, and any PATCH re-stamps that timestamp — so a stale views value + unrelated fontSize write + federation race = cascade failure. Four defenses: (1) **Destructive-write backstop** — a single choke point (`assess_views_destruction()`) rejects any write that collapses >1 view to ≤1, removes ≥50% of views, or removes ≥50% of total members; returns 409 with `backstop:true` and makes NO write. Protects spark-1 fleet-wide even from unupgraded 0.6.7 peers and live stale browser tabs. Single-view and single-member edits stay far below thresholds. (2) **Federation sync now runs the backstop** and gains CAS discipline it previously lacked (`PUT /api/settings/sync` now uses the same optimistic concurrency as PATCH, never overwrites destructively). Deliberately NO force override on sync — only local config file edits can collapse a view now. (3) **Separate `views_updated_at`** — advances only when `views` or `hidden_sessions` actually change, so unrelated field writes can no longer re-arm a stale views value in a race. Absent on legacy peers (v0.6.7 compat); they fall back to prior behavior still gated by the backstop. (4) **PWA re-fetches settings before views mutations** instead of trusting cached blob; on 409 treats backstop and CAS distinctly — backstop 409 reloads from server (stale client), CAS 409 retries once with fresh state (lost race).

### Features

- **Settings snapshot history** — Every write to settings creates a time-stamped backup in `<config_dir>/settings-history/` (20 most recent kept). Enables instant forensics and recovery; monotonic sequence numbering for coarse-grained ordering independent of clock skew.


## v0.11.0 (2026-07-25)

### Bug Fixes

- **Settings clobber protection via rotating snapshots and optimistic concurrency** — a browser tab holding a stale copy of the settings PATCHed it back wholesale and destroyed 7 of 8 views in a single request — recovered only because a manual file backup happened to exist. Two defenses added: (1) Rotating snapshots in `<config_dir>/settings-history/` (20 most recent kept, monotonic sequence numbering for coarse-grained ordering, best-effort so a snapshot failure never blocks the real write). All writers (API PATCH, federation sync, internal code) covered at the lowest choke point. (2) Optimistic concurrency: `PATCH /api/settings` accepts optional `expected_settings_updated_at`; on mismatch returns 409 with the current value and makes no write. Omitting the field preserves existing behavior (federation sync and other clients keep working). PWA now routes all 14 settings-writing call sites through `patchSettingsGuarded()` which sends the precondition, re-fetches on 409, re-applies the mutation to fresh state, and retries exactly once; a second conflict re-renders from server truth. Successful writes update the client's baseline timestamp.

### Features

- **tmux socket directory discoverability** — muxplex manages tmux on a configurable socket dir (e.g. `~/.tmux`) while tmux itself defaults to `$TMUX_TMPDIR` or `/tmp` — so any tool creating a session without that variable set lands on a DIFFERENT tmux server and its sessions are invisible to muxplex. That knowledge was tribal and cost real debugging time. Now discoverable: (1) `settings.resolve_tmux_socket_dir()` resolves configured value, then `$TMUX_TMPDIR`, then `/tmp/tmux-<uid>`; (2) `GET /api/instance-info` returns the resolved `tmux_socket_dir`; (3) new `muxplex env` subcommand prints exactly `export TMUX_TMPDIR="..."` on stdout (human notes to stderr) for `eval "$(muxplex env)"`, following the ssh-agent/direnv convention; (4) README gains a "tmux socket" section explaining the two-server hazard and the one-line fix.

## v0.10.0 (2026-07-25)

### Bug Fixes

- **PWA now follows remote view-membership changes** — when a session is added to or removed from a view via `PUT /api/views/{name}` (on the server or another device), the PWA's 1s poll now detects the change and re-renders all view-related UI (dropdown, grid filters, sidebar, membership checkboxes). Root cause was that `_serverSettings` was fetched once at page load and cached forever, while the session list itself refreshed every second via `/api/sessions` — so "All" updated but "Focus" did not. Fix adds `settings_updated_at` to `GET /api/state` as a change signal (render-only, not persisted), then wires a new `followRemoteViewDefinitions()` into the 1s poll alongside the existing session and active_view followers. Absent field on older servers is treated as no-op. Completes the follow-the-remote-device family: `active_session`, `active_view`, and `view_definitions`.

### Features

- **Case-insensitive glob patterns for `input_allowed_sessions`** — the remote-agent input endpoint's session allowlist now matches case-insensitive. `"Amplifier-*"` matches `amplifier-foo`, `"amplifier-*"` matches `AMPLIFIER-Foo`, exact entries like `"Agent-Sbx"` match `agent-sbx`. Implemented by explicit `casefold()` on both sides rather than `fnmatch.fnmatch` (which would be case-insensitive on macOS/Windows as a side effect of `os.path.normcase`, silently widening the fence per-platform). All other fence properties unchanged: empty list denies all, pattern order preserved, LOCAL-FILE-ONLY still enforced, boundary validation intact.


## v0.9.0 (2026-07-24)

### Features

- **Glob pattern support in `input_allowed_sessions`** — the remote-agent input endpoint's session allowlist now accepts glob patterns instead of exact literals. `"*"` allows every session; `"amplifier-*"` matches a prefix family; bare names like `"agent-sbx"` work exactly as before (backward compatible). Matching uses `fnmatch.fnmatchcase` (case-sensitive, deterministic across all platforms) rather than `fnmatch.fnmatch`, which is case-insensitive on macOS/Windows and would silently widen the fence. Fail-closed guarantees preserved: empty list denies all, non-list values normalize to deny-all before matching, non-string entries are skipped, and the fence ORDER is unchanged (is_valid_session_name 400 → input_enabled 403 → allowlist 403 → fail-closed existence 404). Patterns remain LOCAL-FILE-ONLY — not settable via PATCH /api/settings or federation sync. 15 new tests; 1426 pass.

## v0.8.0 (2026-07-24)

### Features

- **Remote-agent terminal input endpoint** — `POST /api/sessions/{name}/input` allows remote agents
  (which cannot reach tmux directly) to send keystrokes to running sessions and receive a read-back
  snapshot. The endpoint is RCE-by-design and ships with eight hardened fences: (1) global
  `input_enabled` opt-in defaulting to False; (2) per-session `input_allowed_sessions` exact-match
  allowlist defaulting to empty; (3) both keys are LOCAL-FILE-ONLY (excluded from SYNCABLE_KEYS,
  rejected by PATCH /api/settings) so neither federation peers nor Bearer-key agents can
  self-authorize; (4) fail-closed target matching (empty allowlist rejects all) plus
  `is_valid_session_name` boundary validation; (5) keystrokes sent via `tmux send-keys -l`
  (literal text, never shell) through argv subprocess, with named keys restricted to an explicit
  allowlist; (6) payload caps: 8KiB text / 64 key events per request; (7) audit logging (one
  redacted line per action, full text at debug); (8) ~400ms settle + capture_pane read-back so
  agents aren't typing blind. Injection-safety proven end-to-end against real tmux with a hostile
  `; touch` payload — it appeared literally in the pane and never executed. 39 new tests; 1412
  pass. **Deployed default-off** — no behavior change until an operator sets `input_enabled: true`
  and populates `input_allowed_sessions` on the host machine.

## v0.7.1 (2026-07-24)

### Bug Fixes

- **PWA now follows remote `active_view` changes** — when another device (a Stream
  Deck sidecar, an agent, or another browser tab) changes the active view via
  `PATCH /api/state {active_view}`, the PWA's 1s state poll now applies it and
  re-renders, instead of only reflecting the view this tab itself last set. New
  `followRemoteActiveView()` mirrors the existing `followRemoteActiveSession()`
  follow path; the remote-apply is render-only (no re-`PATCH`), so it cannot echo.
  This completes view-switch parity with session-switch: both now propagate to
  every surface.

## v0.7.0 (2026-07-24)

### Features

- **Per-session `last_activity_at` in `GET /api/sessions`** (#11) — exposed for local and
  federation sessions, derived from tmux `#{window_activity}` (deliberately not
  `#{session_activity}`, which freezes for unattended sessions). The PWA's "Recent" sort
  now reflects real activity.
- **`GET /api/view` — server-side resolved view** (#13) — returns the current view with
  membership/hidden filtering, the canonical needs-attention predicate, and sorting
  applied (`?sort=attention` for bells-first / active / recency ordering). New clients
  (Stream Deck sidecar, agents) consume the resolved view instead of re-porting the
  PWA's rules.

### Bug Fixes

- **Session-name shell-injection RCE closed** (#14, security) — session names are
  validated against a strict allowlist (`is_valid_session_name`) at create, delete, and
  connect; `shlex.quote` added as defense-in-depth; matching is fail-closed exact-match.
- **PWA now follows remote `active_session` changes** (#15) — switches made from another
  device (Stream Deck, another browser, an agent) move the sidebar highlight and
  re-attach the terminal automatically — no more stale terminal stuck on
  "Reconnecting…" until you interact.
- **Remote-driven session switch ~8.8s → ~0.7s** (#16) — session-follow now runs on a
  dedicated ~1s `/api/state` poll (decoupled from the slow federation fetch), with a
  same-session connect short-circuit. The frontend is now served with
  `Cache-Control: no-cache` (ETag revalidation) so deploys reach installed PWAs without
  manual cache-clearing, and startup logs the served `app.js` md5.
- **Federation circuit breaker** (#17) — a dead/unreachable federation remote no longer
  drags every `/api/federation/sessions` call to the full timeout (~5s → ~0.02s steady
  state): 3 consecutive connection failures skip the remote for 60s, then re-probe;
  per-remote timeout reduced to 2s. Reachable-but-erroring remotes still report their
  honest status.
- **Clean, fast shutdown** (#18) — SIGTERM now completes in ~0.5s instead of hanging 10s
  and being SIGKILLed by systemd: shutdown cancels the poll loop and WebSocket relays
  first, then kills ttyd, then closes the HTTP client; the terminal WS relay no longer
  blocks on a live ttyd reader.

### Docs

- **`AGENTS.md`** (#12) — conventions for agents and contributors: `/api/*` is a public
  control surface with multiple consumers, additive evolution, API-first-frontend-second,
  and scratch-instance testing gotchas.

## v0.6.10 (2026-07-13)

### Bug Fixes

- **tmux_socket_dir not honored by create_session and delete_session** — When a custom
  `tmux_socket_dir` was configured (to support non-default socket directories via
  `TMUX_TMPDIR`), the muxplex service's session create and delete operations were still
  hitting the default tmux socket location because the subprocess environment was not
  being passed the overridden socket settings. Fixed by wiring the shared `tmux_env()`
  helper (already in use for session enumeration) into the create and delete code paths,
  ensuring all subprocess calls honor the configured socket directory.

## v0.6.9 (2026-07-11)

### Bug Fixes

- **Custom tmux socket directories invisible to the muxplex service** — If a user sets
  `TMUX_TMPDIR` in their shell rc (e.g. to keep sockets out of the shared `/tmp`), the
  muxplex *service* process (systemd/launchd, which does not inherit the login shell's
  environment) silently fell back to tmux's compiled-in default (`/tmp/tmux-$UID`) and
  saw none of the user's real sessions, even though `muxplex doctor` (run interactively)
  reported them correctly. Added a `tmux_socket_dir` setting (default empty, fully
  backward compatible) and a shared `tmux_env()` helper, wired into both session
  enumeration (`sessions.py`) and terminal attach (`ttyd.py`), that overrides
  `TMUX_TMPDIR` and strips `$TMUX` (which otherwise takes priority over `TMUX_TMPDIR`
  when a process is itself a descendant of an attached tmux client) for tmux subprocess
  calls.

## v0.6.8 (2026-07-10)

### Bug Fixes

- **OSC 52 clipboard bridge mangled multi-byte UTF-8 characters** — Copying text out of a
  tmux session via the OSC 52 clipboard bridge (`set-clipboard on`) mangled box-drawing
  lines, bullets, em dashes, and emoji (e.g. "─" became "â", "•" became "â¢"). The
  handler decoded the base64 payload with plain `atob()`, which returns a "binary string"
  (one JS char per raw byte, effectively Latin-1) rather than a UTF-8-decoded string. This
  path was never covered by the earlier `ced0c62` WebSocket-output decode fix — mouse-select
  copy was already correct; the OSC 52 bridge was not. Fixed by re-wrapping the decoded
  bytes and running them through the same `TextDecoder` used for the primary output path.
  Added a regression test and fixed a pre-existing test-mock gap
  (`terminal-container` missing `addEventListener`) that was silently failing ~26
  unrelated tests.

## v0.6.4 (2026-05-17)

### Bug Fixes

- **Empty device block still showing in grouped grid view** — Remote federation devices with
  zero tmux sessions were producing a visible "No sessions" block in the grouped grid view.
  The v0.6.3 fix targeted `renderGroupedGrid` but missed the unconditional `status:empty`
  status-tile append in `renderGrid` itself.  In grouped mode, `status:empty` tiles are now
  suppressed (`auth_failed` and `unreachable` tiles still appear in all modes).

- **`muxplex update` fails when uv/pip is installed outside PATH** — On Unraid (root user),
  macOS (user installs), and snap-packaged systems, `shutil.which("uv")` returned None even
  though uv was present at `~/.local/bin/uv`, `/snap/bin/uv`, or `/root/.local/bin/uv`.
  New helpers `_find_uv()` / `_find_pip()` probe a curated list of known install locations
  after PATH lookup fails, so the upgrade flow works on stripped-PATH environments
  (systemd, launchd, non-login SSH shells).

- **`muxplex update` exit code propagation** — Tests added to confirm that a failed install
  exits with code 1 after the `try/finally` service-recovery block runs (behaviour was
  implemented in v0.6.2; regression test coverage added here).

## v0.5.0 (2026-05-06)

### Features
- **`muxplex setup-tls --method ca`** — generate a persistent local Certificate Authority and sign a 13-month leaf TLS certificate with it. Install the CA once on each client device to get browser-trusted HTTPS for plain LAN names (`my-host`, `192.168.1.5`) without requiring Tailscale on every client and without buying a public domain. The CA persists across regenerations, so leaf rotation does **not** require re-trusting on clients. The leaf SAN auto-discovers the host's primary outbound LAN IPv4 address and the Tailscale MagicDNS name (when Tailscale is connected), in addition to the existing `<hostname>`, `<hostname>.local`, `localhost`, `127.0.0.1`, and `::1` entries. The CA cert has proper `BasicConstraints CA:TRUE pathlen:0` and `KeyUsage keyCertSign+cRLSign` extensions, so OS / browser trust stores accept it cleanly as a Root.
- **PWA install reliability** — the `ca` method specifically addresses the symptom where an installed PWA with a self-signed-cert origin gets kicked back into a regular browser tab on relaunch. With the CA installed in the OS trust store, the PWA shell stays in standalone mode across reopens.
- **New documentation** — [`docs/TRUSTING_THE_LOCAL_CA.md`](docs/TRUSTING_THE_LOCAL_CA.md) walks through CA install on Windows (PowerShell, no admin), macOS (`security` CLI), Linux (`update-ca-certificates` / `update-ca-trust`), iOS (Profile + Trust Settings), Android, and Firefox (separate trust store).

### API
- **`muxplex.tls.generate_local_ca(ca_cert_path, ca_key_path, days_valid=3650)`** — idempotent CA generator. Reuses the existing CA if both files exist; generates a new one otherwise. Returns metadata including a `regenerated` boolean.
- **`muxplex.tls.generate_leaf_signed_by_ca(ca_cert_path, ca_key_path, leaf_cert_path, leaf_key_path, hostnames, ip_addresses=None, days_valid=397)`** — generates a leaf TLS cert signed by an existing local CA. Builds proper `KeyUsage`, `ExtendedKeyUsage serverAuth`, `SubjectKeyIdentifier`, and `AuthorityKeyIdentifier` extensions, plus `SubjectAlternativeName` from the supplied DNS + IP lists.
- **`muxplex.tls._default_lan_ip()`** — returns the primary outbound IPv4 address (no actual packets sent; uses a connected UDP socket to ask the kernel which interface would route external traffic). Returns `None` on failure.
- **`muxplex.tls._default_tailnet_name()`** — returns the host's MagicDNS name from `tailscale status --self --json`, or `None` if Tailscale is unavailable / disconnected. Best-effort with a 5-second timeout.

## v0.3.5 (2026-04-14)

### Bug Fixes
- **Connection pool exhaustion fix** — replaced `setInterval` with self-scheduling `setTimeout` for both `pollSessions` and `sendHeartbeat` loops; prevents `ERR_INSUFFICIENT_RESOURCES` death spiral when federation requests time out during 2-second poll cycles

## v0.3.4 (2026-04-13)

### Bug Fixes
- **Zero-session devices visible** — devices with no tmux sessions now show a "No sessions" status tile instead of being invisible
- **Flapping prevention** — server-side cache of last-known-good federation results per remote; returns cached sessions for up to 3 consecutive failures before marking unreachable
- **Status tiles show device name** — offline/unreachable tiles display the device name instead of blank (was passing session.name which is undefined for status entries)
- **Status entries filtered from session list** — unreachable/auth_failed entries no longer render as blank session tiles in dashboard or sidebar
- **remoteId=0 falsy bug in mobile sheet** — first remote instance (index 0) now works correctly in the mobile bottom sheet session switcher

## v0.3.3 (2026-04-13)

### Bug Fixes
- **iOS/iPadOS touch scrolling** — fix touch scroll handling for Safari on iOS and iPadOS devices (PR #4, @samueljklee)

## v0.3.2 (2026-04-09)

### Bug Fixes
- **Hidden sessions filter now applies to federated sessions** -- hiding a session now hides it everywhere (local and remote), completing the federation-aware hidden sessions feature

## v0.3.1 (2026-04-08)

### Bug Fixes
- **Federation auth stale key** -- the auth middleware now reads the federation key fresh from disk on each request instead of caching it at startup; key generation and rotation no longer require a server restart
- **Settings sync silent push failures** -- the PUT response from `/api/settings/sync` is now checked; 409 (remote newer) is handled gracefully, other errors are logged

## v0.3.0 (2026-04-08)

### Features
- **Federation settings sync** -- user preferences (font size, sort order, hidden sessions, etc.) now sync across all connected muxplex servers using a P2P last-write-wins protocol with per-server timestamps; offline servers catch up automatically on reconnect
- **Heartbeat-driven bell clearing across federation** -- viewing a remote session now clears its activity bell on the remote server automatically; no more stale activity indicators for federated sessions

### Bug Fixes
- **`remoteId: 0` falsy bug** -- sessions from the first remote instance were incorrectly subject to the hidden-sessions filter due to a JavaScript falsy-0 check; fixed `!s.remoteId` to `s.remoteId == null`
- **Browser indicators ignore hidden sessions** -- tab title `(N)` count and favicon activity badge now filter through `getVisibleSessions()` so hidden sessions don't contribute to activity counts

### API
- **`GET /api/settings/sync`** -- returns syncable settings + timestamp for federation sync (Bearer token auth)
- **`PUT /api/settings/sync`** -- accepts synced settings; applies if incoming timestamp is newer (200), rejects if older (409 with local state)

## v0.2.0 (2026-04-08)

### Features
- **Server-side settings consolidation** -- all display preferences (font size, grid columns, hover delay, view mode, device badges, hover preview, activity indicator, grid view mode, sidebar state) moved from browser localStorage to server-side `settings.json`; settings now survive browser clears and are consistent per-server
- **Federation session deletion** -- kill sessions on remote devices from any muxplex client
- **Session creation error reporting** -- replaced fire-and-forget subprocess with async process that checks exit codes, surfaces stderr, and pre-flight checks the command binary on PATH
- **TTY-attach resilience** -- session commands that exit non-zero but still create the tmux session (e.g. `amplifier-workspace` which tries to attach after create) are detected and treated as success

### Bug Fixes
- **Federation key preservation on URL edit** -- editing a remote instance URL (e.g. `http://` to `https://`) no longer erases the federation key; added position-based fallback alongside the existing URL-based key restoration
- **PWA manifest auth bypass** -- added `.json` to the static extension allowlist so `/manifest.json` is not auth-gated; previously produced "Syntax error" in the browser console
- **`auto_open` toggle** -- fixed three-way key mismatch (`auto_open` vs `auto_open_created`) that made the auto-open setting completely non-functional
- **Session enumeration crash** -- `enumerate_sessions()` now catches `FileNotFoundError` when the session command binary is missing from PATH, preventing poll loop crashes
- **Settings PATCH key leak** -- the `PATCH /api/settings` response now redacts sensitive keys, matching the existing `GET /api/settings` behavior
- **Federation 503 diagnostics** -- all federation proxy 503 errors now include the exception type and message instead of just the remote URL
- **FastAPI version string** -- corrected the hardcoded `version` in the FastAPI app from `0.1.0` to match the release

## v0.1.1 (2026-04-07)

### Features
- **TLS/HTTPS support** — `muxplex setup-tls` auto-detects Tailscale → mkcert → self-signed certificates
- **TLS nudge** in `doctor` and `service install` when clipboard requires HTTPS
- **Session device selector** — create sessions on remote devices when multi-device enabled
- **Activity count in page title** — browser tab shows `(2) hostname - muxplex` for unseen bells
- **Favicon activity badge** — amber dot overlay on favicon for unseen notifications
- **Terminal search** — Ctrl+F to search scrollback (xterm-addon-search)
- **Clickable URLs** — Ctrl+Click / Cmd+Click opens URLs in terminal output (xterm-addon-web-links)
- **Inline image rendering** — Sixel and iTerm2 graphic protocols (xterm-addon-image)

### Bug Fixes
- **Federation SSL** — federation client accepts self-signed TLS certificates on remote instances
- **Federation empty key** — skip Authorization header when federation key is empty
- **Federation WebSocket SSL** — WebSocket proxy accepts self-signed certs on wss:// remotes
- **Remote session connect** — terminal reconnect uses federation connect path for remote sessions
- **Remote session restore** — persist `active_remote_id` in state for page refresh restore
- **Bell clearing for remote sessions** — federation bell-clear endpoint + unique sessionKey
- **Service crash-loop prevention** — kill stale port holders on startup, TimeoutStopSec in systemd
- **UTF-8 terminal display** — decode WebSocket output with TextDecoder before xterm.js write
- **Clean clipboard handling** — removed custom paste handlers per COE review, native xterm.js paste
- **Guard empty session name** — openSession bails on empty name from unreachable federation tiles
- **Clean Ctrl+C exit** — `muxplex service logs` exits cleanly on keyboard interrupt

### Infrastructure
- **PyPI publish** — available as `pip install muxplex`
- **GitHub Actions CI** — tests run on push/PR (Python 3.11-3.13)
- **Self-hosted vendor libs** — eliminates Edge Tracking Prevention console noise

## v0.1.0 (2026-04-04)

Initial release.
