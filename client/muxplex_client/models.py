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

    @property
    def needs_attention(self) -> bool:
        """unseen_count > 0 and (seen_at is None or last_fired_at > seen_at).

        Mirrors the server's `muxplex.bells.needs_attention` exactly --
        contract-tested for agreement in `test_client_contract.py` across a
        truth table of (unseen_count, seen_at, last_fired_at). Defensive on
        `last_fired_at is None` combined with a non-None `seen_at` (should
        not occur in practice, but treated as "not newer than seen_at"
        rather than raising, matching the server).
        """
        if self.unseen_count <= 0:
            return False
        if self.seen_at is None:
            return True
        if self.last_fired_at is None:
            return False
        return self.last_fired_at > self.seen_at


@dataclass(frozen=True)
class Session:
    """One tmux session, as returned by GET /api/sessions.

    `views`: the server-resolved list of user-view names this session
    belongs to (pins union glob-rule matches -- see
    `muxplex.views.annotate_view_membership`). Defaults to `()` so every
    existing construction site keeps compiling and so a pre-feature server
    (one that omits the field) parses cleanly -- see `parse_session()`.
    """

    name: str
    snapshot: str
    bell: Bell
    last_activity_at: float | None = None
    views: tuple[str, ...] = ()


@dataclass(frozen=True)
class SessionSnapshot:
    """A single session's pane content at a caller-chosen depth.

    Returned by GET /api/sessions/{name}. Unlike `Session` (the shared,
    ~2s-cycle poll cache), this is one fresh `capture-pane` call scoped to
    a single session.
    """

    name: str
    snapshot: str
    lines: int
    bell: Bell
    last_activity_at: float | None


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
    """GET /api/state."""

    active_session: str | None
    active_view: str  # defaults to "all" when absent/empty
    settings_updated_at: float | None = None
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
    """GET /api/instance-info."""

    name: str
    device_id: str
    version: str
    federation_enabled: bool
    tmux_socket_dir: str | None
    bell_hook_armed: bool | None  # None on servers < 0.18.0
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)


@dataclass(frozen=True)
class ConnectResult:
    """POST /api/sessions/{name}/connect."""

    active_session: str
    ttyd_port: int


@dataclass(frozen=True)
class InputResult:
    """POST /api/sessions/{name}/input."""

    session: str
    snapshot: str


@dataclass(frozen=True)
class CommandResult:
    """Result of `run_shell_command()`."""

    session: str
    exit_code: int
    snapshot: str
    elapsed: float
    token: str
