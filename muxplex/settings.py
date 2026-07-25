"""
Server-side settings management for muxplex.

Settings are stored at ~/.config/muxplex/settings.json.
"""

import copy
import json
import logging
import os
import socket
import threading
import time
from pathlib import Path

_log = logging.getLogger(__name__)

SETTINGS_PATH = Path.home() / ".config" / "muxplex" / "settings.json"
FEDERATION_KEY_PATH = Path.home() / ".config" / "muxplex" / "federation_key"

# Settings schema version. Incremented when settings semantics change.
#
# v1 (implicit, missing field): legacy. The mutual-exclusion invariant between
#     hidden_sessions and view.sessions is enforced at write time. Federation
#     peers without this field are assumed to be v1.
# v2: hidden_sessions and view.sessions are allowed to overlap; visibility is
#     determined by a read-time filter. In practice the v1 backstop
#     enforce_mutual_exclusion still runs in v2 — see
#     docs/plans/2026-05-17-hidden-state-redesign-design.md for the deferral
#     of its removal (Phase 3).
SCHEMA_VERSION: int = 2

DEFAULT_SETTINGS: dict = {
    "host": "127.0.0.1",
    "port": 8088,
    "auth": "pam",
    "session_ttl": 604800,
    "default_session": None,
    "sort_order": "manual",
    "hidden_sessions": [],
    "views": [],
    "window_size_largest": False,
    "auto_open_created": True,
    "new_session_template": "tmux new-session -d -s {name}",
    "remote_instances": [],
    "device_name": "",
    "delete_session_template": "tmux kill-session -t {name}",
    # Explicit override for tmux's socket directory (maps to the TMUX_TMPDIR
    # env var). Empty string = inherit whatever TMUX_TMPDIR (if any) is in
    # the muxplex process's own environment. Needed because a systemd/launchd
    # service does NOT inherit the interactive login shell's environment --
    # if a user sets TMUX_TMPDIR in their shell rc (e.g. to keep sockets out
    # of the shared, world-writable /tmp), the muxplex *service* process
    # never sees it and falls back to tmux's compiled-in default
    # (/tmp/tmux-$UID), silently missing every session the user actually has.
    "tmux_socket_dir": "",
    "multi_device_enabled": False,
    # Terminal input over the API (POST /api/sessions/{name}/input).
    # SECURITY: this is remote-code-execution by design -- an agent typing
    # into a shell pane runs whatever it types. Both fences default CLOSED:
    #   input_enabled          -- global opt-in; False means the endpoint is
    #                             a hard 403 regardless of any other config.
    #   input_allowed_sessions -- GLOB PATTERNS, matched case-INSENSITIVELY
    #                             (casefold() + fnmatch.fnmatchcase -- see
    #                             terminal_input.session_matches_allowlist)
    #                             naming sessions input may target, e.g. "*"
    #                             for all, "amplifier-*" for a prefix family
    #                             (case-insensitive), or an exact name (which
    #                             matches only itself, also case-insensitive).
    #                             A session matching none of these is a 403
    #                             even when input_enabled is True. Keeping a
    #                             human's own working panes off every pattern
    #                             is how they stay un-typeable.
    # Deliberately NOT in SYNCABLE_KEYS: a security fence must never be
    # widened by a federation peer's settings sync.
    "input_enabled": False,
    "input_allowed_sessions": [],
    "federation_key": "",
    "tls_cert": "",
    "tls_key": "",
    "fontSize": 14,
    "hoverPreviewDelay": 1500,
    "gridColumns": "auto",
    "bellSound": False,
    "viewMode": "auto",
    "showDeviceBadges": True,
    "showHoverPreview": True,
    "activityIndicator": "both",
    "gridViewMode": "flat",
    "sidebarOpen": None,
    "settings_updated_at": 0.0,
    # Timestamp of the last change to `views` or `hidden_sessions` SPECIFICALLY.
    # Metadata, like settings_updated_at -- not itself a "setting" a client
    # would ever want to overwrite -- so it lives outside SYNCABLE_KEYS and is
    # threaded through the federation sync payload the same way
    # settings_updated_at is (see get_syncable_settings/apply_synced_settings).
    #
    # WHY THIS EXISTS: settings_updated_at covers the ENTIRE syncable blob, so
    # an unrelated PATCH (e.g. fontSize) bumps the same timestamp a views edit
    # would. In a federation LWW race, that let a peer's stale `views` win
    # over a genuinely newer edit just because ITS settings_updated_at had
    # been bumped more recently by something else entirely. views_updated_at
    # is scoped to exactly the fields it needs to arbitrate.
    #
    # BACKWARD COMPATIBILITY: a peer that doesn't know about this field sends
    # nothing for it; apply_synced_settings() treats that as "no signal" and
    # falls back to applying views/hidden_sessions unconditionally (gated
    # only by the destructive-write backstop, never by a per-field
    # timestamp), so old (pre-this-field) peers keep interoperating exactly
    # as before.
    "views_updated_at": 0.0,
    "_schema_version": SCHEMA_VERSION,
    # Grace period (hours) before a session key missing from all live sessions
    # is removed from views/hidden_sessions. Syncable so the operator can tune
    # it federation-wide; the per-device first-missed-at bookkeeping that drives
    # the actual prune is local-only (pruning.json, never synced).
    "stale_key_grace_hours": 24.0,
}

