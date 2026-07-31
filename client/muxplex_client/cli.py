"""muxplex-client CLI -- drive a muxplex server's HTTP API without writing Python.

Thinness rule: this module contains ONLY argparse wiring, output rendering,
and exit codes. Config resolution, CAS retry, TLS remediation, endpoint
coverage -- all of that lives in the library (`sync_client.py`, `config.py`,
`_protocol.py`) so any consumer gets it for free. Every command handler is
roughly: build a client, call one library method, hand the result to
`_emit()`.

See SPEC.md for the full command surface and design rationale.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import ClientConfig
from .errors import (
    AuthError,
    CommandTimeout,
    ConfigError,
    DestructiveChange,
    InputForbidden,
    MuxplexError,
    SessionNotFound,
    SessionWaitTimeout,
    SettingsConflict,
    TlsTrustError,
    UnreachableError,
)
from .models import Bell, Session, SessionSnapshot, ViewSession
from .sync_client import MuxplexClient

# ---------------------------------------------------------------------------
# Output rendering
# ---------------------------------------------------------------------------


def _emit(payload: object, *, as_json: bool, human: Callable[[], None]) -> None:
    """Render *payload* as JSON or via *human*, chosen by *as_json*."""
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        human()


def _print_dict(payload: dict[str, Any]) -> None:
    """Print a flat dict, two-space indented, one key per line."""
    for key, value in payload.items():
        print(f"  {key}: {value}")


def _bell_dict(bell: Bell) -> dict[str, Any]:
    """Convert a Bell into a JSON-serializable dict."""
    return {
        "last_fired_at": bell.last_fired_at,
        "seen_at": bell.seen_at,
        "unseen_count": bell.unseen_count,
        "needs_attention": bell.needs_attention,
    }


def _session_dict(session: Session) -> dict[str, Any]:
    """Convert a Session into a JSON-serializable dict."""
    return {
        "name": session.name,
        "snapshot": session.snapshot,
        "bell": _bell_dict(session.bell),
        "last_activity_at": session.last_activity_at,
    }


def _view_session_dict(session: ViewSession) -> dict[str, Any]:
    """Convert a ViewSession into a JSON-serializable dict."""
    return {
        "name": session.name,
        "active": session.active,
        "needs_attention": session.needs_attention,
        "bell": _bell_dict(session.bell),
        "last_activity_at": session.last_activity_at,
    }


def _session_snapshot_dict(snapshot: SessionSnapshot) -> dict[str, Any]:
    """Convert a SessionSnapshot into a JSON-serializable dict."""
    return {
        "name": snapshot.name,
        "snapshot": snapshot.snapshot,
        "lines": snapshot.lines,
        "bell": _bell_dict(snapshot.bell),
        "last_activity_at": snapshot.last_activity_at,
    }


# ---------------------------------------------------------------------------
# Safety gates
# ---------------------------------------------------------------------------


def _confirm(prompt: str, *, yes: bool) -> bool:
    """Prompt for confirmation unless *yes*; refuse outright on non-TTY stdin."""
    if yes:
        return True
    if not sys.stdin.isatty():
        print(
            f"error: refusing to proceed without --yes on non-interactive stdin: {prompt}",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        answer = input(f"{prompt}? [y/N] ")
    except EOFError:
        answer = ""
    return answer.strip().lower() in ("y", "yes")


# ---------------------------------------------------------------------------
# Error presentation
# ---------------------------------------------------------------------------


def _print_error(exc: MuxplexError, client: MuxplexClient) -> None:
    """Print an actionable, non-empty stderr message for *exc*."""
    config = client.config
    sources = config.sources if config is not None else {}
    server_url = config.server_url if config is not None else "(unknown)"

    if isinstance(exc, TlsTrustError):
        print(f"TLS certificate verification failed: {exc}", file=sys.stderr)
        if exc.hint:
            print(exc.hint, file=sys.stderr)
    elif isinstance(exc, UnreachableError):
        print(
            f"Could not reach {server_url} "
            f"(source: {sources.get('server_url', 'unknown')}): {exc}",
            file=sys.stderr,
        )
    elif isinstance(exc, InputForbidden):
        print(
            f"Input forbidden for session {exc.name!r}: {exc.detail}", file=sys.stderr
        )
        print(
            "There is no API workaround for this fence. Add this to "
            "~/.config/muxplex/settings.json on the server (input_enabled and "
            "input_allowed_sessions are local-file-only, not PATCHable):",
            file=sys.stderr,
        )
        print(
            json.dumps(
                {"input_enabled": True, "input_allowed_sessions": [exc.name]}, indent=2
            ),
            file=sys.stderr,
        )
    elif isinstance(exc, AuthError):
        print(f"Authentication failed: {exc}", file=sys.stderr)
        print(
            f"Federation key source: {sources.get('federation_key', 'unknown')}. "
            "Check the key, or run `muxplex-client auth-mode` to see the server's auth mode.",
            file=sys.stderr,
        )
    elif isinstance(exc, SessionNotFound):
        print(f"Session {exc.name!r} not found: {exc.detail or exc}", file=sys.stderr)
        print(
            "Run `muxplex-client ls` to see available sessions. A session created "
            "moments ago may not yet be visible (~2s read cache).",
            file=sys.stderr,
        )
    elif isinstance(exc, DestructiveChange):
        print(
            f"Settings update rejected -- would destructively shrink views: {dict(exc.counts)}",
            file=sys.stderr,
        )
        print(
            "--allow-destructive overrides this, but it is a loaded gun -- only use "
            "it if you are certain this is what you want.",
            file=sys.stderr,
        )
    elif isinstance(exc, SettingsConflict):
        print(
            "Settings were changed concurrently by someone else "
            f"(settings_updated_at={exc.settings_updated_at}). Retry the command.",
            file=sys.stderr,
        )
    elif isinstance(exc, CommandTimeout):
        print(
            f"Command in session {exc.session!r} did not complete within "
            f"{exc.elapsed:.1f}s (sentinel token={exc.token!r}).",
            file=sys.stderr,
        )
        print("Last snapshot:", file=sys.stderr)
        print(exc.snapshot, file=sys.stderr)
    elif isinstance(exc, ConfigError):
        print(f"Configuration error: {exc}", file=sys.stderr)
    elif isinstance(exc, SessionWaitTimeout):
        print(
            f"Session {exc.name!r} did not appear within {exc.timeout}s. "
            "It may still be starting -- check `muxplex-client ls`.",
            file=sys.stderr,
        )
    else:
        print(f"Error: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def cmd_info(client: MuxplexClient, *, verbose: bool, as_json: bool) -> None:
    """Show instance info; --verbose also prints where each config value came from."""
    info = client.instance_info()
    config: ClientConfig | None = client.config
    sources = dict(config.sources) if config is not None else {}
    payload: dict[str, Any] = dict(info.raw)
    if verbose:
        payload["config_sources"] = sources

    def _human() -> None:
        print(f"\n{info.name} ({info.device_id})")
        print(f"  version: {info.version}")
        print(f"  federation_enabled: {info.federation_enabled}")
        print(f"  tmux_socket_dir: {info.tmux_socket_dir}")
        print(f"  bell_hook_armed: {info.bell_hook_armed}")
        if verbose:
            print("\n  config sources:")
            for field_name, source in sorted(sources.items()):
                print(f"    {field_name}: {source}")
        print()

    _emit(payload, as_json=as_json, human=_human)


def cmd_health(client: MuxplexClient, *, as_json: bool) -> None:
    """Run the unauthenticated liveness check."""
    result = client.health()
    _emit(result, as_json=as_json, human=lambda: _print_dict(result))


def cmd_auth_mode(client: MuxplexClient, *, as_json: bool) -> None:
    """Show the server's auth mode and running username."""
    result = client.auth_mode()
    _emit(result, as_json=as_json, human=lambda: _print_dict(result))


