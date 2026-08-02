"""Managed tmux configuration for muxplex.

muxplex ships an opinionated tmux config and installs it by adding ONE guarded
line to the user's tmux config file. Everything muxplex owns lives under
``~/.config/muxplex/tmux.d/``; the user's own file is edited exactly once, at
the top, and can be restored byte-for-byte by ``muxplex tmux uninstall``.

WHY THE DESIGN LOOKS LIKE THIS -- every constraint below was measured, not
assumed (tmux 3.4 on Linux):

  * tmux >= 3.1 loads EVERY user config in its search path, in order:
    ``/etc/tmux.conf``, then ``~/.tmux.conf``, then
    ``$XDG_CONFIG_HOME/tmux/tmux.conf``. It does NOT stop at the first one
    found -- both load, and the later one wins conflicts.

  * Therefore we install into ``~/.tmux.conf``, the EARLIEST user file, and put
    the block at the TOP of it. That places muxplex's defaults first in the
    whole chain, so anything the user has -- later in that file, or anywhere in
    their XDG config -- overrides us. Installing into the XDG file, or at the
    bottom, would silently make muxplex outrank the user's own settings. This
    is deliberately the opposite of the conda/rustup/nvm convention: they
    install last because they want to win. We install first because we want to
    lose.

  * ``source-file -q <glob>`` is silent when the glob matches nothing and does
    not abort the rest of the config, so an absent or empty ``tmux.d`` is a
    true no-op. Glob support requires tmux >= 3.0.

  * A tmux config path is often a symlink into a tracked dotfiles repo. Writing
    through one without saying so would commit to someone's repo on their
    behalf, and ``os.replace()`` onto a symlink path replaces the LINK with a
    regular file, silently detaching it. We refuse by default, and when
    explicitly allowed we resolve first and leave the link intact.

SAFETY POSTURE. This is the only place muxplex writes to a file it did not
create, so every write is: backed up first, atomic (tmp + ``os.replace``, the
``state.py``/``manifest.py`` pattern rather than the non-atomic ``settings.py``
one), verified by re-reading the file AND by starting a throwaway tmux server
on a private socket, and reversible. Anything ambiguous raises
``TmuxConfigError`` -- there is no degraded path.
"""

from __future__ import annotations

import difflib
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
# Module-level constants (not inline Path.home() calls) so tests can redirect
# them, matching settings.SETTINGS_PATH.

TMUX_CONF_PATH = Path.home() / ".tmux.conf"
XDG_TMUX_CONF_PATH = (
    (
        Path(os.environ["XDG_CONFIG_HOME"])
        if os.environ.get("XDG_CONFIG_HOME")
        else Path.home() / ".config"
    )
    / "tmux"
    / "tmux.conf"
)
TMUX_D_PATH = Path.home() / ".config" / "muxplex" / "tmux.d"
TEMPLATES_PATH = Path(__file__).parent / "tmux_templates"

# ── The managed block ──────────────────────────────────────────────────────

BEGIN_MARKER = "# >>> muxplex managed block >>>"
END_MARKER = "# <<< muxplex managed block <<<"
SOURCE_LINE = "source-file -q ~/.config/muxplex/tmux.d/*.conf"

MIN_TMUX_VERSION = (3, 0)  # source-file glob support

BLOCK_BODY = f"""{BEGIN_MARKER}
# Managed by muxplex. Do not edit between these markers -- your changes will be
# overwritten. Put your own tmux settings anywhere BELOW this block (they win),
# or in ~/.config/muxplex/tmux.d/90-local.conf.
# Remove with: muxplex tmux uninstall
{SOURCE_LINE}
{END_MARKER}"""

_BLOCK_RE = re.compile(
    re.escape(BEGIN_MARKER) + r".*?" + re.escape(END_MARKER) + r"\n?", re.DOTALL
)

_LOCAL_FRAGMENT_HEADER = (
    "# Your tmux settings. muxplex never writes this file.\n"
    "# Loads after everything muxplex ships, so anything here wins.\n"
)


class TmuxConfigError(RuntimeError):
    """Loud failure. Never swallowed, never downgraded to a warning."""


@dataclass
class Status:
    tmux_version: tuple[int, int] | None
    loaded: list[Path]  # every config tmux loads, in load order
    target: Path  # the file we write to (always the earliest-loaded)
    target_exists: bool
    is_symlink: bool
    symlink_target: Path | None
    installed: bool
    misplaced: list[Path] = field(default_factory=list)
    fragments: list[Path] = field(default_factory=list)

    @property
    def outranks_user(self) -> bool:
        """A block sits in a LATER-loaded file, so muxplex would beat the
        user's own settings. Wrong way round -- reinstalling relocates it."""
        return bool(self.misplaced)


