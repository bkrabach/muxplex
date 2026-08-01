"""Tests for muxplex's managed tmux configuration.

SAFETY: every test redirects the module-level path constants in
``muxplex.tmux_config`` at ``tmp_path``, so the real ``~/.tmux.conf`` and the
real ``~/.config/muxplex/tmux.d`` are never touched. Tests that need a real
tmux server always run it on a PRIVATE socket (``-L``) with ``HOME`` pointed at
the sandbox, so the developer's live sessions are never at risk. This mirrors
the redirect discipline in ``test_settings.py`` -- ``conftest.py`` guards ports
and ``serve()``, not ``Path.home()``.

Tests needing a real tmux binary are skipped when tmux is absent, so the suite
still runs on a machine without it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from muxplex import tmux_config as tc

pytestmark = pytest.mark.usefixtures("sandbox")

HAS_TMUX = shutil.which("tmux") is not None
needs_tmux = pytest.mark.skipif(not HAS_TMUX, reason="tmux not installed")

USER_CONF = """# my precious config, curated since 2011
set -g prefix C-a
unbind C-b
bind C-a send-prefix
set -g @mine "untouched"
"""


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect every path muxplex writes into an isolated fake HOME."""
    home = tmp_path / "home"
    (home / ".config" / "muxplex").mkdir(parents=True)
    monkeypatch.setattr(tc, "TMUX_CONF_PATH", home / ".tmux.conf")
    monkeypatch.setattr(
        tc, "XDG_TMUX_CONF_PATH", home / ".config" / "tmux" / "tmux.conf"
    )
    monkeypatch.setattr(tc, "TMUX_D_PATH", home / ".config" / "muxplex" / "tmux.d")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return home


def write_user_conf(home: Path, content: str, *, where: str = "dot") -> None:
    if where in ("dot", "both"):
        (home / ".tmux.conf").write_text(content)
    if where in ("xdg", "both"):
        p = home / ".config" / "tmux"
        p.mkdir(parents=True, exist_ok=True)
        (p / "tmux.conf").write_text(content)


def tmux_option(home: Path, option: str = "@muxplex_loaded") -> str:
    """Start a real tmux server against *home*'s config and read an option."""
    sock = "muxplex-test"
    env = {**os.environ, "HOME": str(home)}
    env.pop("TMUX", None)
    env.pop("XDG_CONFIG_HOME", None)
    kill = ["tmux", "-L", sock, "kill-server"]
    subprocess.run(kill, capture_output=True, env=env, check=False)
    subprocess.run(
        ["tmux", "-L", sock, "new-session", "-d"],
        capture_output=True,
        env=env,
        check=False,
    )
    out = subprocess.run(
        ["tmux", "-L", sock, "show-options", "-gv", option],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    ).stdout.strip()
    subprocess.run(kill, capture_output=True, env=env, check=False)
    return out


# ── Ground truth about tmux itself ─────────────────────────────────────────


@needs_tmux
def test_tmux_loads_both_user_configs_later_wins(sandbox: Path) -> None:
    """The measurement the whole design rests on.

    tmux >= 3.1 loads BOTH ~/.tmux.conf and the XDG config, in that order, and
    the later one wins conflicts. That is why we install into the earlier file.
    """
    (sandbox / ".tmux.conf").write_text('set -g @a "dot"\nset -g @who "dot"\n')
    xdg = sandbox / ".config" / "tmux"
    xdg.mkdir(parents=True, exist_ok=True)
    (xdg / "tmux.conf").write_text('set -g @b "xdg"\nset -g @who "xdg"\n')

    assert tmux_option(sandbox, "@a") == "dot", "~/.tmux.conf must be loaded"
    assert tmux_option(sandbox, "@b") == "xdg", "XDG config must be loaded"
    assert tmux_option(sandbox, "@who") == "xdg", "later file must win conflicts"


def test_install_target_is_always_the_earliest_loaded_file(sandbox: Path) -> None:
    write_user_conf(sandbox, USER_CONF, where="both")
    assert tc.install_target() == sandbox / ".tmux.conf"
    assert tc.candidate_config_paths()[0] == sandbox / ".tmux.conf"


# ── Install ────────────────────────────────────────────────────────────────


@needs_tmux
def test_install_creates_config_when_none_exists(sandbox: Path) -> None:
    r = tc.install()
    assert r["created"] is True
    assert (sandbox / ".tmux.conf").exists()
    assert tmux_option(sandbox) == "base-1"


