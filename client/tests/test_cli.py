"""Pure tests for muxplex_client.cli -- no subprocess, no network.

Handlers are called directly with an injected `FakeClient` (a duck-typed
stand-in for `MuxplexClient` that records calls and returns canned results
without ever touching httpx). Parser-shape assertions build the real parser
via `_build_parser()` and call `parse_args()` on canned argv lists. A few
`main()`-level tests monkeypatch `MuxplexClient.from_env` to verify the
top-level dispatch wiring end-to-end, still with zero network.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from muxplex_client import cli
from muxplex_client.config import ClientConfig
from muxplex_client.errors import (
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
from muxplex_client.models import Bell
from muxplex_client.sync_client import MuxplexClient

# ---------------------------------------------------------------------------
# Test double
# ---------------------------------------------------------------------------


class FakeClient:
    """Records every call and returns canned results -- no HTTP, ever.

    Any attribute access returns a bound recorder function; if a canned
    result was registered under that name it is returned (or raised, when
    the canned value is an exception instance). Passed to handlers via
    `cast(MuxplexClient, ...)` since it duck-types the surface rather than
    subclassing (avoiding ~25 near-duplicate signature overrides).
    """

    def __init__(self, config: ClientConfig | None = None, **returns: Any) -> None:
        self.config = config
        self._returns = returns
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        def _method(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            if name in self._returns:
                value = self._returns[name]
                if isinstance(value, BaseException):
                    raise value
                return value
            return None

        return _method


def _fake(**returns: Any) -> MuxplexClient:
    """Build a FakeClient with a default config, cast to MuxplexClient for typing."""
    config = returns.pop(
        "config",
        ClientConfig(
            server_url="http://127.0.0.1:8088",
            federation_key=None,
            ca_file=None,
            timeout=5.0,
            sources={"server_url": "default", "federation_key": "default"},
        ),
    )
    return cast(MuxplexClient, FakeClient(config=config, **returns))


def _parser() -> argparse.ArgumentParser:
    parser, _bell, _fed = cli._build_parser()
    return parser


# ---------------------------------------------------------------------------
# Parser argument shapes -- every documented command in SPEC.md section 2.3
# ---------------------------------------------------------------------------


def test_parser_global_flags() -> None:
    args = _parser().parse_args(
        [
            "--url",
            "https://h:1",
            "--key",
            "k",
            "--key-file",
            "/tmp/k",
            "--ca",
            "/tmp/ca.crt",
            "--timeout",
            "9.5",
            "--json",
            "health",
        ]
    )
    assert args.url == "https://h:1"
    assert args.federation_key == "k"
    assert args.key_file == "/tmp/k"
    assert args.ca == "/tmp/ca.crt"
    assert args.timeout == 9.5
    assert args.json is True


def test_parser_no_command() -> None:
    args = _parser().parse_args([])
    assert getattr(args, "command", None) is None


def test_parser_info() -> None:
    args = _parser().parse_args(["info", "--verbose"])
    assert args.command == "info"
    assert args.verbose is True


def test_parser_info_default_not_verbose() -> None:
    args = _parser().parse_args(["info"])
    assert args.verbose is False


def test_parser_health_auth_mode_ca() -> None:
    assert _parser().parse_args(["health"]).command == "health"
    assert _parser().parse_args(["auth-mode"]).command == "auth-mode"
    assert _parser().parse_args(["ca"]).command == "ca"


def test_parser_ls() -> None:
    args = _parser().parse_args(["ls", "--sort", "attention"])
    assert args.command == "ls"
    assert args.sort == "attention"
    assert _parser().parse_args(["ls"]).sort is None


def test_parser_ls_rejects_unknown_sort() -> None:
    with pytest.raises(SystemExit):
        _parser().parse_args(["ls", "--sort", "bogus"])


def test_parser_sessions() -> None:
    assert _parser().parse_args(["sessions"]).command == "sessions"


def test_parser_show() -> None:
    args = _parser().parse_args(["show", "web", "--lines", "50"])
    assert args.name == "web"
    assert args.lines == 50


def test_parser_new() -> None:
    args = _parser().parse_args(["new", "web", "--no-wait", "--wait-timeout", "3"])
    assert args.name == "web"
    assert args.no_wait is True
    assert args.wait_timeout == 3.0


def test_parser_new_defaults() -> None:
    args = _parser().parse_args(["new", "web"])
    assert args.no_wait is False
    assert args.wait_timeout == 6.0


def test_parser_rm() -> None:
    args = _parser().parse_args(["rm", "web", "--yes"])
    assert args.name == "web"
    assert args.yes is True


def test_parser_disconnect() -> None:
    assert _parser().parse_args(["disconnect"]).command == "disconnect"


def test_parser_connect() -> None:
    args = _parser().parse_args(["connect", "web"])
    assert args.name == "web"


def test_parser_send() -> None:
    args = _parser().parse_args(
        [
            "send",
            "web",
            "--text",
            "hi",
            "--key",
            "Enter",
            "--key",
            "Tab",
            "--enter",
            "--lines",
            "10",
        ]
    )
    assert args.name == "web"
    assert args.text == "hi"
    assert args.keys == ["Enter", "Tab"]
    assert args.enter is True
    assert args.lines == 10


def test_parser_send_defaults() -> None:
    args = _parser().parse_args(["send", "web"])
    assert args.text == ""
    assert args.keys == []
    assert args.enter is False
    assert args.lines is None


def test_parser_run() -> None:
    args = _parser().parse_args(
        [
            "run",
            "web",
            "pytest -x",
            "--timeout",
            "30",
            "--lines",
            "200",
            "--no-bell",
            "--exit-expr",
            "$status",
        ]
    )
    assert args.command == "run"
    assert args.name == "web"
    assert args.shell_command == "pytest -x"
    assert args.run_timeout == 30.0
    assert args.lines == 200
    assert args.no_bell is True
    assert args.exit_expr == "$status"


def test_parser_run_defaults() -> None:
    args = _parser().parse_args(["run", "web", "pytest"])
    assert args.run_timeout == 600.0
    assert args.lines == 500
    assert args.no_bell is False
    assert args.exit_expr == "$?"


def test_parser_run_global_timeout_is_independent_of_run_timeout() -> None:
    """The global --timeout (HTTP) and run's own --timeout (completion) must not collide."""
    args = _parser().parse_args(
        ["--timeout", "2", "run", "web", "pytest", "--timeout", "30"]
    )
    assert args.timeout == 2.0
    assert args.run_timeout == 30.0