# Keys that can ONLY be changed by editing the settings file on disk
# (~/.config/muxplex/settings.json) -- never via any API path.
#
# SECURITY: PATCH /api/settings sits behind the same shared auth as the rest
# of the API, and the federation Bearer key satisfies it -- the SAME
# credential handed to remote agents that call the terminal-input endpoint.
# If these fence keys were PATCHable, a Bearer-key holder could self-authorize
# typing into any session (including the human's own panes), defeating the
# per-session allowlist entirely. Requiring a local file edit makes widening
# the fence a deliberate local-operator action. These keys are also
# deliberately NOT in SYNCABLE_KEYS (federation sync must never widen them).
LOCAL_ONLY_KEYS: frozenset[str] = frozenset(
    {
        "input_enabled",
        "input_allowed_sessions",
    }
)

SYNCABLE_KEYS: frozenset[str] = frozenset(
    {
        # Display preferences
        "fontSize",
        "hoverPreviewDelay",
        "gridColumns",
        "bellSound",
        "viewMode",
        "showDeviceBadges",
        "showHoverPreview",
        "activityIndicator",
        "gridViewMode",
        "sidebarOpen",
        # Session behavior
        "sort_order",
        "hidden_sessions",
        "views",
        "default_session",
        "window_size_largest",
        "auto_open_created",
        # Schema version — sent so peers can detect our version, but never
        # accepted from the wire (see apply_synced_settings).
        "_schema_version",
        # Pruning grace period — synced so the operator can tune it
        # federation-wide. The per-device bookkeeping (first-missed-at
        # timestamps) is NOT synced; it lives in pruning.json locally.
        "stale_key_grace_hours",
    }
)


def load_settings() -> dict:
    """Load settings from disk, merging saved values over defaults.

    Returns DEFAULT_SETTINGS if the file does not exist or contains corrupt JSON.
    Unknown keys in the file are ignored.
    """
    result = copy.deepcopy(DEFAULT_SETTINGS)
    try:
        text = SETTINGS_PATH.read_text()
        data = json.loads(text)
        for key in DEFAULT_SETTINGS:
            if key in data:
                result[key] = data[key]
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    if not result["device_name"]:
        result["device_name"] = socket.gethostname()
    return result


# Rotating snapshot safety net for settings.json -- see _snapshot_current_settings().
#
# Incident that motivated this: PATCH /api/settings replaces whole values
# (e.g. the entire `views` array). A browser tab holding a STALE in-memory
# copy of `views` PATCHed it back wholesale and destroyed 7 of 8 views in
# one request. Recovery only worked because a manual file backup happened
# to exist. This makes that recovery path automatic and always-on,
# regardless of which writer (API, federation sync, internal code) is
# responsible.
SETTINGS_HISTORY_DIRNAME = "settings-history"
SETTINGS_HISTORY_KEEP = 20