# ── tmux probing ───────────────────────────────────────────────────────────


def tmux_version() -> tuple[int, int] | None:
    """(major, minor) of the tmux on PATH, or None if tmux is absent."""
    exe = shutil.which("tmux")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "-V"], capture_output=True, text=True, timeout=5, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+)\.(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else None


def candidate_config_paths() -> list[Path]:
    """Every user config tmux loads, in LOAD ORDER (earliest first)."""
    paths = [TMUX_CONF_PATH, XDG_TMUX_CONF_PATH]
    seen: set[Path] = set()
    return [p for p in paths if not (p in seen or seen.add(p))]


def install_target() -> Path:
    """Always the EARLIEST-loaded user config.

    Block-at-top-of-earliest-file is the entire safety property: muxplex
    applies first and loses to everything else. Do not "optimise" this to
    whichever file happens to already exist.
    """
    return TMUX_CONF_PATH


def loaded_configs() -> list[Path]:
    return [p for p in candidate_config_paths() if p.exists()]


def _has_block(path: Path) -> bool:
    try:
        return BEGIN_MARKER in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def status() -> Status:
    target = install_target()
    return Status(
        tmux_version=tmux_version(),
        loaded=loaded_configs(),
        target=target,
        target_exists=target.exists(),
        is_symlink=target.is_symlink(),
        symlink_target=target.resolve() if target.is_symlink() else None,
        installed=_has_block(target),
        misplaced=[p for p in loaded_configs() if p != target and _has_block(p)],
        fragments=sorted(TMUX_D_PATH.glob("*.conf")) if TMUX_D_PATH.is_dir() else [],
    )


# ── Fragments muxplex owns ─────────────────────────────────────────────────


def available_themes() -> list[str]:
    d = TEMPLATES_PATH / "themes"
    return sorted(p.stem for p in d.glob("*.conf")) if d.is_dir() else []


# Closed vocabulary for the copy-mode keybinding scheme -- see
# settings.DEFAULT_SETTINGS["tmux_copy_mode"] for the user-facing rationale.
# Never accept anything outside this set from an API caller; this is the
# entire security model for that field (see AGENTS.md's constrained-
# vocabulary discussion for `tmux_theme` -- the same reasoning applies here).
COPY_MODES: tuple[str, ...] = ("desktop", "vi")

_COPY_MODE_FRAGMENT_NAME = "30-copy-mode.conf"


def render_fragments(theme: str, copy_mode: str = "desktop") -> list[Path]:
    """Write muxplex's own fragments into ~/.config/muxplex/tmux.d/.

    muxplex owns that directory outright, so these are safe to overwrite on
    every run -- the user's edits live in 90-local.conf, which is created once
    and then never written again.

    *copy_mode* controls whether ``30-copy-mode.conf`` (vi-style copy-mode
    keybindings, see tmux_templates/copy-mode-vi.conf) is written. It loads
    after the base (10) and theme (20) fragments -- numeric order -- so it can
    override tmux's default mode-keys, and before the user's 90-local.conf so
    anything the user sets there still wins. ``"desktop"`` (tmux's own
    emacs-style default) means no fragment is needed; any stale
    30-copy-mode.conf from a previous "vi" selection is removed so switching
    back to desktop actually takes effect, not just stops being written.
    """
    theme_src = TEMPLATES_PATH / "themes" / f"{theme}.conf"
    if not theme_src.is_file():
        raise TmuxConfigError(
            f"Unknown tmux theme {theme!r}. Available: {', '.join(available_themes())}"
        )
    if copy_mode not in COPY_MODES:
        raise TmuxConfigError(
            f"Unknown tmux copy mode {copy_mode!r}. Available: {', '.join(COPY_MODES)}"
        )

    TMUX_D_PATH.mkdir(parents=True, exist_ok=True)
    written = []
    shutil.copy2(TEMPLATES_PATH / "base.conf", TMUX_D_PATH / "10-muxplex-base.conf")
    written.append(TMUX_D_PATH / "10-muxplex-base.conf")
    shutil.copy2(theme_src, TMUX_D_PATH / "20-theme.conf")
    written.append(TMUX_D_PATH / "20-theme.conf")

    copy_mode_dest = TMUX_D_PATH / _COPY_MODE_FRAGMENT_NAME
    if copy_mode == "vi":
        shutil.copy2(TEMPLATES_PATH / "copy-mode-vi.conf", copy_mode_dest)
        written.append(copy_mode_dest)
    else:
        # Remove a stale fragment left over from a previous "vi" selection --
        # otherwise switching back to "desktop" would silently keep loading
        # vi keybindings from disk even though the setting says otherwise.
        copy_mode_dest.unlink(missing_ok=True)

    local = TMUX_D_PATH / "90-local.conf"
    if not local.exists():
        local.write_text(_LOCAL_FRAGMENT_HEADER, encoding="utf-8")
        written.append(local)
    return written


def _directives_only(text: str) -> str:
    """Strip comments and blank runs, leaving just the settings themselves.

    The template files are heavily commented on purpose -- they explain WHY each
    setting exists to whoever opens the file. That is the wrong content for a UI
    preview: measured on the shipped default, 116 of 162 lines are comment or
    blank, so 71% of what a reader scrolls past answers a question they did not
    ask. Someone opening "Show the generated config" wants to see what it does,
    not read an essay. The file on disk keeps every comment.
    """
    kept = [
        ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
    ]
    return "\n".join(kept) + "\n"


def render_preview(theme: str, copy_mode: str = "desktop") -> str:
    """Return the concatenated text muxplex WOULD render for *theme* and
    *copy_mode*, without writing anything to disk.

    A pure read (unlike render_fragments(), which writes to TMUX_D_PATH) --
    used by GET /api/tmux-config so a caller can preview a configuration
    without side effects. Mirrors render_fragments()'s fragment order (base +
    theme + copy-mode-vi when applicable) but reads straight from
    TEMPLATES_PATH. Deliberately excludes 90-local.conf: that is the user's
    own file, never something muxplex renders.
    """
    theme_src = TEMPLATES_PATH / "themes" / f"{theme}.conf"
    if not theme_src.is_file():
        raise TmuxConfigError(
            f"Unknown tmux theme {theme!r}. Available: {', '.join(available_themes())}"
        )
    if copy_mode not in COPY_MODES:
        raise TmuxConfigError(
            f"Unknown tmux copy mode {copy_mode!r}. Available: {', '.join(COPY_MODES)}"
        )

    parts = [
        (TEMPLATES_PATH / "base.conf").read_text(encoding="utf-8"),
        theme_src.read_text(encoding="utf-8"),
    ]
    if copy_mode == "vi":
        parts.append((TEMPLATES_PATH / "copy-mode-vi.conf").read_text(encoding="utf-8"))
    return _directives_only("\n".join(parts))


# ── Write helpers ──────────────────────────────────────────────────────────


def _atomic_write(path: Path, content: str) -> None:
    """tmp + os.replace, per state.py/manifest.py (NOT the non-atomic
    settings.py pattern).

    Symlinks are resolved FIRST: os.replace() onto a symlink path would replace
    the symlink itself with a regular file, silently detaching a dotfiles repo.
    """
    if path.is_symlink():
        path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".muxplex-tmp-{os.getpid()}")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _backup(path: Path) -> Path:
    if path.is_symlink():
        path = path.resolve()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"{path.name}.muxplex-backup.{stamp}")
    n = 0
    while dest.exists():  # never clobber an existing backup
        n += 1
        dest = path.with_name(f"{path.name}.muxplex-backup.{stamp}.{n}")
    shutil.copy2(path, dest)
    return dest