def cmd_ca(client: MuxplexClient) -> None:
    """Print the local CA certificate PEM to stdout, and nothing else."""
    pem = client.ca_certificate()
    sys.stdout.write(pem)
    if not pem.endswith("\n"):
        sys.stdout.write("\n")


def cmd_ls(client: MuxplexClient, *, sort: str | None, as_json: bool) -> None:
    """List sessions from the cheap, server-resolved view."""
    result = client.view(sort=sort)
    payload = {
        "view": result.view,
        "views": list(result.views),
        "sort": result.sort,
        "sessions": [_view_session_dict(s) for s in result.sessions],
    }

    def _human() -> None:
        if not result.sessions:
            print("  (no sessions)")
            return
        # Width the name column to the longest name so a long name can never
        # run into its status text -- ljust() alone silently produces
        # "my-long-session-nameactive" once the name exceeds the pad.
        width = max(len(s.name) for s in result.sessions)
        for s in result.sessions:
            marker = "*" if s.active else ("!" if s.needs_attention else " ")
            status = (
                "active"
                if s.active
                else ("needs attention" if s.needs_attention else "")
            )
            print(f"  {marker} {s.name.ljust(width)}  {status}".rstrip())

    _emit(payload, as_json=as_json, human=_human)


def cmd_sessions(client: MuxplexClient, *, as_json: bool) -> None:
    """List sessions including their captured pane snapshots (expensive)."""
    sessions = client.sessions()
    payload = [_session_dict(s) for s in sessions]

    def _human() -> None:
        if not sessions:
            print("  (no sessions)")
            return
        for s in sessions:
            marker = "!" if s.bell.needs_attention else " "
            print(f"\n{marker} {s.name}")
            for line in s.snapshot.splitlines():
                print(f"  {line}")

    _emit(payload, as_json=as_json, human=_human)