# Monotonic tie-breaker for snapshot filenames. time.time()'s resolution is
# platform-dependent and, empirically, coarse enough on some hosts that two
# snapshots taken microseconds apart (e.g. in a tight test loop, or two rapid
# API writes) can produce the IDENTICAL formatted timestamp -- silently
# overwriting one snapshot with another instead of retaining both. An
# incrementing counter appended to the filename guarantees uniqueness and a
# stable chronological sort regardless of clock resolution.
_snapshot_counter_lock = threading.Lock()
_snapshot_counter = 0


def _settings_history_dir() -> Path:
    return SETTINGS_PATH.parent / SETTINGS_HISTORY_DIRNAME


def _next_snapshot_seq() -> int:
    global _snapshot_counter
    with _snapshot_counter_lock:
        _snapshot_counter += 1
        return _snapshot_counter


def _snapshot_current_settings() -> None:
    """Copy the CURRENT on-disk settings.json into settings-history/ before
    it gets overwritten by a new write.

    Best-effort and non-blocking: this is a safety net, not a transactional
    requirement. Any failure (permissions, disk full, race) is logged as a
    warning and swallowed -- it must NEVER prevent or corrupt the real write
    that triggered it. No-op if settings.json doesn't exist yet (first run,
    nothing to snapshot).

    The history directory is created lazily with mode 0700 (settings can
    contain secrets -- federation keys, TLS material -- so history copies
    need the same protection as the live file).
    """
    if not SETTINGS_PATH.exists():
        return
    try:
        history_dir = _settings_history_dir()
        history_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(history_dir, 0o700)
        except OSError:
            pass  # best-effort permission tightening; don't fail the snapshot over it
        # High-resolution timestamp for human readability, PLUS a zero-padded
        # monotonic sequence number for guaranteed uniqueness and correct
        # chronological sort even when time.time() ties across rapid calls
        # (see _next_snapshot_seq's docstring-adjacent comment above).
        seq = _next_snapshot_seq()
        snapshot_path = history_dir / f"settings-{seq:012d}-{time.time():.6f}.json"
        snapshot_path.write_text(SETTINGS_PATH.read_text())
        _prune_settings_history(history_dir)
    except Exception:
        _log.warning("settings: failed to write history snapshot", exc_info=True)


def _prune_settings_history(history_dir: Path) -> None:
    """Keep only the SETTINGS_HISTORY_KEEP most recent snapshot files.

    Filenames sort lexicographically in chronological order (fixed-width
    fractional-second formatting), so a plain sorted() is sufficient --
    no need to parse timestamps back out of the filename.
    """
    try:
        snapshots = sorted(history_dir.glob("settings-*.json"))
        excess = len(snapshots) - SETTINGS_HISTORY_KEEP
        # Guard against negative `excess`: a bare `snapshots[:excess]` with a
        # negative excess is NOT "nothing to remove" -- Python slice
        # semantics treat a negative stop as counting from the end (e.g.
        # `snapshots[:-1]` = "all but the last one"), which would delete
        # perfectly recent snapshots well before the KEEP threshold is even
        # reached. Only prune when there is a genuine surplus.
        if excess > 0:
            for old in snapshots[:excess]:
                old.unlink(missing_ok=True)
    except Exception:
        _log.warning("settings: failed to prune history snapshots", exc_info=True)


def save_settings(data: dict) -> None:
    """Save settings to disk, merging *data* with defaults first.

    Creates parent directories as needed. Writes JSON with indent=2 and a
    trailing newline.

    The `_schema_version` field is always written as the current
    SCHEMA_VERSION regardless of *data*. Clients do not get to write older
    versions — that would defeat the version field's purpose as a marker for
    federated peers.

    Before writing, snapshots whatever is CURRENTLY on disk to
    settings-history/ (see _snapshot_current_settings()) -- this is the
    single lowest choke point where the settings file is actually written,
    so every caller (API PATCH, federation sync, internal code) gets the
    safety net for free.
    """
    merged = copy.deepcopy(DEFAULT_SETTINGS)
    for key in DEFAULT_SETTINGS:
        if key in data:
            merged[key] = data[key]
    merged["_schema_version"] = SCHEMA_VERSION
    _snapshot_current_settings()
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(merged, indent=2) + "\n")


