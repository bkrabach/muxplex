"""Per-key circuit breaker for federation remotes.

Prevents a persistently-unreachable remote from lagging every federation
fan-out: after ``threshold`` consecutive connection failures the circuit
OPENs and the remote is skipped (no network call) for ``cooldown`` seconds.
After the cooldown one probe is allowed (half-open); success closes the
circuit, failure re-opens it for another cooldown window.

In-memory only — state resets on restart, which is fine: the first poll
after restart re-discovers reachability at the cost of one timeout.

Only count *connection-level* failures (connect refused, timeout — the
remote is unreachable). A remote that responds with an HTTP error is
reachable and must NOT be circuit-broken; call ``record_success`` for it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class _KeyState:
    failures: int = 0  # consecutive connection failures
    opened_at: float | None = None  # monotonic time the circuit opened; None = closed


class CircuitBreaker:
    """Track consecutive connection failures per key and gate retry attempts.

    Args:
        threshold: consecutive failures before the circuit opens.
        cooldown: seconds to skip a key after its circuit opens.
        clock: monotonic time source (injectable for tests).
    """

    def __init__(
        self,
        threshold: int = 2,
        cooldown: float = 60.0,
        clock=time.monotonic,
    ) -> None:
        self.threshold = threshold
        self.cooldown = cooldown
        self._clock = clock
        self._states: dict[str, _KeyState] = {}

    def should_attempt(self, key: str) -> bool:
        """Return True if a network call to ``key`` is allowed right now.

        Closed circuit: always True. Open circuit: False until ``cooldown``
        has elapsed, then True once (half-open probe) — the window is
        re-armed so repeated calls during the probe don't hammer the remote.
        """
        state = self._states.get(key)
        if state is None or state.opened_at is None:
            return True
        if self._clock() - state.opened_at >= self.cooldown:
            state.opened_at = self._clock()  # re-arm: one probe per cooldown window
            return True
        return False

    def record_success(self, key: str) -> bool:
        """Reset ``key`` to closed. Returns True if a previously-open circuit
        just closed (i.e. the remote recovered) so the caller can log it once."""
        state = self._states.pop(key, None)
        return state is not None and state.opened_at is not None

    def record_failure(self, key: str) -> bool:
        """Record a connection failure. Returns True if this failure opened
        the circuit (closed -> open transition) so the caller can log it once."""
        state = self._states.setdefault(key, _KeyState())
        state.failures += 1
        if state.opened_at is None:
            if state.failures >= self.threshold:
                state.opened_at = self._clock()
                return True
        else:
            state.opened_at = self._clock()  # failed half-open probe: re-open window
        return False

    def is_open(self, key: str) -> bool:
        """Return True if the circuit for ``key`` is currently open."""
        state = self._states.get(key)
        return state is not None and state.opened_at is not None

    def reset(self) -> None:
        """Clear all state (tests)."""
        self._states.clear()
