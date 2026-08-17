"""Response shapes for the muxplex client.

All frozen dataclasses, deliberately not pydantic -- see
../muxplex-client-design.md §8 ("Rejected: pydantic models"): heavy for six
shapes, and actively wrong here, since AGENTS.md requires clients to
tolerate unknown fields. `.get()`-based parsing in `_protocol.py` satisfies
that by construction; a strict model would have to be deliberately loosened
to avoid violating the contract.

Where a `raw` field appears (`ServerState`, `Settings`, `InstanceInfo` -- the
single-object, evolution-likely shapes) it is excluded from `__eq__`/`__hash__`
via `compare=False` so a frozen dataclass carrying a dict stays hashable, and
it is how "clients tolerate unknown fields" becomes usable rather than lossy:
a caller needing a field the model doesn't expose yet can still reach it.
`Session`/`Bell`/etc. are hot-path list items with a stable shape and omit it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class Bell:
    """A session's bell-alert sub-state, as returned by GET /api/sessions."""

    last_fired_at: float | None
    seen_at: float | None
    unseen_count: int
    source: str | None = None
    """Closed enum recording which detection path recorded the last bell:
    "hook" (POST /bell), "poll" (window_bell_flag transition), "seeded"
    (muxplex manufactured it for a new session), "halt" (the follow-up
    queue itself halted), or None (no bell has fired, or a pre-feature
    server omitted the key -- see docs/plans/2026-08-07-bell-causality-plan.md
    §4). Defaulted and LAST so every existing construction site keeps
    compiling and a pre-feature server's response parses cleanly (the same
    treatment `views`/`created_at` already got). Agent-facing only --
    rendered nowhere in the PWA, and deliberately NOT read by
    `needs_attention` below (see that property's docstring and §3 of the
    plan)."""

    @property
    def needs_attention(self) -> bool:
        """unseen_count > 0 and (seen_at is None or last_fired_at > seen_at).

        Mirrors the server's `muxplex.bells.needs_attention` exactly --
        contract-tested for agreement in `test_client_contract.py` across a
        truth table of (unseen_count, seen_at, last_fired_at). Defensive on
        `last_fired_at is None` combined with a non-None `seen_at` (should
        not occur in practice, but treated as "not newer than seen_at"
        rather than raising, matching the server).

        `source` is NEVER read here, by design -- it labels/triages a bell
        for a poller, it does not change whether the bell needs attention
        (docs/plans/2026-08-07-bell-causality-plan.md §3). Contract-tested
        with every enum value injected in test_client_contract.py.
        """
        if self.unseen_count <= 0:
            return False
        if self.seen_at is None:
            return True
        if self.last_fired_at is None:
            return False
        return self.last_fired_at > self.seen_at


@dataclass(frozen=True)
class Followups:
    """The queue badge on GET /api/sessions and GET /api/view entries.

    Deliberately not the full queue (that's `FollowupQueue`, from
    GET .../followups) -- this is the lightweight badge carried alongside
    every session on the shared poll cache, so a halted queue is visible
    without a second round trip per session.
    """

    pending: int = 0
    halted: bool = False