class DestructiveSettingsWriteRejected(Exception):
    """Raised by patch_settings()/apply_synced_settings() when a write would
    catastrophically shrink `views` (see views.assess_views_destruction) and
    the caller is not permitted to override the backstop.

    No write has been made when this is raised -- the check runs before any
    mutation of the in-memory settings dict, let alone save_settings().

    Attributes:
        reason: human-readable explanation, safe to log or return in an API
            error body (e.g. "views would collapse from 8 to 1 ...").
        counts: dict with before_views/after_views/before_members/after_members
            (see views.ViewsDestructionAssessment.as_counts_dict()).
    """

    def __init__(self, reason: str, counts: dict):
        self.reason = reason
        self.counts = counts
        super().__init__(reason)


def patch_settings(patch: dict, *, allow_destructive: bool = False) -> dict:
    """Merge known keys from *patch* into the current settings, save, and return result.

    Unknown keys in *patch* are silently ignored.

    Key-preservation rule: when ``remote_instances`` is included in the patch,
    any remote whose ``key`` field is empty or absent in the patch retains its
    existing key from the on-disk settings.  This prevents the redaction-wipe
    bug where ``GET /api/settings`` returns ``key=""`` for security reasons and
    a subsequent PATCH would silently overwrite real keys with empty strings.
    Only a patch that supplies a *non-empty* key value is treated as an
    intentional key rotation and actually written to disk.

    Destructive-write backstop: when *patch* contains ``views``, the incoming
    value is assessed via ``views.assess_views_destruction`` against the
    CURRENT on-disk ``views`` before anything else happens. A catastrophic
    shrinkage (see that function's docstring for the exact thresholds) raises
    ``DestructiveSettingsWriteRejected`` and makes NO write at all -- not even
    to unrelated keys in the same patch. Pass ``allow_destructive=True`` to
    perform an intentional bulk deletion anyway (the API surfaces this as an
    ``allow_destructive: true`` field in the PATCH body; see main.py's
    ``update_settings()``). A patch that omits ``views`` entirely, or whose
    ``views`` value isn't a list (None, a stray string, etc.), never triggers
    this check -- see assess_views_destruction's docstring for why that's
    always treated as "not changing views."
    """
    current = load_settings()

    if "views" in patch:
        # Lazy import: avoids potential circular import between settings and views.
        from muxplex.views import assess_views_destruction

        assessment = assess_views_destruction(current.get("views"), patch["views"])
        if assessment.destructive and not allow_destructive:
            _log.warning(
                "settings: rejected destructive views write (PATCH): %s "
                "(before=%d views/%d members, after=%d views/%d members)",
                assessment.reason,
                assessment.before_views,
                assessment.before_members,
                assessment.after_views,
                assessment.after_members,
            )
            raise DestructiveSettingsWriteRejected(
                assessment.reason, assessment.as_counts_dict()
            )

    # Snapshot existing remote keys by URL *and* by position *before* applying
    # the patch so we can restore them if the patch contains redacted (empty)
    # key values.  The URL-based lookup handles the common case (name-only edits).
    # The position-based fallback handles URL edits (e.g. http -> https) where
    # the URL changes but the remote identity is the same.
    existing_remotes = current.get("remote_instances", [])
    existing_remote_keys_by_url: dict[str, str] = {
        r["url"]: r.get("key", "") for r in existing_remotes if r.get("url")
    }
    existing_remote_keys_by_index: list[str] = [
        r.get("key", "") for r in existing_remotes
    ]

    for key in DEFAULT_SETTINGS:
        if key in patch:
            if key in LOCAL_ONLY_KEYS:
                # Security fence: these keys can only be widened by editing
                # settings.json on disk (a local-operator action) -- never
                # via the API, whose Bearer key is held by the same remote
                # agents the fence is meant to contain. Skip the key but
                # apply the rest of the patch (don't fail the whole request).
                _log.warning(
                    "settings: %r is local-only (edit settings.json directly); "
                    "ignoring value in PATCH",
                    key,
                )
                continue
            current[key] = patch[key]

    # Restore keys that were stripped by redaction.
    if "remote_instances" in patch:
        for i, remote in enumerate(current["remote_instances"]):
            if remote.get("key"):
                # Non-empty key in the patch = intentional key rotation, keep it.
                continue
            url = remote.get("url", "")
            if url in existing_remote_keys_by_url:
                # URL unchanged -- restore by exact URL match.
                remote["key"] = existing_remote_keys_by_url[url]
            elif (
                i < len(existing_remote_keys_by_index)
                and existing_remote_keys_by_index[i]
            ):
                # URL changed (e.g. http -> https) but position is the same --
                # restore by index so editing a URL doesn't erase the key.
                remote["key"] = existing_remote_keys_by_index[i]

    if any(key in SYNCABLE_KEYS for key in patch if key in DEFAULT_SETTINGS):
        current["settings_updated_at"] = time.time()

    # views_updated_at is scoped to exactly the fields it arbitrates -- see
    # DEFAULT_SETTINGS's comment for why this is separate from
    # settings_updated_at above. Bumped whenever the patch touches either
    # key, regardless of whether the new value differs from the old one
    # (matching the presence-based semantics of the settings_updated_at bump
    # just above, for consistency).
    if "views" in patch or "hidden_sessions" in patch:
        current["views_updated_at"] = time.time()

    save_settings(current)
    return current