def render_block(existing: str | None) -> str:
    """New file content: block at TOP, everything else preserved verbatim."""
    if existing is None:
        return BLOCK_BODY + "\n"
    stripped = _BLOCK_RE.sub("", existing).lstrip("\n")  # idempotent
    if stripped and not stripped.endswith("\n"):
        stripped += "\n"  # tolerate a file with no trailing newline
    return BLOCK_BODY + "\n" if not stripped else BLOCK_BODY + "\n\n" + stripped


def diff_preview(existing: str | None, updated: str) -> str:
    return "".join(
        difflib.unified_diff(
            (existing or "").splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile="before",
            tofile="after",
            n=2,
        )
    )


# ── Install / uninstall ────────────────────────────────────────────────────


def install(
    *,
    theme: str = "brand",
    copy_mode: str = "desktop",
    allow_symlink: bool = False,
    dry_run: bool = False,
) -> dict:
    """Render fragments and add the managed block. Raises on anything unclear."""
    st = status()
    if st.tmux_version is None:
        raise TmuxConfigError("tmux not found on PATH. Install tmux first.")
    if st.tmux_version < MIN_TMUX_VERSION:
        raise TmuxConfigError(
            f"tmux {st.tmux_version[0]}.{st.tmux_version[1]} is too old; "
            f"source-file globbing needs >= {MIN_TMUX_VERSION[0]}.{MIN_TMUX_VERSION[1]}."
        )

    target = st.target
    if st.is_symlink and not allow_symlink:
        raise TmuxConfigError(
            f"{target} is a symlink to {st.symlink_target}.\n"
            f"Writing would modify that file -- often a tracked dotfiles repo.\n"
            f"Re-run with --allow-symlink to proceed."
        )

    existing = target.read_text(encoding="utf-8") if target.exists() else None
    updated = render_block(existing)
    result = {
        "target": str(target),
        "theme": theme,
        "created": existing is None,
        "changed": updated != existing,
        "backup": None,
        "diff": diff_preview(existing, updated),
        "fragments": [],
    }

    if dry_run:
        result["fragments"] = [str(p) for p in (TEMPLATES_PATH,)]
        return result

    result["fragments"] = [str(p) for p in render_fragments(theme, copy_mode)]

    if not result["changed"]:
        return result

    if existing is not None:
        result["backup"] = str(_backup(target))
    _atomic_write(target, updated)

    # Assert the write landed; do not assume it.
    after = target.read_text(encoding="utf-8")
    if BEGIN_MARKER not in after or SOURCE_LINE not in after:
        raise TmuxConfigError(
            f"Wrote {target} but the block is not present on re-read."
        )
    if (
        existing is not None
        and _BLOCK_RE.sub("", after).strip() != _BLOCK_RE.sub("", existing).strip()
    ):
        raise TmuxConfigError(
            f"Wrote {target} but pre-existing content changed. "
            f"Restore from {result['backup']} and report this."
        )
    return result