@dataclass(frozen=True)
class Session:
    """One tmux session, as returned by GET /api/sessions.

    `views`: the server-resolved list of user-view names this session
    belongs to (pins union glob-rule matches -- see
    `muxplex.views.annotate_view_membership`). Defaults to `()` so every
    existing construction site keeps compiling and so a pre-feature server
    (one that omits the field) parses cleanly -- see `parse_session()`.

    `created_at`: tmux's own `#{session_created}`, unix epoch seconds.
    `None` when tmux reported no parseable value for this session, or when
    talking to a pre-this-field server (defaults to `None` so every
    existing construction site keeps compiling, same rationale as `views`
    above). Paired with `InstanceInfo.server_started_at`: a session is
    genuinely new to that server's current process iff `created_at >=
    server_started_at` -- see ../../docs/API_SEMANTICS.md for why both
    halves are shipped as raw values rather than a single precomputed
    boolean.

    `followups`: the queue badge (see `Followups`). Defaults to
    `Followups()` so a pre-feature server (no key) parses cleanly.

    `cwd`: the session's working directory (tmux's own
    `#{session_path}`), or `None` on a pre-this-field server or when tmux
    reported nothing parseable.

    `device_id`/`device_name`/`device_version`/`remote_id`/`session_key`:
    federation-aware additions, present only on entries returned by
    GET /api/federation/sessions (main.py's `federation_sessions()`) --
    `sessions()`/GET /api/sessions never tags these keys at all, so every
    field here defaults to `None` and a pre-federation-aware server (or
    the plain local endpoint) parses exactly as before. Wire field names
    are camelCase (`deviceId`/`deviceName`/`deviceVersion`/`remoteId`/
    `sessionKey`) -- deliberately NOT renamed to match every other
    snake_case field on this model, because that IS the real wire shape
    `federation_sessions()` sends (see that route's docstring and
    `parse_session()`).

    `remote_id`: `None` for a local session (both a session from
    `sessions()`, and a LOCAL entry within `federation_sessions()`'s
    merged list -- the server tags those with `remoteId: null`
    explicitly). Non-`None` marks this session as living on a federation
    peer, and is exactly the value `connect()`'s `remote_id` parameter
    expects -- pass a `Session` straight through without inspecting any
    other field to route the connect correctly.

    `device_id`: populated for BOTH local and remote entries when the
    session came from `federation_sessions()` (the local device's own id,
    or the peer's `device_id`); `None` when the session came from
    `sessions()` (that endpoint never tags it). Note this is NOT the same
    concept as `remote_id`: `device_id` identifies *whose* session this
    is, `remote_id` identifies *whether -- and where -- to proxy a
    connect* (the two happen to share a value for remote sessions, but
    `device_id` is also set, to a different value, for LOCAL sessions
    within a `federation_sessions()` response, where `remote_id` stays
    `None`).

    `session_key`: `f\"{device_id}:{name}\"`, globally unique across a
    federation where two different servers may each have a same-named
    session -- prefer this over `name` for de-duplication/identity when
    working with `federation_sessions()` results (mirrors the PWA's own
    `s.sessionKey || s.name` convention in frontend/app.js).
    """

    name: str
    snapshot: str
    bell: Bell
    last_activity_at: float | None = None
    views: tuple[str, ...] = ()
    created_at: float | None = None
    followups: Followups = Followups()
    cwd: str | None = None
    device_id: str | None = None
    device_name: str | None = None
    device_version: str | None = None
    remote_id: str | None = None
    session_key: str | None = None


@dataclass(frozen=True)
class RemoteStatus:
    """One federation peer's non-session status entry from
    GET /api/federation/sessions.

    Emitted by main.py's `federation_sessions()` INSTEAD OF session
    entries for a given peer when that peer contributed no sessions to
    this poll: `\"unreachable\"` (connection failure), `\"auth_failed\"`
    (401/403 from the peer -- its federation key is stale/wrong),
    `\"empty\"` (peer reachable, zero tmux sessions there). These three
    strings are the entire closed set the server sends today (never a new
    status string per that route's docstring) -- surfaced here rather
    than silently dropped, so a caller can render e.g. an offline tile
    exactly as the PWA does.

    `device_version` is `None` when unknown (unreachable peer, or a peer
    too old to serve `/api/instance-info`) -- never defaulted to a real
    version string, same non-guessing rule as the server's own
    `deviceVersion` field.
    """

    device_id: str
    remote_id: str
    device_name: str
    status: str
    device_version: str | None = None