def apply_synced_settings(
    incoming_settings: dict,
    incoming_timestamp: float,
    incoming_views_updated_at: float | None = None,
) -> dict:
    """Apply synced settings from a remote server.

    Only applies keys that are in SYNCABLE_KEYS. Sets settings_updated_at
    to the incoming timestamp (NOT time.time()) to prevent sync loops.

    `_schema_version` is intentionally **never** accepted from the wire.
    Each device speaks for its own schema version; receiving a peer's version
    must not downgrade ours. The peer's version is observable on the incoming
    payload (use `peer_supports_v2()`) before this function applies anything.

    After applying synced keys, enforces the mutual exclusion invariant:
    any session key that appears in both hidden_sessions and a view's sessions
    is removed from hidden_sessions (visibility wins over hiding).

    Destructive-write backstop: whenever `views` or `hidden_sessions` is about
    to be applied, the incoming `views` is assessed via
    `views.assess_views_destruction` against the CURRENT on-disk `views`. A
    catastrophic shrinkage raises `DestructiveSettingsWriteRejected` and makes
    NO write at all. Unlike patch_settings(), there is no override here --
    federation sync NEVER gets `allow_destructive`. Rationale: a peer must
    never be able to force a destructive change onto another device just by
    sending the right flag; only a local operator editing settings.json
    directly can perform an intentional bulk deletion that a peer disagrees
    with.

    views-specific conflict resolution (`views_updated_at`): `views` and
    `hidden_sessions` are gated by `incoming_views_updated_at`, NOT by the
    overall `incoming_timestamp`/settings_updated_at LWW comparison the
    caller used to decide whether to invoke this function at all. This
    closes the race where an unrelated field (e.g. fontSize) bumped a peer's
    settings_updated_at more recently than a genuine views edit, letting the
    peer's stale views win a sync purely because the BLOB looked newer. If
    `incoming_views_updated_at` is strictly newer than our local
    `views_updated_at`, the incoming views/hidden_sessions are applied
    (subject to the backstop above); otherwise they are left untouched and
    every OTHER present key is still applied normally (the overall sync is
    not all-or-nothing).

    Backward compatibility: `incoming_views_updated_at=None` (the default)
    means the peer doesn't know about this field -- e.g. a pre-this-feature
    muxplex instance. In that case views/hidden_sessions are applied
    unconditionally (gated only by the backstop, never by a per-field
    timestamp), which is exactly the pre-existing behavior, so older peers
    keep interoperating without any change on their end.
    """
    # Lazy import: avoids potential circular import between settings and views
    from muxplex.views import assess_views_destruction, enforce_mutual_exclusion

    current = load_settings()

    views_keys_present = (
        "views" in incoming_settings or "hidden_sessions" in incoming_settings
    )
    local_views_updated_at = current.get("views_updated_at", 0.0)
    apply_views_fields = (
        incoming_views_updated_at is None
        or incoming_views_updated_at > local_views_updated_at
    )

    if views_keys_present and apply_views_fields:
        assessment = assess_views_destruction(
            current.get("views"), incoming_settings.get("views")
        )
        if assessment.destructive:
            _log.warning(
                "settings: rejected destructive views write (federation sync): %s "
                "(before=%d views/%d members, after=%d views/%d members)",
                assessment.reason,
                assessment.before_views,
                assessment.before_members,
                assessment.after_views,
                assessment.after_members,
            )
            raise DestructiveSettingsWriteRejected(
                assessment.reason, assessment.as_counts_dict()
            )

    for key in SYNCABLE_KEYS:
        if key == "_schema_version":
            # Never downgrade local schema version from sync.
            continue
        if key not in incoming_settings:
            continue
        if key in ("views", "hidden_sessions") and not apply_views_fields:
            # Incoming views-related data is stale by views_updated_at --
            # keep ours, but still apply every other syncable key below.
            continue
        current[key] = incoming_settings[key]

    if views_keys_present and apply_views_fields:
        current["views_updated_at"] = (
            incoming_views_updated_at
            if incoming_views_updated_at is not None
            else incoming_timestamp
        )

    enforce_mutual_exclusion(current)
    current["settings_updated_at"] = incoming_timestamp
    save_settings(current)
    return current