def uninstall(*, allow_symlink: bool = False) -> dict:
    """Remove exactly the managed block. Touch nothing else. Keep backups."""
    targets = [p for p in candidate_config_paths() if p.exists() and _has_block(p)]
    if not targets:
        return {"removed_from": [], "changed": False}

    removed: list[str] = []
    for target in targets:
        if target.is_symlink() and not allow_symlink:
            raise TmuxConfigError(
                f"{target} is a symlink to {target.resolve()}; refusing to edit. "
                f"Re-run with --allow-symlink."
            )
        existing = target.read_text(encoding="utf-8")
        _backup(target)
        _atomic_write(target, _BLOCK_RE.sub("", existing).lstrip("\n"))
        if BEGIN_MARKER in target.read_text(encoding="utf-8"):
            raise TmuxConfigError(f"Failed to remove block from {target}.")
        removed.append(str(target))
    return {"removed_from": removed, "changed": True}


# ── Live apply + verification ──────────────────────────────────────────────


def apply_live(socket: str | None = None) -> dict:
    """Source the fragments into the RUNNING tmux server.

    Makes the change visible immediately instead of "restart tmux and hope".
    No running server is not an error -- there is nothing to apply to.
    """
    exe = shutil.which("tmux")
    if not exe:
        raise TmuxConfigError("tmux not found on PATH.")
    base = [exe] + (["-L", socket] if socket else [])
    if (
        subprocess.run(
            [*base, "list-sessions"], capture_output=True, text=True, check=False
        ).returncode
        != 0
    ):
        return {"applied": False, "reason": "no running tmux server"}
    r = subprocess.run(
        [*base, "source-file", "-q", str(TMUX_D_PATH / "*.conf")],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        raise TmuxConfigError(
            f"tmux source-file failed: {r.stderr.strip() or r.stdout.strip()}"
        )
    return {"applied": True, "reason": None}


def verify(socket: str = "muxplex-verify") -> dict:
    """Prove it: start a throwaway tmux server against the real config on a
    PRIVATE socket and confirm a muxplex-set option is actually in effect.

    A private socket means the user's live sessions are never touched.
    """
    exe = shutil.which("tmux")
    if not exe:
        raise TmuxConfigError("tmux not found on PATH.")
    env = {**os.environ, "HOME": str(TMUX_CONF_PATH.parent)}
    env.pop("TMUX", None)
    subprocess.run(
        [exe, "-L", socket, "kill-server"], capture_output=True, env=env, check=False
    )
    try:
        start = subprocess.run(
            [exe, "-L", socket, "new-session", "-d"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        if start.returncode != 0:
            raise TmuxConfigError(
                f"tmux refused to start with the current config: "
                f"{start.stderr.strip() or start.stdout.strip()}"
            )
        sentinel = subprocess.run(
            [exe, "-L", socket, "show-options", "-gv", "@muxplex_loaded"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        ).stdout.strip()
        return {
            "loaded": sentinel != "",
            "sentinel": sentinel,
            "stderr": start.stderr.strip(),
        }
    finally:
        subprocess.run(
            [exe, "-L", socket, "kill-server"],
            capture_output=True,
            env=env,
            check=False,
        )