@needs_tmux
def test_install_preserves_existing_content_and_user_wins(sandbox: Path) -> None:
    write_user_conf(sandbox, USER_CONF)
    r = tc.install()
    text = (sandbox / ".tmux.conf").read_text()

    assert text.startswith(tc.BEGIN_MARKER), "block must be at the TOP"
    assert USER_CONF.strip() in text, "every original byte preserved"
    assert r["backup"] is not None
    assert Path(str(r["backup"])).read_text() == USER_CONF

    assert tmux_option(sandbox) == "base-1"
    assert tmux_option(sandbox, "@mine") == "untouched"
    assert tmux_option(sandbox, "prefix") == "C-a", "user override must win"


@needs_tmux
def test_install_leaves_xdg_config_alone_and_user_still_wins(sandbox: Path) -> None:
    write_user_conf(sandbox, USER_CONF, where="xdg")
    r = tc.install()

    assert r["target"] == str(sandbox / ".tmux.conf")
    xdg_text = (sandbox / ".config" / "tmux" / "tmux.conf").read_text()
    assert tc.BEGIN_MARKER not in xdg_text, "must never edit the user's XDG file"
    assert tmux_option(sandbox) == "base-1"
    assert tmux_option(sandbox, "prefix") == "C-a", "XDG loads after us, so it wins"


@needs_tmux
def test_install_tolerates_missing_trailing_newline(sandbox: Path) -> None:
    write_user_conf(sandbox, 'set -g @mine "no-newline"')
    tc.install()
    assert tmux_option(sandbox) == "base-1"
    assert tmux_option(sandbox, "@mine") == "no-newline"


def test_install_is_idempotent(sandbox: Path) -> None:
    write_user_conf(sandbox, USER_CONF)
    tc.install()
    first = (sandbox / ".tmux.conf").read_text()
    second = tc.install()
    third = tc.install()

    text = (sandbox / ".tmux.conf").read_text()
    assert text.count(tc.BEGIN_MARKER) == 1, "exactly one block after three runs"
    assert text == first, "file unchanged on re-run"
    assert second["changed"] is False
    assert third["backup"] is None, "no backup churn on a no-op"


def test_install_replaces_a_stale_block_in_place(sandbox: Path) -> None:
    stale = (
        f"{tc.BEGIN_MARKER}\n"
        "source-file ~/.config/muxplex/OLD-PATH/*.conf\n"
        f"{tc.END_MARKER}\n\n" + USER_CONF
    )
    write_user_conf(sandbox, stale)
    tc.install()

    text = (sandbox / ".tmux.conf").read_text()
    assert text.count(tc.BEGIN_MARKER) == 1, "replaced, not duplicated"
    assert "OLD-PATH" not in text
    assert USER_CONF.strip() in text


def test_install_dry_run_writes_nothing(sandbox: Path) -> None:
    write_user_conf(sandbox, USER_CONF)
    r = tc.install(dry_run=True)
    assert (sandbox / ".tmux.conf").read_text() == USER_CONF
    assert tc.SOURCE_LINE in r["diff"]


def test_install_rejects_unknown_theme(sandbox: Path) -> None:
    with pytest.raises(tc.TmuxConfigError, match="Unknown tmux theme"):
        tc.install(theme="no-such-theme")


# ── Symlinked config (a tracked dotfiles repo) ─────────────────────────────


def test_install_refuses_to_write_through_a_symlink(sandbox: Path) -> None:
    repo = sandbox / "dotfiles"
    repo.mkdir()
    real = repo / "tmux.conf"
    real.write_text(USER_CONF)
    (sandbox / ".tmux.conf").symlink_to(real)

    with pytest.raises(tc.TmuxConfigError) as exc:
        tc.install()
    assert "symlink" in str(exc.value)
    assert "dotfiles" in str(exc.value), "error must name the real target"
    assert real.read_text() == USER_CONF, "tracked file untouched after refusal"


@needs_tmux
def test_install_through_symlink_preserves_the_link(sandbox: Path) -> None:
    """os.replace() onto a symlink path would replace the LINK with a regular
    file, silently detaching the user's dotfiles repo. It must not."""
    repo = sandbox / "dotfiles"
    repo.mkdir()
    real = repo / "tmux.conf"
    real.write_text(USER_CONF)
    (sandbox / ".tmux.conf").symlink_to(real)

    tc.install(allow_symlink=True)

    assert tc.BEGIN_MARKER in real.read_text(), "wrote through to the real file"
    assert (sandbox / ".tmux.conf").is_symlink(), "symlink must survive the write"
    assert tmux_option(sandbox) == "base-1"


