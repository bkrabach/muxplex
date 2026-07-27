"""Pure tests for muxplex_client.sentinel -- no network, no server import.

The digit-anchor regression (test_digit_anchor_regression) is the single
most important test in this package: it is the exact bug the library exists
to make unwritable. See sentinel.py's module docstring for the full
rationale.
"""

from __future__ import annotations

from muxplex_client.sentinel import make_sentinel


def test_digit_anchor_regression() -> None:
    """A bare-token match would false-positive on tmux's input echo.

    tmux echoes the literal, unexpanded '...EXIT_$?' into the pane the
    instant the line is sent -- before the shell has run anything. A naive
    `"MUXPLEX_DONE_<token>" in snapshot` substring check would report "done"
    immediately with no real exit code. The digit-anchored regex must NOT
    match this echoed-but-unexpanded text.
    """
    sentinel = make_sentinel("abc123")
    echoed_input = f'echo "MUXPLEX_DONE_{sentinel.token}_EXIT_$?"\n'
    assert sentinel.search(echoed_input) is None


def test_search_matches_real_exit_zero() -> None:
    sentinel = make_sentinel("tok")
    snapshot = "some output\nMUXPLEX_DONE_tok_EXIT_0\n$ "
    assert sentinel.search(snapshot) == 0


def test_search_matches_real_exit_nonzero() -> None:
    sentinel = make_sentinel("tok")
    snapshot = "some output\nMUXPLEX_DONE_tok_EXIT_1\n$ "
    assert sentinel.search(snapshot) == 1


def test_search_matches_multi_digit_exit_code() -> None:
    sentinel = make_sentinel("tok")
    snapshot = "MUXPLEX_DONE_tok_EXIT_127\n"
    assert sentinel.search(snapshot) == 127


def test_search_returns_none_when_absent() -> None:
    sentinel = make_sentinel("tok")
    assert sentinel.search("nothing interesting here") is None


def test_search_does_not_match_a_different_token() -> None:
    sentinel = make_sentinel("tok-a")
    snapshot = "MUXPLEX_DONE_tok-b_EXIT_0\n"
    assert sentinel.search(snapshot) is None


def test_wrap_default_includes_bell_clause() -> None:
    sentinel = make_sentinel("tok")
    wrapped = sentinel.wrap("do-thing")
    assert wrapped == (
        "do-thing; rc=$?; [ $rc -ne 0 ] && printf '\\a'; "
        'echo "MUXPLEX_DONE_tok_EXIT_$rc"'
    )


def test_wrap_omits_bell_clause_when_disabled() -> None:
    sentinel = make_sentinel("tok")
    wrapped = sentinel.wrap("do-thing", bell_on_failure=False)
    assert "printf" not in wrapped
    assert wrapped == 'do-thing; rc=$?; echo "MUXPLEX_DONE_tok_EXIT_$rc"'


def test_wrap_honors_non_default_exit_expr() -> None:
    sentinel = make_sentinel("tok")
    wrapped = sentinel.wrap("do-thing", exit_expr="$status")
    assert "rc=$status" in wrapped


def test_make_sentinel_generates_unique_tokens() -> None:
    a = make_sentinel()
    b = make_sentinel()
    assert a.token != b.token


def test_make_sentinel_escapes_regex_metacharacters_in_token() -> None:
    # A caller-supplied token could contain regex-special characters; the
    # compiled pattern must treat it as a literal, not a regex fragment.
    sentinel = make_sentinel("a.b*c")
    assert sentinel.search("MUXPLEX_DONE_aXbYc_EXIT_0") is None
    assert sentinel.search("MUXPLEX_DONE_a.b*c_EXIT_0") == 0