def peer_supports_v2(peer_settings: dict) -> bool:
    """Return True if a peer's settings payload indicates schema version >= 2.

    Legacy peers omit `_schema_version` (or send a lower value) and are
    treated as v1. v1 peers enforce the mutual-exclusion invariant between
    hidden_sessions and view.sessions; v2 peers tolerate overlap.

    Used during federation handshake to decide whether outgoing settings
    need to be pre-flattened for legacy compatibility.
    """
    try:
        return int(peer_settings.get("_schema_version", 0)) >= 2
    except (TypeError, ValueError):
        return False


def get_syncable_settings() -> dict:
    """Return only syncable settings + metadata timestamps.

    `settings_updated_at` and `views_updated_at` are metadata, not
    themselves syncable "settings" -- they're timestamps used by the
    receiving peer to arbitrate conflicts (see apply_synced_settings), which
    is why they're merged in here explicitly rather than living in
    SYNCABLE_KEYS. A peer that doesn't understand `views_updated_at` simply
    ignores the extra field (additive wire change; see AGENTS.md).
    """
    settings = load_settings()
    result = {key: settings[key] for key in SYNCABLE_KEYS if key in settings}
    result["settings_updated_at"] = settings.get("settings_updated_at", 0.0)
    result["views_updated_at"] = settings.get("views_updated_at", 0.0)
    return result


def resolve_tmux_socket_dir() -> str:
    """Best-effort resolution of the tmux socket directory sessions actually
    live under, for THIS process's environment.

    Mirrors the precedence in sessions.tmux_env(): an explicit
    `tmux_socket_dir` setting is authoritative. If unset, a process just
    inherits `TMUX_TMPDIR` from its own environment -- which, for a
    systemd/launchd *service* process, is typically NOT set (services don't
    inherit the interactive login shell's rc-file exports). Absent both,
    fall back to tmux's own compiled-in default (`/tmp/tmux-<uid>`) so
    callers always get an actionable, non-empty path instead of "".

    Used by GET /api/instance-info (where this runs inside the live server
    process, so os.environ is that process's actual environment -- exact)
    and by the `muxplex env` CLI command (a separate process; see that
    command's docstring for why its resolution is a best-effort inference
    rather than a guarantee).
    """
    configured = load_settings().get("tmux_socket_dir", "")
    if configured:
        return configured
    env_value = os.environ.get("TMUX_TMPDIR", "")
    if env_value:
        return env_value
    return f"/tmp/tmux-{os.getuid()}"


def load_federation_key() -> str:
    """Load the federation key from disk or env-overridden path.

    Reads from FEDERATION_KEY_PATH by default; override via
    MUXPLEX_FEDERATION_KEY_FILE env var. Returns empty string when
    the file does not exist.
    """
    env_path = os.environ.get("MUXPLEX_FEDERATION_KEY_FILE")
    path = Path(env_path) if env_path else FEDERATION_KEY_PATH
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return ""