# ── Uninstall ──────────────────────────────────────────────────────────────


@needs_tmux
def test_uninstall_restores_the_file_byte_for_byte(sandbox: Path) -> None:
    write_user_conf(sandbox, USER_CONF)
    tc.install()
    tc.uninstall()

    assert (sandbox / ".tmux.conf").read_text() == USER_CONF
    assert tmux_option(sandbox) == "", "muxplex config no longer loads"
    assert tmux_option(sandbox, "@mine") == "untouched", "user config still works"


def test_uninstall_is_a_clean_noop_when_not_installed(sandbox: Path) -> None:
    write_user_conf(sandbox, USER_CONF)
    assert tc.uninstall()["changed"] is False
    assert (sandbox / ".tmux.conf").read_text() == USER_CONF


# ── Status ─────────────────────────────────────────────────────────────────


def test_status_flags_a_block_in_a_later_loaded_file(sandbox: Path) -> None:
    """A block in the XDG file would outrank the user's own ~/.tmux.conf."""
    write_user_conf(sandbox, USER_CONF)
    xdg = sandbox / ".config" / "tmux"
    xdg.mkdir(parents=True, exist_ok=True)
    (xdg / "tmux.conf").write_text(tc.BLOCK_BODY + '\nset -g @mine "xdg"\n')

    st = tc.status()
    assert st.outranks_user is True
    assert xdg / "tmux.conf" in st.misplaced
    assert st.installed is False


def test_status_reports_fragments_in_load_order(sandbox: Path) -> None:
    tc.install()
    names = [p.name for p in tc.status().fragments]
    assert names == ["10-muxplex-base.conf", "20-theme.conf", "90-local.conf"]


# ── Fragments ──────────────────────────────────────────────────────────────


def test_local_fragment_is_created_once_and_never_rewritten(sandbox: Path) -> None:
    tc.render_fragments("brand")
    local = tc.TMUX_D_PATH / "90-local.conf"
    local.write_text("set -g @user-owned 1\n")
    tc.render_fragments("brand")
    assert local.read_text() == "set -g @user-owned 1\n", "muxplex must never write it"


def test_theme_switch_rewrites_only_the_theme_fragment(sandbox: Path) -> None:
    tc.render_fragments("brand")
    brand = (tc.TMUX_D_PATH / "20-theme.conf").read_text()
    tc.render_fragments("steel")
    steel = (tc.TMUX_D_PATH / "20-theme.conf").read_text()
    assert brand != steel
    assert "#00D9F5" in brand, "brand theme uses the muxplex accent"


def test_every_shipped_theme_is_selectable(sandbox: Path) -> None:
    themes = tc.available_themes()
    assert "brand" in themes
    for theme in themes:
        tc.render_fragments(theme)


@needs_tmux
def test_every_shipped_theme_actually_loads_in_tmux(sandbox: Path) -> None:
    """A theme that tmux rejects would break the user's whole config."""
    for theme in tc.available_themes():
        tc.install(theme=theme)
        assert tmux_option(sandbox) == "base-1", f"theme {theme} broke config load"


# ── Verification helpers ───────────────────────────────────────────────────


@needs_tmux
def test_verify_proves_the_fragment_loaded(sandbox: Path) -> None:
    tc.install()
    v = tc.verify(socket="muxplex-test-verify")
    assert v["loaded"] is True
    assert v["sentinel"] == "base-1"


@needs_tmux
def test_apply_live_is_a_noop_without_a_running_server(sandbox: Path) -> None:
    tc.install()
    r = tc.apply_live(socket="muxplex-test-no-such-server")
    assert r["applied"] is False


# ── Settings integration ───────────────────────────────────────────────────


def test_tmux_theme_default_names_a_real_shipped_theme() -> None:
    from muxplex.settings import DEFAULT_SETTINGS

    assert DEFAULT_SETTINGS["tmux_theme"] in tc.available_themes()


def test_tmux_theme_is_not_syncable() -> None:
    """It renders to a file on THIS host and must not cross a federation link."""
    from muxplex.settings import SYNCABLE_KEYS

    assert "tmux_theme" not in SYNCABLE_KEYS