def cmd_show(
    client: MuxplexClient, name: str, *, lines: int | None, as_json: bool
) -> None:
    """Show one session's current pane content."""
    snapshot = client.session(name, lines=lines)
    payload = _session_snapshot_dict(snapshot)

    def _human() -> None:
        marker = "!" if snapshot.bell.needs_attention else " "
        print(f"{marker} {snapshot.name} ({snapshot.lines} lines)")
        for line in snapshot.snapshot.splitlines():
            print(f"  {line}")

    _emit(payload, as_json=as_json, human=_human)


def cmd_new(
    client: MuxplexClient,
    name: str,
    *,
    no_wait: bool,
    wait_timeout: float,
    as_json: bool,
) -> None:
    """Create a new tmux session."""
    client.create_session(name, wait=not no_wait, timeout=wait_timeout)
    payload = {"name": name, "created": True, "waited": not no_wait}
    _emit(payload, as_json=as_json, human=lambda: print(f"Created session {name!r}."))


def cmd_rm(client: MuxplexClient, name: str, *, yes: bool, as_json: bool) -> None:
    """Delete a session, prompting for confirmation unless --yes."""
    if not _confirm(f"Delete session {name!r}", yes=yes):
        print("Aborted. Nothing was deleted.")
        return
    client.delete_session(name)
    payload = {"name": name, "deleted": True}
    _emit(payload, as_json=as_json, human=lambda: print(f"Deleted session {name!r}."))


def cmd_disconnect(client: MuxplexClient, *, as_json: bool) -> None:
    """Disconnect the current ttyd session."""
    client.delete_current_session()
    _emit({"disconnected": True}, as_json=as_json, human=lambda: print("Disconnected."))


def cmd_connect(client: MuxplexClient, name: str, *, as_json: bool) -> None:
    """Connect to a session -- WARNING: this moves the human's browser view too."""
    result = client.connect(name)
    payload = {"active_session": result.active_session, "ttyd_port": result.ttyd_port}

    def _human() -> None:
        print(f"Connected: {result.active_session} (ttyd port {result.ttyd_port})")
        print("Note: this moves the browser view for anyone connected to muxplex.")

    _emit(payload, as_json=as_json, human=_human)


def cmd_send(
    client: MuxplexClient,
    name: str,
    *,
    text: str,
    keys: list[str],
    enter: bool,
    lines: int | None,
    as_json: bool,
) -> None:
    """Type into a session and always print the read-back snapshot."""
    if not text and not keys and not enter:
        print("error: provide at least one of --text/--key/--enter", file=sys.stderr)
        sys.exit(1)
    if enter and "Enter" in keys:
        print(
            "warning: --enter together with --key Enter sends two Enters",
            file=sys.stderr,
        )
    result = client.send_input(name, text=text, keys=keys, enter=enter, lines=lines)
    payload = {"session": result.session, "snapshot": result.snapshot}

    def _human() -> None:
        print(f"Sent to {result.session!r}.")
        print("--- snapshot ---")
        print(result.snapshot)

    _emit(payload, as_json=as_json, human=_human)


def cmd_run(
    client: MuxplexClient,
    name: str,
    command: str,
    *,
    timeout: float,
    lines: int,
    no_bell: bool,
    exit_expr: str,
    as_json: bool,
) -> None:
    """Run a shell command to completion; exit with the REMOTE command's exit code."""
    result = client.run_shell_command(
        name,
        command,
        timeout=timeout,
        lines=lines,
        bell_on_failure=not no_bell,
        exit_expr=exit_expr,
    )
    payload = {
        "session": result.session,
        "exit_code": result.exit_code,
        "elapsed": result.elapsed,
        "token": result.token,
        "snapshot": result.snapshot,
    }

    def _human() -> None:
        print(
            f"{command!r} in {result.session!r} exited {result.exit_code} "
            f"after {result.elapsed:.1f}s"
        )
        print("--- snapshot ---")
        print(result.snapshot)

    _emit(payload, as_json=as_json, human=_human)
    sys.exit(result.exit_code)