def test_parser_bell_ring_and_clear() -> None:
    ring = _parser().parse_args(["bell", "ring", "web"])
    assert ring.command == "bell"
    assert ring.bell_command == "ring"
    assert ring.name == "web"

    clear = _parser().parse_args(["bell", "clear", "web"])
    assert clear.bell_command == "clear"


def test_parser_bell_no_subcommand() -> None:
    args = _parser().parse_args(["bell"])
    assert getattr(args, "bell_command", None) is None


def test_parser_state_get() -> None:
    args = _parser().parse_args(["state"])
    assert args.command == "state"
    assert getattr(args, "state_command", None) is None


def test_parser_state_set() -> None:
    args = _parser().parse_args(
        [
            "state",
            "set",
            "--active-view",
            "grid",
            "--active-session",
            "web",
            "--active-remote-id",
            "dev-2",
        ]
    )
    assert args.state_command == "set"
    assert args.active_view == "grid"
    assert args.active_session == "web"
    assert args.active_remote_id == "dev-2"
    assert args.clear_active_session is False


def test_parser_state_set_clear_active_session() -> None:
    args = _parser().parse_args(["state", "set", "--clear-active-session"])
    assert args.clear_active_session is True


def test_parser_state_set_rejects_session_and_clear_together() -> None:
    with pytest.raises(SystemExit):
        _parser().parse_args(
            ["state", "set", "--active-session", "web", "--clear-active-session"]
        )


def test_parser_settings_get() -> None:
    args = _parser().parse_args(["settings"])
    assert args.command == "settings"
    assert getattr(args, "settings_command", None) is None