@dataclass(frozen=True)
class FederationSessions:
    """GET /api/federation/sessions -- the federation-aware analogue of
    `sessions()`.

    `sessions`: every connectable session, local and remote alike (each
    `Session.remote_id` is `None` for local, non-`None` for a session
    living on a peer -- see `Session`'s docstring). `statuses`: one
    `RemoteStatus` entry per peer that contributed no sessions this poll
    (unreachable/auth_failed/empty) -- kept separate from `sessions`
    rather than merged into it, since a status entry has no `name` to
    connect to.

    A brand-new type rather than reusing `list[Session]` as `sessions()`
    does: `sessions()`'s wire response never carries peer-status entries
    at all, so overloading its return shape would either lose that
    information or make every existing `sessions()` caller handle a
    shape it never sees in practice. Kept as a SEPARATE method/type for
    exactly that reason -- see `MuxplexClient.federation_sessions()`.
    """

    sessions: tuple[Session, ...]
    statuses: tuple[RemoteStatus, ...]


@dataclass(frozen=True)
class SessionSnapshot:
    """A single session's pane content at a caller-chosen depth.

    Returned by GET /api/sessions/{name}. Unlike `Session` (the shared,
    ~2s-cycle poll cache), this is one fresh `capture-pane` call scoped to
    a single session.

    `created_at`, `followups`, `views`, and `cwd` are field-parity
    additions with `Session` (the server added them to this endpoint so
    polling one session and polling the bulk list never disagree about
    what the session's state is -- see main.py's `get_session_snapshot`
    docstring). All four default so a pre-parity server parses cleanly;
    `lines` keeps its exact existing meaning (the depth REQUESTED) and is
    unaffected.
    """

    name: str
    snapshot: str
    lines: int
    bell: Bell
    last_activity_at: float | None
    created_at: float | None = None
    followups: Followups = Followups()
    views: tuple[str, ...] = ()
    cwd: str | None = None
    # Scrollback-paging additions (docs/plans/2026-08-07-scrollback-paging-plan.md
    # §3.3/§5) -- all default so a pre-paging server still parses cleanly.
    # `start` is the absolute row index of the first returned row; the next
    # (older) page is always a `session(name, before=start)` call. See
    # docs/AGENT_GUIDE.md §6.3.1 for the has_more/saturated truth table.
    start: int | None = None
    row_count: int | None = None
    total: int | None = None
    has_more: bool = False
    saturated: bool = False


@dataclass(frozen=True)
class ViewSession:
    """One entry of GET /api/view's `sessions` list."""

    name: str
    active: bool
    needs_attention: bool
    bell: Bell
    last_activity_at: float | None


@dataclass(frozen=True)
class ViewResult:
    """The server-resolved current view: GET /api/view."""

    view: str
    views: tuple[str, ...]
    sort: str  # "server" | "alphabetical" | "attention"
    sessions: tuple[ViewSession, ...]


@dataclass(frozen=True)
class ServerState:
    """GET /api/state.

    `sync_group`, `controlled_by`, and `active_remote_id` are additive
    fields for the deck-control-target feature
    (docs/plans/2026-08-16-deck-control-target-design.md §8.1 #10, §8.3).
    All three parse via `.get()` and default to `None` -- a pre-feature
    server that omits them parses cleanly, never raises.

    `sync_group`: the group `?device_id=` resolved to (`"global"` when no
    `device_id` was sent), echoed by the server today. `controlled_by`:
    the device_id of whoever is following THIS device, if any -- `None`
    on every server today (the field itself is Step 2 of that design;
    absent-safe now so no follow-up client release is needed once it
    ships). `active_remote_id`: non-`None` when the projected group's
    active session came from a federation peer rather than this server
    (docs/plans/2026-08-16-deck-control-target-design.md §4.5/§7.2's v1
    ship-blocker) -- previously dropped on the floor entirely.
    """

    active_session: str | None
    active_view: str  # defaults to "all" when absent/empty
    settings_updated_at: float | None = None
    sync_group: str | None = None
    controlled_by: str | None = None
    active_remote_id: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)


@dataclass(frozen=True)
class View:
    """One user-defined view, as returned by GET /api/settings."""

    name: str
    sessions: frozenset[str]