def cmd_bell_ring(client: MuxplexClient, name: str, *, as_json: bool) -> None:
    """Record a bell fire for a session."""
    client.ring_bell(name)
    payload = {"session": name, "bell": "rung"}
    _emit(payload, as_json=as_json, human=lambda: print(f"Bell rung for {name!r}."))


def cmd_bell_clear(client: MuxplexClient, name: str, *, as_json: bool) -> None:
    """Acknowledge (clear) a session's bell."""
    client.clear_bell(name)
    payload = {"session": name, "bell": "cleared"}
    _emit(payload, as_json=as_json, human=lambda: print(f"Bell cleared for {name!r}."))


def cmd_state(client: MuxplexClient, *, as_json: bool) -> None:
    """Show server state."""
    state = client.state()
    payload = dict(state.raw)

    def _human() -> None:
        print(f"  active_session: {state.active_session}")
        print(f"  active_view: {state.active_view}")
        print(f"  settings_updated_at: {state.settings_updated_at}")

    _emit(payload, as_json=as_json, human=_human)


def cmd_state_set(
    client: MuxplexClient,
    *,
    active_view: str | None,
    active_session: str | None,
    active_remote_id: str | None,
    clear_active_session: bool,
    as_json: bool,
) -> None:
    """Patch server state -- WARNING: these fields are server-global, last-writer-wins."""
    kwargs: dict[str, Any] = {}
    if active_view is not None:
        kwargs["active_view"] = active_view
    if clear_active_session:
        kwargs["active_session"] = None
    elif active_session is not None:
        kwargs["active_session"] = active_session
    if active_remote_id is not None:
        kwargs["active_remote_id"] = active_remote_id

    if not kwargs:
        print(
            "error: state set requires at least one of --active-view/"
            "--active-session/--active-remote-id/--clear-active-session",
            file=sys.stderr,
        )
        sys.exit(1)

    client.patch_state(**kwargs)
    _emit(kwargs, as_json=as_json, human=lambda: print(f"Updated state: {kwargs}"))


def cmd_settings(client: MuxplexClient, *, as_json: bool) -> None:
    """Show current settings."""
    settings = client.settings()
    payload = dict(settings.raw)

    def _human() -> None:
        print(f"  sort_order: {settings.sort_order}")
        print(f"  hidden_sessions: {sorted(settings.hidden_sessions)}")
        print(f"  views: {[v.name for v in settings.views]}")

    _emit(payload, as_json=as_json, human=_human)


def cmd_settings_set(
    client: MuxplexClient,
    key: str,
    raw_value: str,
    *,
    allow_destructive: bool,
    as_json: bool,
) -> None:
    """Set one top-level settings key via a safe CAS read-modify-write."""
    try:
        value: Any = json.loads(raw_value)
    except json.JSONDecodeError:
        value = raw_value

    def _mutate(current: dict[str, Any]) -> dict[str, Any]:
        return {key: value}

    result = client.apply_settings(_mutate, allow_destructive=allow_destructive)
    _emit(result, as_json=as_json, human=lambda: print(f"  {key}: {value!r}"))


def cmd_settings_sync(client: MuxplexClient, *, as_json: bool) -> None:
    """Show syncable settings and their timestamps."""
    result = client.settings_sync()
    _emit(result, as_json=as_json, human=lambda: _print_dict(result))