def test_parser_settings_set() -> None:
    args = _parser().parse_args(
        ["settings", "set", "sort_order", "manual", "--allow-destructive"]
    )
    assert args.settings_command == "set"
    assert args.key == "sort_order"
    assert args.value == "manual"
    assert args.allow_destructive is True


def test_parser_settings_sync() -> None:
    args = _parser().parse_args(["settings", "sync"])
    assert args.settings_command == "sync"


def test_parser_settings_push() -> None:
    args = _parser().parse_args(["settings", "push", "-"])
    assert args.settings_command == "push"
    assert args.source == "-"


def test_parser_fed_ls() -> None:
    args = _parser().parse_args(["fed", "ls"])
    assert args.command == "fed"
    assert args.fed_command == "ls"


def test_parser_fed_connect() -> None:
    args = _parser().parse_args(["fed", "connect", "dev-2", "web"])
    assert args.device == "dev-2"
    assert args.session == "web"


def test_parser_fed_new() -> None:
    args = _parser().parse_args(["fed", "new", "dev-2", "web"])
    assert args.device == "dev-2"
    assert args.name == "web"


def test_parser_fed_rm() -> None:
    args = _parser().parse_args(["fed", "rm", "dev-2", "web", "--yes"])
    assert args.device == "dev-2"
    assert args.name == "web"
    assert args.yes is True


def test_parser_fed_bell_clear() -> None:
    args = _parser().parse_args(["fed", "bell-clear", "dev-2", "web"])
    assert args.device == "dev-2"
    assert args.name == "web"


def test_parser_fed_generate_key() -> None:
    args = _parser().parse_args(["fed", "generate-key", "--yes"])
    assert args.fed_command == "generate-key"
    assert args.yes is True


def test_parser_fed_no_subcommand() -> None:
    args = _parser().parse_args(["fed"])
    assert getattr(args, "fed_command", None) is None


def test_parser_heartbeat() -> None:
    args = _parser().parse_args(["heartbeat", "--device-id", "dev-1"])
    assert args.command == "heartbeat"
    assert args.device_id == "dev-1"


def test_parser_heartbeat_default_device_id() -> None:
    assert _parser().parse_args(["heartbeat"]).device_id is None


def test_parser_setup_hooks() -> None:
    assert _parser().parse_args(["setup-hooks"]).command == "setup-hooks"


# ---------------------------------------------------------------------------
# _emit -- JSON vs human rendering
# ---------------------------------------------------------------------------


def test_emit_json_mode(capsys: pytest.CaptureFixture[str]) -> None:
    cli._emit({"a": 1}, as_json=True, human=lambda: print("SHOULD NOT PRINT"))
    out = capsys.readouterr().out
    assert json.loads(out) == {"a": 1}
    assert "SHOULD NOT PRINT" not in out


def test_emit_human_mode(capsys: pytest.CaptureFixture[str]) -> None:
    cli._emit({"a": 1}, as_json=False, human=lambda: print("human output"))
    out = capsys.readouterr().out
    assert out == "human output\n"


def test_emit_json_uses_default_str_for_non_serializable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli._emit(Path("/tmp/x"), as_json=True, human=lambda: None)
    out = capsys.readouterr().out
    assert json.loads(out) == "/tmp/x"


# ---------------------------------------------------------------------------
# `ca` -- PEM to stdout, and nothing else
# ---------------------------------------------------------------------------


def test_cmd_ca_prints_only_the_pem(capsys: pytest.CaptureFixture[str]) -> None:
    client = _fake(
        ca_certificate="-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----\n"
    )
    cli.cmd_ca(client)
    captured = capsys.readouterr()
    assert (
        captured.out == "-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----\n"
    )
    assert captured.err == ""


def test_cmd_ca_adds_trailing_newline_if_missing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _fake(ca_certificate="no-trailing-newline")
    cli.cmd_ca(client)
    assert capsys.readouterr().out == "no-trailing-newline\n"


