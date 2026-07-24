"""Tests for muxplex/breaker.py — per-key circuit breaker for federation remotes."""

from muxplex.breaker import CircuitBreaker


class FakeClock:
    """Injectable monotonic clock for deterministic cooldown tests."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_breaker(threshold: int = 2, cooldown: float = 60.0):
    clock = FakeClock()
    return CircuitBreaker(threshold=threshold, cooldown=cooldown, clock=clock), clock


def test_closed_by_default():
    breaker, _ = make_breaker()
    assert breaker.should_attempt("http://a:1") is True
    assert breaker.is_open("http://a:1") is False


def test_stays_closed_below_threshold():
    breaker, _ = make_breaker(threshold=2)
    opened = breaker.record_failure("http://a:1")
    assert opened is False, "first failure must not open the circuit"
    assert breaker.should_attempt("http://a:1") is True


def test_opens_at_threshold_and_blocks():
    breaker, _ = make_breaker(threshold=2)
    assert breaker.record_failure("http://a:1") is False
    assert breaker.record_failure("http://a:1") is True, (
        "second consecutive failure must open the circuit (and report the transition once)"
    )
    assert breaker.is_open("http://a:1") is True
    assert breaker.should_attempt("http://a:1") is False, (
        "open circuit within cooldown must block attempts"
    )


def test_open_transition_reported_exactly_once():
    breaker, _ = make_breaker(threshold=2)
    breaker.record_failure("http://a:1")
    assert breaker.record_failure("http://a:1") is True
    assert breaker.record_failure("http://a:1") is False, (
        "further failures on an already-open circuit must not re-report the transition"
    )


def test_success_below_threshold_resets_failure_count():
    breaker, _ = make_breaker(threshold=2)
    breaker.record_failure("http://a:1")
    assert breaker.record_success("http://a:1") is False, (
        "closing an already-closed circuit is not a recovery"
    )
    # Failure count was reset: one more failure must not open
    assert breaker.record_failure("http://a:1") is False
    assert breaker.should_attempt("http://a:1") is True


def test_half_open_probe_after_cooldown():
    breaker, clock = make_breaker(threshold=2, cooldown=60.0)
    breaker.record_failure("http://a:1")
    breaker.record_failure("http://a:1")
    assert breaker.should_attempt("http://a:1") is False

    clock.advance(59.0)
    assert breaker.should_attempt("http://a:1") is False, (
        "cooldown not yet elapsed — must still block"
    )

    clock.advance(2.0)  # past the 60s cooldown
    assert breaker.should_attempt("http://a:1") is True, (
        "after cooldown one half-open probe must be allowed"
    )
    # Window re-armed: a second attempt during the probe window is blocked
    assert breaker.should_attempt("http://a:1") is False


def test_half_open_to_closed_on_success():
    """The recovery path: open -> cooldown -> half-open probe succeeds -> closed."""
    breaker, clock = make_breaker(threshold=2, cooldown=60.0)
    breaker.record_failure("http://a:1")
    breaker.record_failure("http://a:1")
    clock.advance(61.0)
    assert breaker.should_attempt("http://a:1") is True  # half-open probe

    recovered = breaker.record_success("http://a:1")
    assert recovered is True, (
        "closing a previously-open circuit must report recovery (for the one-line log)"
    )
    assert breaker.is_open("http://a:1") is False
    assert breaker.should_attempt("http://a:1") is True
    # Fully reset: it takes threshold failures again to re-open
    assert breaker.record_failure("http://a:1") is False


def test_half_open_probe_failure_reopens():
    breaker, clock = make_breaker(threshold=2, cooldown=60.0)
    breaker.record_failure("http://a:1")
    breaker.record_failure("http://a:1")
    clock.advance(61.0)
    assert breaker.should_attempt("http://a:1") is True  # probe allowed

    assert breaker.record_failure("http://a:1") is False, (
        "failed probe re-opens silently — the open transition was already reported"
    )
    assert breaker.should_attempt("http://a:1") is False
    clock.advance(61.0)
    assert breaker.should_attempt("http://a:1") is True, (
        "next cooldown window allows another probe"
    )


def test_keys_are_independent():
    breaker, _ = make_breaker(threshold=2)
    breaker.record_failure("http://dead:1")
    breaker.record_failure("http://dead:1")
    assert breaker.should_attempt("http://dead:1") is False
    assert breaker.should_attempt("http://healthy:1") is True, (
        "one remote's open circuit must never affect another remote"
    )


def test_reset_clears_all_state():
    breaker, _ = make_breaker(threshold=2)
    breaker.record_failure("http://a:1")
    breaker.record_failure("http://a:1")
    breaker.reset()
    assert breaker.should_attempt("http://a:1") is True
    assert breaker.is_open("http://a:1") is False