def cmd_settings_push(client: MuxplexClient, source: str, *, as_json: bool) -> None:
    """Push a JSON settings-sync payload read from FILE (or '-' for stdin)."""
    try:
        text = (
            sys.stdin.read()
            if source == "-"
            else Path(source).read_text(encoding="utf-8")
        )
    except OSError as exc:
        print(f"error: could not read {source}: {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"error: {source} is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(payload, dict):
        print(
            f"error: {source} must contain a JSON object, not {type(payload).__name__}",
            file=sys.stderr,
        )
        sys.exit(1)
    result = client.put_settings_sync(payload)
    _emit(result, as_json=as_json, human=lambda: _print_dict(result))


def cmd_fed_ls(client: MuxplexClient, *, as_json: bool) -> None:
    """List federation sessions -- local and remote, merged; failures arrive in-band."""
    entries = client.federation_sessions()
    payload = [dict(e.raw) for e in entries]

    def _human() -> None:
        if not entries:
            print("  (no federation entries)")
            return
        for e in entries:
            label = e.device_name or e.device_id or "?"
            if e.is_session:
                print(f"  {label}: {e.name}")
            else:
                print(f"  {label}: [{e.status}]")

    _emit(payload, as_json=as_json, human=_human)


def cmd_fed_connect(
    client: MuxplexClient, device: str, session: str, *, as_json: bool
) -> None:
    """Connect to a session on a remote device."""
    result = client.federation_connect(device, session)
    _emit(result, as_json=as_json, human=lambda: _print_dict(result))


def cmd_fed_new(
    client: MuxplexClient, device: str, name: str, *, as_json: bool
) -> None:
    """Create a session on a remote device."""
    result = client.federation_create_session(device, name)
    _emit(result, as_json=as_json, human=lambda: _print_dict(result))


def cmd_fed_rm(
    client: MuxplexClient, device: str, name: str, *, yes: bool, as_json: bool
) -> None:
    """Delete a session on a remote device, prompting for confirmation unless --yes."""
    if not _confirm(f"Delete session {name!r} on device {device!r}", yes=yes):
        print("Aborted. Nothing was deleted.")
        return
    result = client.federation_delete_session(device, name)
    _emit(result, as_json=as_json, human=lambda: _print_dict(result))


def cmd_fed_bell_clear(
    client: MuxplexClient, device: str, name: str, *, as_json: bool
) -> None:
    """Clear a bell on a remote device's session."""
    result = client.federation_clear_bell(device, name)
    _emit(result, as_json=as_json, human=lambda: _print_dict(result))


def cmd_fed_generate_key(client: MuxplexClient, *, yes: bool, as_json: bool) -> None:
    """Rotate this server's federation key -- invalidates every existing client."""
    if not _confirm(
        "Overwrite the federation key file, invalidating every existing client", yes=yes
    ):
        print("Aborted. Key was not changed.")
        return
    result = client.generate_federation_key()
    _emit(result, as_json=as_json, human=lambda: _print_dict(result))


def cmd_heartbeat(
    client: MuxplexClient, *, device_id: str | None, as_json: bool
) -> None:
    """Register or update this device's heartbeat."""
    resolved_device_id = device_id or client.instance_info().device_id
    payload = {
        "device_id": resolved_device_id,
        "label": "muxplex-client",
        "viewing_session": None,
        "view_mode": "grid",
        "last_interaction_at": time.time(),
    }
    result = client.heartbeat(payload)
    _emit(result, as_json=as_json, human=lambda: _print_dict(result))


def cmd_setup_hooks(client: MuxplexClient, *, as_json: bool) -> None:
    """Re-register tmux hooks; call after a tmux server restart."""
    result = client.setup_hooks()
    _emit(result, as_json=as_json, human=lambda: _print_dict(result))


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _build_parser() -> tuple[
    argparse.ArgumentParser, argparse.ArgumentParser, argparse.ArgumentParser
]:
    """Build the muxplex-client parser; also returns the bell/fed group parsers.

    The bell and fed group parsers are returned alongside the main parser
    (rather than only being reachable through argparse's internals) so
    `main()` can call `.print_help()` on the right one when a group is
    invoked with no recognized sub-subcommand -- `state`/`settings` don't
    need this because they have a sensible default (GET) instead.
    """
    parser = argparse.ArgumentParser(
        prog="muxplex-client",
        description=(
            "Drive a muxplex server over its HTTP API -- every feature, "
            "no server dependency."
        ),
    )
    parser.add_argument(
        "--url", default=None, help="Override server URL (env MUXPLEX_URL)"
    )
    parser.add_argument(
        "--key",
        # Explicit dest: `settings set KEY VALUE` has a positional named `key`,
        # and argparse merges subparser results into one namespace -- without
        # this, the positional shadows the global flag.
        dest="federation_key",
        default=None,
        help=(
            "Federation key literal (env MUXPLEX_KEY). Prefer --key-file: a "
            "literal here lands in your shell history."
        ),
    )
    parser.add_argument(
        "--key-file",
        default=None,
        dest="key_file",
        help="Read the federation key from a file (env MUXPLEX_FEDERATION_KEY_FILE)",
    )
    parser.add_argument(
        "--ca", default=None, help="CA certificate path (env MUXPLEX_CA_FILE)"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="HTTP timeout in seconds (env MUXPLEX_TIMEOUT)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON to stdout instead of human-readable text",
    )

    sub = parser.add_subparsers(dest="command")

    info_parser = sub.add_parser("info", help="Show instance info")
    info_parser.add_argument(
        "--verbose",
        action="store_true",
        help="Also print where each config value came from",
    )

    sub.add_parser("health", help="Unauthenticated liveness check")
    sub.add_parser("auth-mode", help="Show the server's auth mode and running username")
    sub.add_parser(
        "ca", help="Print the local CA certificate PEM to stdout, and nothing else"
    )

    ls_parser = sub.add_parser("ls", help="List sessions (cheap, server-resolved view)")
    ls_parser.add_argument(
        "--sort",
        choices=["attention"],
        default=None,
        help="Sort sessions needing attention first",
    )

    sub.add_parser(
        "sessions", help="List sessions including pane snapshots (expensive)"
    )

    show_parser = sub.add_parser("show", help="Show one session's current pane content")
    show_parser.add_argument("name", help="Session name")
    show_parser.add_argument(
        "--lines", type=int, default=None, help="Number of pane lines to capture"
    )

    new_parser = sub.add_parser(
        "new",
        help="Create a new tmux session",
        description=(
            "Create a new tmux session. Two different timeouts are involved "
            "and are NOT the same thing: the global --timeout is the HTTP "
            "request timeout, but this command's own POST always gets a "
            "longer floor regardless of --timeout (currently 30s -- see "
            "muxplex_client.constants.SUBPROCESS_TIMEOUT) since the server "
            "runs the operator's new_session_template synchronously before "
            "responding, and that routinely takes longer than an ordinary "
            "read. --wait-timeout below is a separate, later step: once the "
            "POST has already succeeded, how long to poll the ~2s read "
            "cache waiting for the new session to become visible."
        ),
    )
    new_parser.add_argument("name", help="Session name")
    new_parser.add_argument(
        "--no-wait",
        action="store_true",
        dest="no_wait",
        help="Don't wait for the session to appear in the read cache",
    )
    new_parser.add_argument(
        "--wait-timeout",
        type=float,
        default=6.0,
        dest="wait_timeout",
        help=(
            "Seconds to poll the read cache for the session to appear AFTER "
            "creation succeeds (default: 6.0). This is the wait-for-"
            "visibility ceiling, not the HTTP request timeout -- see this "
            "command's --help description for the distinction"
        ),
    )

    rm_parser = sub.add_parser("rm", help="Delete a session")
    rm_parser.add_argument("name", help="Session name")
    rm_parser.add_argument(
        "--yes", action="store_true", help="Skip the confirmation prompt"
    )

    sub.add_parser("disconnect", help="Disconnect the current ttyd session")

    connect_parser = sub.add_parser(
        "connect",
        help="Connect to a session -- WARNING: moves the human's browser view too",
    )
    connect_parser.add_argument("name", help="Session name")

    send_parser = sub.add_parser(
        "send", help="Type into a session; always prints the read-back snapshot"
    )
    send_parser.add_argument("name", help="Session name")
    send_parser.add_argument("--text", default="", help="Literal text to type")
    send_parser.add_argument(
        "--key",
        action="append",
        dest="keys",
        default=[],
        metavar="K",
        help=(
            "A named key to send (repeatable): Enter, Escape, Tab, C-c, C-d, "
            "Up, Down, Left, Right, PageUp, PageDown"
        ),
    )
    send_parser.add_argument(
        "--enter", action="store_true", help="Send Enter after text/keys"
    )
    send_parser.add_argument(
        "--lines", type=int, default=None, help="Number of pane lines in the read-back"
    )

    run_parser = sub.add_parser(
        "run",
        help="Run a shell command to completion; exits with the REMOTE command's exit code",
    )
    run_parser.add_argument(
        "name", help="Session name (must be an idle POSIX shell prompt)"
    )
    run_parser.add_argument(
        "shell_command",
        metavar="COMMAND",
        help="Shell command to run -- quote it if it contains spaces",
    )
    run_parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        dest="run_timeout",
        metavar="SECONDS",
        help="Seconds to wait for completion (default: 600.0)",
    )
    run_parser.add_argument(
        "--lines", type=int, default=500, help="Pane lines to poll (default: 500)"
    )
    run_parser.add_argument(
        "--no-bell",
        action="store_true",
        dest="no_bell",
        help="Don't ring the bell on a non-zero exit code",
    )
    run_parser.add_argument(
        "--exit-expr",
        default="$?",
        dest="exit_expr",
        help="Shell expression for the exit code (default: $?)",
    )

    bell_parser = sub.add_parser("bell", help="Ring or clear a session's bell")
    bell_sub = bell_parser.add_subparsers(dest="bell_command")
    bell_ring_parser = bell_sub.add_parser(
        "ring", help="Record a bell fire for a session"
    )
    bell_ring_parser.add_argument("name", help="Session name")
    bell_clear_parser = bell_sub.add_parser(
        "clear", help="Acknowledge a session's bell"
    )
    bell_clear_parser.add_argument("name", help="Session name")

    state_parser = sub.add_parser("state", help="Show server state")
    state_sub = state_parser.add_subparsers(dest="state_command")
    state_set_parser = state_sub.add_parser(
        "set", help="Patch server state -- WARNING: server-global, last-writer-wins"
    )
    state_set_parser.add_argument(
        "--active-view", default=None, dest="active_view", help="New active view name"
    )
    state_set_active_session_group = state_set_parser.add_mutually_exclusive_group()
    state_set_active_session_group.add_argument(
        "--active-session",
        default=None,
        dest="active_session",
        help="New active session name",
    )
    state_set_active_session_group.add_argument(
        "--clear-active-session",
        action="store_true",
        dest="clear_active_session",
        help="Clear the active session (set it to null)",
    )
    state_set_parser.add_argument(
        "--active-remote-id",
        default=None,
        dest="active_remote_id",
        help="New active remote device id",
    )

    settings_parser = sub.add_parser("settings", help="Show current settings")
    settings_sub = settings_parser.add_subparsers(dest="settings_command")
    settings_set_parser = settings_sub.add_parser(
        "set", help="Set one top-level settings key (safe CAS read-modify-write)"
    )
    settings_set_parser.add_argument("key", help="Top-level settings key")
    settings_set_parser.add_argument(
        "value", help="New value, parsed as JSON (falls back to a bare string)"
    )
    settings_set_parser.add_argument(
        "--allow-destructive",
        action="store_true",
        dest="allow_destructive",
        help="Allow a write that would catastrophically shrink views -- a loaded gun",
    )
    settings_sub.add_parser("sync", help="Show syncable settings and their timestamps")
    settings_push_parser = settings_sub.add_parser(
        "push", help="Push a JSON settings-sync payload"
    )
    settings_push_parser.add_argument(
        "source", metavar="FILE", help="JSON file to push, or '-' for stdin"
    )

    fed_parser = sub.add_parser(
        "fed", help="Federation: sessions on other muxplex instances"
    )
    fed_sub = fed_parser.add_subparsers(dest="fed_command")
    fed_sub.add_parser("ls", help="List federation sessions")
    fed_connect_parser = fed_sub.add_parser(
        "connect", help="Connect to a session on a remote device"
    )
    fed_connect_parser.add_argument("device", help="Remote device id")
    fed_connect_parser.add_argument("session", help="Remote session name")
    fed_new_parser = fed_sub.add_parser(
        "new", help="Create a session on a remote device"
    )
    fed_new_parser.add_argument("device", help="Remote device id")
    fed_new_parser.add_argument("name", help="New session name")
    fed_rm_parser = fed_sub.add_parser("rm", help="Delete a session on a remote device")
    fed_rm_parser.add_argument("device", help="Remote device id")
    fed_rm_parser.add_argument("name", help="Session name")
    fed_rm_parser.add_argument(
        "--yes", action="store_true", help="Skip the confirmation prompt"
    )
    fed_bell_clear_parser = fed_sub.add_parser(
        "bell-clear", help="Clear a bell on a remote device's session"
    )
    fed_bell_clear_parser.add_argument("device", help="Remote device id")
    fed_bell_clear_parser.add_argument("name", help="Session name")
    fed_generate_key_parser = fed_sub.add_parser(
        "generate-key",
        help="Rotate this server's federation key -- invalidates every existing client",
    )
    fed_generate_key_parser.add_argument(
        "--yes", action="store_true", help="Skip the confirmation prompt"
    )

    heartbeat_parser = sub.add_parser(
        "heartbeat", help="Register or update this device's heartbeat"
    )
    heartbeat_parser.add_argument(
        "--device-id",
        default=None,
        dest="device_id",
        help="Device id (default: this instance's own, from GET /api/instance-info)",
    )

    sub.add_parser(
        "setup-hooks", help="Re-register tmux hooks (call after a tmux server restart)"
    )

    return parser, bell_parser, fed_parser