# ---------------------------------------------------------------------------
# Error presentation -- every exception in SPEC.md section 2.6
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        TlsTrustError("verify failed", hint="point --ca at the CA, not the leaf"),
        UnreachableError("connection refused"),
        AuthError("bad credential"),
        InputForbidden("web", "session not allowlisted"),
        SessionNotFound("web", "not found"),
        DestructiveChange("would shrink views", counts={"before": 8, "after": 1}),
        SettingsConflict("stale timestamp", settings_updated_at=123.0),
        CommandTimeout("web", "tok123", 12.3, "some pane text"),
        ConfigError("MUXPLEX_TIMEOUT='x' is not a valid number"),
        SessionWaitTimeout("web", 6.0),
    ],
)
def test_print_error_produces_nonempty_actionable_message(
    exc: MuxplexError, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _fake()
    cli._print_error(exc, client)
    err = capsys.readouterr().err
    assert err.strip() != ""


def test_print_error_tls_trust_includes_hint(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exc = TlsTrustError(
        "verify failed", hint="use the CA at ~/.config/muxplex/ca/muxplex-ca.crt"
    )
    cli._print_error(exc, _fake())
    err = capsys.readouterr().err
    assert "TLS" in err
    assert "muxplex-ca.crt" in err


def test_print_error_unreachable_names_url_and_source(
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = ClientConfig(
        server_url="https://example:8088",
        federation_key=None,
        ca_file=None,
        timeout=5.0,
        sources={"server_url": "env:MUXPLEX_URL"},
    )
    cli._print_error(UnreachableError("boom"), _fake(config=config))
    err = capsys.readouterr().err
    assert "https://example:8088" in err
    assert "env:MUXPLEX_URL" in err


def test_print_error_input_forbidden_prints_literal_json_no_retry(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exc = InputForbidden("web", "not allowlisted")
    cli._print_error(exc, _fake())
    err = capsys.readouterr().err
    assert '"input_enabled": true' in err
    assert '"web"' in err
    assert "retry" not in err.lower()
    assert "no api workaround" in err.lower() or "no workaround" in err.lower()


def test_print_error_auth_error_suggests_auth_mode(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli._print_error(AuthError("nope"), _fake())
    err = capsys.readouterr().err
    assert "auth-mode" in err


def test_print_error_session_not_found_suggests_ls(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli._print_error(SessionNotFound("web", "gone"), _fake())
    err = capsys.readouterr().err
    assert "muxplex-client ls" in err


def test_print_error_destructive_change_mentions_allow_destructive(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exc = DestructiveChange("would shrink", counts={"before": 8, "after": 1})
    cli._print_error(exc, _fake())
    err = capsys.readouterr().err
    assert "--allow-destructive" in err
    assert "8" in err and "1" in err


def test_print_error_settings_conflict_suggests_retry(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli._print_error(SettingsConflict("stale", settings_updated_at=1.0), _fake())
    err = capsys.readouterr().err
    assert "retry" in err.lower()


def test_print_error_config_error(capsys: pytest.CaptureFixture[str]) -> None:
    cli._print_error(ConfigError("MUXPLEX_TIMEOUT='x' is bad"), _fake())
    err = capsys.readouterr().err
    assert "MUXPLEX_TIMEOUT" in err


# ---------------------------------------------------------------------------
# Confirmation gates
# ---------------------------------------------------------------------------


class _FakeStdin:
    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_confirm_yes_flag_bypasses_prompt() -> None:
    assert cli._confirm("Delete it", yes=True) is True


def test_confirm_non_tty_without_yes_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=False))
    with pytest.raises(SystemExit) as exc_info:
        cli._confirm("Delete it", yes=False)
    assert exc_info.value.code == 1


def test_confirm_tty_accepts_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert cli._confirm("Delete it", yes=False) is True


def test_confirm_tty_declines_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert cli._confirm("Delete it", yes=False) is False


def test_cmd_rm_declines_does_not_delete(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "_confirm", lambda prompt, *, yes: False)
    client = _fake()
    cli.cmd_rm(client, "web", yes=False, as_json=False)
    fake = cast(FakeClient, client)
    assert not any(name == "delete_session" for name, _, _ in fake.calls)
    assert "Aborted" in capsys.readouterr().out


def test_cmd_rm_yes_deletes(capsys: pytest.CaptureFixture[str]) -> None:
    client = _fake()
    cli.cmd_rm(client, "web", yes=True, as_json=False)
    fake = cast(FakeClient, client)
    assert fake.calls[0] == ("delete_session", ("web",), {})
    assert "Deleted" in capsys.readouterr().out


def test_cmd_fed_rm_requires_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_confirm", lambda prompt, *, yes: False)
    client = _fake()
    cli.cmd_fed_rm(client, "dev-2", "web", yes=False, as_json=False)
    fake = cast(FakeClient, client)
    assert not any(name == "federation_delete_session" for name, _, _ in fake.calls)


def test_cmd_fed_generate_key_requires_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_confirm", lambda prompt, *, yes: False)
    client = _fake()
    cli.cmd_fed_generate_key(client, yes=False, as_json=False)
    fake = cast(FakeClient, client)
    assert not any(name == "generate_federation_key" for name, _, _ in fake.calls)


# ---------------------------------------------------------------------------
# `send` -- always prints the read-back snapshot; never fires blind
# ---------------------------------------------------------------------------


def test_cmd_send_prints_readback_snapshot_by_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _Result:
        session = "web"
        snapshot = "$ pane content after typing"

    client = _fake(send_input=_Result())
    cli.cmd_send(
        client, "web", text="ls", keys=[], enter=True, lines=None, as_json=False
    )
    out = capsys.readouterr().out
    assert "pane content after typing" in out


def test_cmd_send_requires_at_least_one_of_text_keys_enter() -> None:
    client = _fake()
    with pytest.raises(SystemExit) as exc_info:
        cli.cmd_send(
            client, "web", text="", keys=[], enter=False, lines=None, as_json=False
        )
    assert exc_info.value.code == 1


def test_cmd_send_warns_on_double_enter(capsys: pytest.CaptureFixture[str]) -> None:
    class _Result:
        session = "web"
        snapshot = ""

    client = _fake(send_input=_Result())
    cli.cmd_send(
        client, "web", text="", keys=["Enter"], enter=True, lines=None, as_json=False
    )
    err = capsys.readouterr().err
    assert "two Enters" in err


# ---------------------------------------------------------------------------
# `run` -- exits with the REMOTE command's exit code
# ---------------------------------------------------------------------------


def test_cmd_run_exits_with_remote_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _Result:
        session = "web"
        exit_code = 7
        elapsed = 1.23
        token = "tok"
        snapshot = "output here"

    client = _fake(run_shell_command=_Result())
    with pytest.raises(SystemExit) as exc_info:
        cli.cmd_run(
            client,
            "web",
            "pytest",
            timeout=600.0,
            lines=500,
            no_bell=False,
            exit_expr="$?",
            as_json=False,
        )
    assert exc_info.value.code == 7
    assert "output here" in capsys.readouterr().out


def test_cmd_run_json_payload_includes_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _Result:
        session = "web"
        exit_code = 0
        elapsed = 0.5
        token = "tok"
        snapshot = "ok"

    client = _fake(run_shell_command=_Result())
    with pytest.raises(SystemExit):
        cli.cmd_run(
            client,
            "web",
            "pytest",
            timeout=600.0,
            lines=500,
            no_bell=True,
            exit_expr="$?",
            as_json=True,
        )
    payload = json.loads(capsys.readouterr().out)
    assert payload["exit_code"] == 0
    assert payload["session"] == "web"


# ---------------------------------------------------------------------------
# `state set` / `settings set` -- argument-shaping only
# ---------------------------------------------------------------------------


def test_cmd_state_set_requires_at_least_one_flag() -> None:
    client = _fake()
    with pytest.raises(SystemExit) as exc_info:
        cli.cmd_state_set(
            client,
            active_view=None,
            active_session=None,
            active_remote_id=None,
            clear_active_session=False,
            as_json=False,
        )
    assert exc_info.value.code == 1


def test_cmd_state_set_clear_active_session_sends_none() -> None:
    client = _fake()
    cli.cmd_state_set(
        client,
        active_view=None,
        active_session=None,
        active_remote_id=None,
        clear_active_session=True,
        as_json=False,
    )
    fake = cast(FakeClient, client)
    name, _args, kwargs = fake.calls[0]
    assert name == "patch_state"
    assert kwargs == {"active_session": None}


def test_cmd_state_set_passes_only_provided_fields() -> None:
    client = _fake()
    cli.cmd_state_set(
        client,
        active_view="grid",
        active_session=None,
        active_remote_id=None,
        clear_active_session=False,
        as_json=False,
    )
    fake = cast(FakeClient, client)
    _name, _args, kwargs = fake.calls[0]
    assert kwargs == {"active_view": "grid"}


def test_cmd_settings_set_parses_json_value() -> None:
    captured: dict[str, Any] = {}

    class _FakeSettingsClient(FakeClient):
        def apply_settings(
            self, mutate: Any, *, allow_destructive: bool
        ) -> dict[str, Any]:
            captured["patch"] = mutate({})
            captured["allow_destructive"] = allow_destructive
            return {"ok": True}

    client = cast(MuxplexClient, _FakeSettingsClient())
    cli.cmd_settings_set(
        client, "sort_order", "true", allow_destructive=False, as_json=False
    )
    assert captured["patch"] == {"sort_order": True}
    assert captured["allow_destructive"] is False


def test_cmd_settings_set_falls_back_to_bare_string() -> None:
    captured: dict[str, Any] = {}

    class _FakeSettingsClient(FakeClient):
        def apply_settings(
            self, mutate: Any, *, allow_destructive: bool
        ) -> dict[str, Any]:
            captured["patch"] = mutate({})
            return {"ok": True}

    client = cast(MuxplexClient, _FakeSettingsClient())
    cli.cmd_settings_set(
        client, "label", "not-json-{{", allow_destructive=False, as_json=False
    )
    assert captured["patch"] == {"label": "not-json-{{"}


# ---------------------------------------------------------------------------
# `settings push` -- file / stdin / invalid JSON
# ---------------------------------------------------------------------------


def test_cmd_settings_push_from_file(tmp_path: Path) -> None:
    payload_file = tmp_path / "settings.json"
    payload_file.write_text(json.dumps({"sort_order": "manual"}), encoding="utf-8")
    client = _fake(put_settings_sync={"ok": True})
    cli.cmd_settings_push(client, str(payload_file), as_json=False)
    fake = cast(FakeClient, client)
    name, args, _kwargs = fake.calls[0]
    assert name == "put_settings_sync"
    assert args[0] == {"sort_order": "manual"}


def test_cmd_settings_push_from_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys, "stdin", type("S", (), {"read": lambda self: '{"a": 1}'})()
    )
    client = _fake(put_settings_sync={"ok": True})
    cli.cmd_settings_push(client, "-", as_json=False)
    fake = cast(FakeClient, client)
    _name, args, _kwargs = fake.calls[0]
    assert args[0] == {"a": 1}


def test_cmd_settings_push_invalid_json_exits_1(tmp_path: Path) -> None:
    payload_file = tmp_path / "bad.json"
    payload_file.write_text("{not json", encoding="utf-8")
    client = _fake()
    with pytest.raises(SystemExit) as exc_info:
        cli.cmd_settings_push(client, str(payload_file), as_json=False)
    assert exc_info.value.code == 1


def test_cmd_settings_push_non_object_exits_1(tmp_path: Path) -> None:
    payload_file = tmp_path / "list.json"
    payload_file.write_text("[1, 2, 3]", encoding="utf-8")
    client = _fake()
    with pytest.raises(SystemExit) as exc_info:
        cli.cmd_settings_push(client, str(payload_file), as_json=False)
    assert exc_info.value.code == 1


def test_cmd_settings_push_missing_file_exits_1() -> None:
    client = _fake()
    with pytest.raises(SystemExit) as exc_info:
        cli.cmd_settings_push(client, "/does/not/exist.json", as_json=False)
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# `fed ls` -- real sessions vs in-band status entries
# ---------------------------------------------------------------------------


def test_cmd_fed_ls_distinguishes_sessions_and_status(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _Entry:
        def __init__(
            self,
            *,
            device_id: str | None,
            device_name: str | None,
            name: str | None,
            status: str | None,
            raw: dict[str, Any],
        ) -> None:
            self.device_id = device_id
            self.device_name = device_name
            self.name = name
            self.status = status
            self.raw = raw

        @property
        def is_session(self) -> bool:
            return self.status is None

    entries = [
        _Entry(
            device_id="dev-2",
            device_name="laptop",
            name="web",
            status=None,
            raw={"name": "web"},
        ),
        _Entry(
            device_id="dev-3",
            device_name=None,
            name=None,
            status="unreachable",
            raw={"status": "unreachable"},
        ),
    ]
    client = _fake(federation_sessions=entries)
    cli.cmd_fed_ls(client, as_json=False)
    out = capsys.readouterr().out
    assert "laptop" in out and "web" in out
    assert "unreachable" in out


def test_cmd_fed_ls_json_uses_raw(capsys: pytest.CaptureFixture[str]) -> None:
    class _Entry:
        def __init__(self) -> None:
            self.raw = {"name": "web", "status": None}

    client = _fake(federation_sessions=[_Entry()])
    cli.cmd_fed_ls(client, as_json=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload == [{"name": "web", "status": None}]


# ---------------------------------------------------------------------------
# `heartbeat` -- device_id default falls back to this instance's own
# ---------------------------------------------------------------------------


def test_cmd_heartbeat_uses_explicit_device_id() -> None:
    client = _fake(heartbeat={"device_id": "explicit", "status": "ok"})
    cli.cmd_heartbeat(client, device_id="explicit", as_json=False)
    fake = cast(FakeClient, client)
    name, args, _kwargs = fake.calls[0]
    assert name == "heartbeat"
    assert args[0]["device_id"] == "explicit"


def test_cmd_heartbeat_falls_back_to_own_device_id() -> None:
    class _Info:
        device_id = "self-id"

    client = _fake(instance_info=_Info(), heartbeat={"status": "ok"})
    cli.cmd_heartbeat(client, device_id=None, as_json=False)
    fake = cast(FakeClient, client)
    heartbeat_calls = [c for c in fake.calls if c[0] == "heartbeat"]
    assert heartbeat_calls[0][1][0]["device_id"] == "self-id"


# ---------------------------------------------------------------------------
# `ls` human rendering -- matches SPEC.md section 2.4's sample block exactly
# ---------------------------------------------------------------------------


def test_cmd_ls_human_rendering_matches_spec_sample(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _Sess:
        def __init__(self, name: str, active: bool, needs_attention: bool) -> None:
            self.name = name
            self.active = active
            self.needs_attention = needs_attention
            self.bell = Bell(last_fired_at=None, seen_at=None, unseen_count=0)
            self.last_activity_at = None

    class _Result:
        view = "all"
        views = ()
        sort = "server"
        sessions = (
            _Sess("web", True, False),
            _Sess("build", False, True),
            _Sess("scratch", False, False),
        )

    client = _fake(view=_Result())
    cli.cmd_ls(client, sort=None, as_json=False)
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines == [
        "  * web      active",
        "  ! build    needs attention",
        "    scratch",
    ]


def test_cmd_ls_name_column_widens_for_long_names(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A name longer than the pad must not run into its status text."""

    class _Sess:
        def __init__(self, name: str, active: bool, needs_attention: bool) -> None:
            self.name = name
            self.active = active
            self.needs_attention = needs_attention
            self.bell = Bell(last_fired_at=None, seen_at=None, unseen_count=0)
            self.last_activity_at = None

    class _Result:
        view = "all"
        views = ()
        sort = "server"
        # 22 chars -- longer than any fixed pad a naive ljust() would use.
        sessions = (
            _Sess("amplifier-computer-use", False, True),
            _Sess("web", True, False),
        )

    client = _fake(view=_Result())
    cli.cmd_ls(client, sort=None, as_json=False)
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        "  ! amplifier-computer-use  needs attention",
        "  * web                     active",
    ]


# ---------------------------------------------------------------------------
# main() dispatch -- monkeypatched MuxplexClient.from_env, still zero network
# ---------------------------------------------------------------------------


def test_main_no_command_prints_help(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["muxplex-client"])
    cli.main()
    out = capsys.readouterr().out
    assert "usage" in out.lower()


def test_main_dispatches_health(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeClient(health={"status": "ok"})
    monkeypatch.setattr(MuxplexClient, "from_env", classmethod(lambda cls, **kw: fake))
    monkeypatch.setattr(sys, "argv", ["muxplex-client", "--json", "health"])
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"status": "ok"}


def test_main_config_error_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _raise(cls: type, **kw: Any) -> MuxplexClient:
        raise ConfigError("bad MUXPLEX_TIMEOUT")

    monkeypatch.setattr(MuxplexClient, "from_env", classmethod(_raise))
    monkeypatch.setattr(sys, "argv", ["muxplex-client", "health"])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 1
    assert "Configuration error" in capsys.readouterr().err


def test_main_muxplex_error_from_handler_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeClient(sessions=SessionNotFound("web", "gone"))

    class _RaisingClient(FakeClient):
        def session(self, *a: Any, **kw: Any) -> Any:
            raise SessionNotFound("web", "gone")

    raising = _RaisingClient()
    monkeypatch.setattr(
        MuxplexClient, "from_env", classmethod(lambda cls, **kw: raising)
    )
    monkeypatch.setattr(sys, "argv", ["muxplex-client", "show", "web"])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 1
    assert "not found" in capsys.readouterr().err
    del fake  # unused placeholder client for the module-level FakeClient import path


def test_main_bell_missing_subcommand_prints_help(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeClient()
    monkeypatch.setattr(MuxplexClient, "from_env", classmethod(lambda cls, **kw: fake))
    monkeypatch.setattr(sys, "argv", ["muxplex-client", "bell"])
    cli.main()
    out = capsys.readouterr().out
    assert "usage" in out.lower()


def test_main_fed_missing_subcommand_prints_help(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeClient()
    monkeypatch.setattr(MuxplexClient, "from_env", classmethod(lambda cls, **kw: fake))
    monkeypatch.setattr(sys, "argv", ["muxplex-client", "fed"])
    cli.main()
    out = capsys.readouterr().out
    assert "usage" in out.lower()


def test_main_warns_when_key_passed_on_cmdline(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeClient(health={"status": "ok"})
    monkeypatch.setattr(MuxplexClient, "from_env", classmethod(lambda cls, **kw: fake))
    monkeypatch.setattr(sys, "argv", ["muxplex-client", "--key", "secret", "health"])
    cli.main()
    err = capsys.readouterr().err
    assert "shell history" in err


def test_main_run_exits_with_remote_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _Result:
        session = "web"
        exit_code = 3
        elapsed = 0.1
        token = "tok"
        snapshot = "boom"

    fake = FakeClient(run_shell_command=_Result())
    monkeypatch.setattr(MuxplexClient, "from_env", classmethod(lambda cls, **kw: fake))
    monkeypatch.setattr(sys, "argv", ["muxplex-client", "run", "web", "false"])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 3


def test_main_closes_client(monkeypatch: pytest.MonkeyPatch) -> None:
    closed = []

    class _Client(FakeClient):
        def close(self) -> None:
            closed.append(True)

    fake = _Client(health={"status": "ok"})
    monkeypatch.setattr(MuxplexClient, "from_env", classmethod(lambda cls, **kw: fake))
    monkeypatch.setattr(sys, "argv", ["muxplex-client", "--json", "health"])
    cli.main()
    assert closed == [True]


def test_global_key_flag_does_not_collide_with_settings_set_positional() -> None:
    """`settings set KEY VALUE`'s positional must not shadow the global --key.

    argparse merges subparser results into one namespace, so a positional
    named `key` silently became `args.key` and tripped the "--key lands in
    your shell history" warning on a command that never passed --key.
    """
    parser, _, _ = cli._build_parser()
    args = parser.parse_args(["settings", "set", "sort_order", '"attention"'])
    assert args.key == "sort_order"
    assert args.federation_key is None

    with_flag = parser.parse_args(["--key", "secret", "settings", "set", "a", "1"])
    assert with_flag.key == "a"
    assert with_flag.federation_key == "secret"