@dataclass(frozen=True)
class Settings:
    """GET /api/settings."""

    views: tuple[View, ...]
    hidden_sessions: frozenset[str]
    sort_order: str  # default "manual"
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)


@dataclass(frozen=True)
class InstanceInfo:
    """GET /api/instance-info.

    `server_started_at`: the moment THIS instance's process actually came
    up (unix epoch seconds); `None` on a server older than this field.
    This is the watermark a `Session.created_at` must be compared against
    to reproduce the server's own "genuinely new" rule -- see
    `Session.created_at`'s docstring and ../../docs/API_SEMANTICS.md.
    """

    name: str
    device_id: str
    version: str
    federation_enabled: bool
    tmux_socket_dir: str | None
    bell_hook_armed: bool | None  # None on servers < 0.18.0
    server_started_at: float | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)


@dataclass(frozen=True)
class ConnectResult:
    """POST /api/sessions/{name}/connect."""

    active_session: str
    ttyd_port: int


@dataclass(frozen=True)
class HeartbeatResult:
    """POST /api/heartbeat.

    `sync_group` is the group the device is ACTUALLY in after this call
    -- when the caller omitted `sync_group` (meaning "leave unchanged"),
    this still reports the resolved value, exactly the same
    request-vs-resolved distinction `ConnectResult.active_session` and
    `main.py`'s `get_state()`/`patch_state()` docstrings already make
    (see docs/plans/2026-08-16-deck-control-target-design.md §8.3).
    """

    device_id: str
    status: str
    sync_group: str


@dataclass(frozen=True)
class RenameResult:
    """POST /api/sessions/{name}/rename.

    `name` is the name tmux ACTUALLY has after the call -- never the
    requested name echoed back (see docs/plans/2026-08-07-session-rename-plan.md
    \u00a75.2: tmux can report success while silently mangling the result).
    `renamed` is False only for the \u00a77.3 no-op case (new_name == old name);
    `migrated` is the per-keyspace evidence dict (empty in the no-op case).
    """

    from_name: str
    name: str
    renamed: bool = True
    migrated: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)


@dataclass(frozen=True)
class InputResult:
    """POST /api/sessions/{name}/input."""

    session: str
    snapshot: str


@dataclass(frozen=True)
class FocusResult:
    """POST /api/focus -- see MuxplexClient.raise_focus()."""

    platform: str
    app: str


@dataclass(frozen=True)
class FollowupItem:
    """One item in a session's follow-up queue.

    Returned by every follow-up endpoint (GET/POST/PUT/.../resume) as
    either the sole `item` (append) or within `items` (the rest).
    """

    id: str
    text: str
    enter: bool
    created_at: float | None = None


@dataclass(frozen=True)
class FollowupQueue:
    """The full follow-up queue for one session.

    Returned by GET/PUT/DELETE/.../resume on
    /api/sessions/{name}/followups.

    `halted` stays a raw mapping rather than a typed dataclass: it is a
    diagnostic payload whose shape is the server's to evolve, and the
    only question a caller asks of it is `is not None`. Typing it would
    create a second place to keep in sync for no benefit.
    """

    session: str
    revision: int
    items: tuple[FollowupItem, ...]
    halted: Mapping[str, Any] | None  # None = not halted
    target_window: str | None = None


@dataclass(frozen=True)
class SessionCommand:
    """One configured session command pair, as returned by
    GET /api/session-commands.

    The templates are arbitrary shell commands the server runs -- see
    that endpoint's docstring for why it deliberately sits outside the
    auth-exempt path.
    """

    id: str
    label: str
    new_session_template: str
    delete_session_template: str


@dataclass(frozen=True)
class SessionCommands:
    """GET /api/session-commands -- the canonical, server-resolved list
    of configured session command pairs. `commands` is never empty;
    `commands[0].id == default_id`.
    """

    commands: tuple[SessionCommand, ...]
    default_id: str
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommandResult:
    """Result of `run_shell_command()`."""

    session: str
    exit_code: int
    snapshot: str
    elapsed: float
    token: str