def _build_client(args: argparse.Namespace) -> MuxplexClient:
    """Build a MuxplexClient from resolved config and CLI overrides."""
    return MuxplexClient.from_env(
        server_url=args.url,
        federation_key=args.federation_key,
        key_file=args.key_file,
        ca_file=args.ca,
        timeout=args.timeout,
    )


def main() -> None:
    """CLI entry point."""
    parser, bell_parser, fed_parser = _build_parser()
    args = parser.parse_args()

    if not getattr(args, "command", None):
        parser.print_help()
        return

    if args.federation_key:
        print(
            "warning: --key on the command line lands in your shell history; "
            "consider --key-file instead",
            file=sys.stderr,
        )

    try:
        client = _build_client(args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    as_json = args.json
    try:
        if args.command == "info":
            cmd_info(client, verbose=args.verbose, as_json=as_json)
        elif args.command == "health":
            cmd_health(client, as_json=as_json)
        elif args.command == "auth-mode":
            cmd_auth_mode(client, as_json=as_json)
        elif args.command == "ca":
            cmd_ca(client)
        elif args.command == "ls":
            cmd_ls(client, sort=args.sort, as_json=as_json)
        elif args.command == "sessions":
            cmd_sessions(client, as_json=as_json)
        elif args.command == "show":
            cmd_show(client, args.name, lines=args.lines, as_json=as_json)
        elif args.command == "new":
            cmd_new(
                client,
                args.name,
                no_wait=args.no_wait,
                wait_timeout=args.wait_timeout,
                as_json=as_json,
            )
        elif args.command == "rm":
            cmd_rm(client, args.name, yes=args.yes, as_json=as_json)
        elif args.command == "disconnect":
            cmd_disconnect(client, as_json=as_json)
        elif args.command == "connect":
            cmd_connect(client, args.name, as_json=as_json)
        elif args.command == "send":
            cmd_send(
                client,
                args.name,
                text=args.text,
                keys=args.keys,
                enter=args.enter,
                lines=args.lines,
                as_json=as_json,
            )
        elif args.command == "run":
            cmd_run(
                client,
                args.name,
                args.shell_command,
                timeout=args.run_timeout,
                lines=args.lines,
                no_bell=args.no_bell,
                exit_expr=args.exit_expr,
                as_json=as_json,
            )
        elif args.command == "bell":
            bell_command = getattr(args, "bell_command", None)
            if bell_command == "ring":
                cmd_bell_ring(client, args.name, as_json=as_json)
            elif bell_command == "clear":
                cmd_bell_clear(client, args.name, as_json=as_json)
            else:
                bell_parser.print_help()
        elif args.command == "state":
            state_command = getattr(args, "state_command", None)
            if state_command == "set":
                cmd_state_set(
                    client,
                    active_view=args.active_view,
                    active_session=args.active_session,
                    active_remote_id=args.active_remote_id,
                    clear_active_session=args.clear_active_session,
                    as_json=as_json,
                )
            else:
                cmd_state(client, as_json=as_json)
        elif args.command == "settings":
            settings_command = getattr(args, "settings_command", None)
            if settings_command == "set":
                cmd_settings_set(
                    client,
                    args.key,
                    args.value,
                    allow_destructive=args.allow_destructive,
                    as_json=as_json,
                )
            elif settings_command == "sync":
                cmd_settings_sync(client, as_json=as_json)
            elif settings_command == "push":
                cmd_settings_push(client, args.source, as_json=as_json)
            else:
                cmd_settings(client, as_json=as_json)
        elif args.command == "fed":
            fed_command = getattr(args, "fed_command", None)
            if fed_command == "ls":
                cmd_fed_ls(client, as_json=as_json)
            elif fed_command == "connect":
                cmd_fed_connect(client, args.device, args.session, as_json=as_json)
            elif fed_command == "new":
                cmd_fed_new(client, args.device, args.name, as_json=as_json)
            elif fed_command == "rm":
                cmd_fed_rm(
                    client, args.device, args.name, yes=args.yes, as_json=as_json
                )
            elif fed_command == "bell-clear":
                cmd_fed_bell_clear(client, args.device, args.name, as_json=as_json)
            elif fed_command == "generate-key":
                cmd_fed_generate_key(client, yes=args.yes, as_json=as_json)
            else:
                fed_parser.print_help()
        elif args.command == "heartbeat":
            cmd_heartbeat(client, device_id=args.device_id, as_json=as_json)
        elif args.command == "setup-hooks":
            cmd_setup_hooks(client, as_json=as_json)
    except MuxplexError as exc:
        _print_error(exc, client)
        sys.exit(1)
    finally:
        client.close()
