"""muxplex_client -- typed sync/async HTTP client for the muxplex API.

Basic usage:

    >>> from muxplex_client import MuxplexClient
    >>> with MuxplexClient("https://your-server:8088", "federation-key") as client:
    ...     sessions = client.sessions()

Async usage:

    >>> from muxplex_client import AsyncMuxplexClient
    >>> async with AsyncMuxplexClient("https://your-server:8088", "federation-key") as client:
    ...     sessions = await client.sessions()

See ../README.md and ../../muxplex-client-design.md for the full design
rationale (why this is a second distribution, version-alignment policy, the
sentinel's digit-anchor rule, and what was deliberately excluded).
"""

from __future__ import annotations

import importlib.metadata

from .async_client import AsyncMuxplexClient
from .constants import (
    DEFAULT_CAPTURE_LINES,
    KNOWN_KEYS,
    MAX_CAPTURE_LINES,
    MAX_KEYS,
    MIN_SERVER_VERSION,
)
from .errors import (
    ApiError,
    AuthError,
    CommandTimeout,
    InputForbidden,
    MuxplexError,
    SessionNotFound,
    UnreachableError,
)
from .models import (
    Bell,
    CommandResult,
    ConnectResult,
    InputResult,
    InstanceInfo,
    ServerState,
    Session,
    SessionSnapshot,
    Settings,
    View,
    ViewResult,
    ViewSession,
)
from .sentinel import Sentinel, make_sentinel
from .sync_client import MuxplexClient

try:
    __version__ = importlib.metadata.version("muxplex-client")
except importlib.metadata.PackageNotFoundError:
    # Editable/workspace checkout with no build metadata yet (e.g. running
    # straight from a source tree before an initial `uv sync`).
    __version__ = "0.0.0.dev0"

__all__ = [
    "__version__",
    "ApiError",
    "AsyncMuxplexClient",
    "AuthError",
    "Bell",
    "CommandResult",
    "CommandTimeout",
    "ConnectResult",
    "DEFAULT_CAPTURE_LINES",
    "InputForbidden",
    "InputResult",
    "InstanceInfo",
    "KNOWN_KEYS",
    "MAX_CAPTURE_LINES",
    "MAX_KEYS",
    "MIN_SERVER_VERSION",
    "MuxplexClient",
    "MuxplexError",
    "Sentinel",
    "ServerState",
    "Session",
    "SessionNotFound",
    "SessionSnapshot",
    "Settings",
    "UnreachableError",
    "View",
    "ViewResult",
    "ViewSession",
    "make_sentinel",
]