# ── Status-bar regressions (reported 2026-08-01) ───────────────────────────
#
# Two bugs shipped in the first brand theme, both invisible to the tests that
# existed at the time because those only checked that tmux ACCEPTED the config:
#
#   1. Clicking a window label stopped switching windows. The MouseDown1Status
#      binding was fine -- an unbounded #{pane_current_path} in status-right ate
#      the columns the window list needed, pushing windows behind the ">"
#      truncation marker. A window that is not on screen has no mouse range.
#   2. Only the session badge looked padded. A cell's background fills the whole
#      character cell including leading, so segments without a background (or
#      with one indistinguishable from the bar) read as unpadded bare text.


def _theme_options(sandbox: Path, theme: str) -> dict[str, str]:
    """Read every status option a real tmux resolves for *theme*."""
    tc.install(theme=theme)
    sock = "muxplex-test-theme"
    env = {**os.environ, "HOME": str(sandbox)}
    env.pop("TMUX", None)
    env.pop("XDG_CONFIG_HOME", None)
    kill = ["tmux", "-L", sock, "kill-server"]
    subprocess.run(kill, capture_output=True, env=env, check=False)
    subprocess.run(
        ["tmux", "-L", sock, "new-session", "-d"],
        capture_output=True,
        env=env,
        check=False,
    )
    out = subprocess.run(
        ["tmux", "-L", sock, "show-options", "-g"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    ).stdout
    subprocess.run(kill, capture_output=True, env=env, check=False)
    opts = {}
    for line in out.splitlines():
        if " " in line:
            k, v = line.split(" ", 1)
            opts[k] = v.strip().strip('"')
    return opts


@needs_tmux
def test_status_right_is_bounded_so_the_window_list_stays_clickable(
    sandbox: Path,
) -> None:
    """status-left + status-right must leave room for windows on a narrow client.

    tmux gives the window list whatever columns the two ends do not take. If
    they take everything, windows fall behind ">" and cannot be clicked.
    """
    for theme in tc.available_themes():
        opts = _theme_options(sandbox, theme)
        left = int(opts["status-left-length"])
        right = int(opts["status-right-length"])
        assert left + right <= 90, (
            f"theme {theme}: status-left-length({left}) + status-right-length({right}) "
            f"= {left + right} leaves too little room for the window list on an "
            f"80-100 column client; windows would be unclickable behind '>'"
        )


@needs_tmux
def test_no_theme_puts_an_unbounded_path_in_the_status_bar(sandbox: Path) -> None:
    """A full #{pane_current_path} grows without limit and crowds out windows.

    Use #{b:pane_current_path} (basename) instead.
    """
    for theme in tc.available_themes():
        opts = _theme_options(sandbox, theme)
        right = opts.get("status-right", "")
        assert "#{pane_current_path}" not in right, (
            f"theme {theme}: status-right uses the unbounded #{{pane_current_path}}; "
            f"use #{{b:pane_current_path}} so the window list keeps its columns"
        )


@needs_tmux
def test_every_status_segment_paints_its_own_background(sandbox: Path) -> None:
    """Mixed filled/unfilled segments look like inconsistent vertical padding.

    A terminal cell's background fills the full cell height, so a segment with a
    background reads as a padded cell and one without reads as bare text.
    """
    for theme in tc.available_themes():
        opts = _theme_options(sandbox, theme)
        for key in (
            "status-left",
            "status-right",
            "window-status-format",
            "window-status-current-format",
        ):
            assert "bg=" in opts.get(key, ""), (
                f"theme {theme}: {key} paints no background, so it renders as "
                f"unpadded text next to segments that do"
            )


@needs_tmux
def test_window_cell_backgrounds_are_visibly_distinct_from_the_bar(
    sandbox: Path,
) -> None:
    """A background only 10 RGB-steps off the bar is invisible in practice."""

    def first_bg(fmt: str) -> tuple[int, int, int] | None:
        m = re.search(r"bg=#([0-9A-Fa-f]{6})", fmt)
        return tuple(int(m.group(1)[i : i + 2], 16) for i in (0, 2, 4)) if m else None  # type: ignore[return-value]

    for theme in tc.available_themes():
        opts = _theme_options(sandbox, theme)
        bar = first_bg(opts.get("status-style", ""))
        if bar is None:
            continue
        for key in ("window-status-format", "window-status-current-format"):
            cell = first_bg(opts.get(key, ""))
            assert cell is not None, f"theme {theme}: {key} has no bg colour"
            distance = sum(abs(a - b) for a, b in zip(cell, bar))
            assert distance >= 40, (
                f"theme {theme}: {key} background is only {distance} RGB-steps from "
                f"the status bar background -- it will read as having no padding"
            )
