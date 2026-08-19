"""muxplex CLI — web-based tmux session dashboard."""

import argparse
import logging
import os
import platform
import secrets as _secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

from muxplex.auth import (
    get_password_path,
    get_secret_path,
    load_password,
    pam_available,
)

# Module-level path constants (overridable in tests via monkeypatch)
_system_service_path = Path("/etc/systemd/system/muxplex.service")


def _have_systemctl() -> bool:
    """Return True if systemctl is on PATH (used to gate service-management steps)."""
    return shutil.which("systemctl") is not None


def _have_launchctl() -> bool:
    """Return True if launchctl is on PATH (used to gate service-management steps)."""
    return shutil.which("launchctl") is not None


def _probe_service_port(port: int) -> bool:
    """Return True if a muxplex server is responding on localhost:port.

    Tries HTTPS first (self-signed cert tolerated), then HTTP.  Any HTTP
    response code (including 4xx/5xx) confirms the server is listening.
    A connection error, timeout, or SSL failure means the server is not up.
    """
    import ssl
    import urllib.error
    import urllib.request

    for scheme in ("https", "http"):
        try:
            url = f"{scheme}://localhost:{port}/login"
            if scheme == "https":
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                with urllib.request.urlopen(url, timeout=5, context=ctx) as _resp:
                    return True
            else:
                with urllib.request.urlopen(url, timeout=5) as _resp:
                    return True
        except urllib.error.HTTPError:
            # Server returned an HTTP error — it IS running
            return True
        except Exception:
            pass  # Connection refused, timeout, SSL issue — try next scheme
    return False


def _verify_service_started(timeout_s: int = 10) -> bool:
    """Verify the muxplex service is actually serving after a start command.

    For systemctl: calls ``systemctl --user is-active muxplex`` once and
    returns ``True`` only when the unit is ``active`` (exit code 0).
    ``systemctl start`` is synchronous so a single check is sufficient.

    For launchctl: polls ``_probe_service_port()`` until a successful HTTP
    response is received or ``timeout_s`` seconds have elapsed.  launchd
    starts processes asynchronously, so polling is necessary.

    Returns ``False`` if the service is not active / not responding.
    """
    import time

    if _have_systemctl():
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "muxplex"],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    if _have_launchctl():
        from muxplex.settings import load_settings

        cfg = load_settings()
        port = cfg.get("port", 8088)
        deadline = time.monotonic() + timeout_s
        while True:
            if _probe_service_port(port):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(1.0, remaining))

    return False


def _wait_for_service_ready(port: int, timeout_s: float = 10.0) -> bool:
    """Poll until the muxplex API answers on *port*, or *timeout_s* elapses.

    ``_verify_service_started`` (above) confirms the *process* is running --
    for systemd that's a single ``systemctl is-active`` check, true the
    instant the unit starts, well before uvicorn has finished loading
    settings and binding the configured host:port. Calling ``doctor()``
    immediately in that gap races a server that is actually healthy, just not
    listening yet, and its "Running:" check reports a false "not serving"
    warning that a manual `muxplex doctor` moments later would not show.

    Poll on a short interval with a generous ceiling instead of guessing a
    flat delay -- same shape as the eventually-consistent read model in
    ``docs/AGENT_GUIDE.md`` Sec 4 ("poll on a short interval, not a long
    sleep"). Uses ``_fetch_local_instance_info``, the exact probe ``doctor``
    itself uses for its "Running:" line, so once this returns True, doctor's
    own check is guaranteed to observe the same live server rather than a
    differently-timed probe.

    Returns True the moment the API responds. Returns False once the ceiling
    elapses without a response -- a real failure to report honestly, not one
    to sleep-and-hope past.
    """
    import time

    deadline = time.monotonic() + timeout_s
    while True:
        if _fetch_local_instance_info(port) is not None:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.5, remaining))


def _find_uv() -> str | None:
    """Locate the ``uv`` binary, checking PATH first then well-known install locations.

    ``shutil.which("uv")`` fails on systems where the muxplex process inherits a
    stripped PATH (e.g. under systemd/launchd or non-login SSH shells) that does not
    include ``~/.local/bin`` or ``/snap/bin``.  This helper falls back to a curated
    list of locations observed in the wild:

    * ``~/.local/bin/uv``       — pip-style user installs (Linux, macOS)
    * ``/opt/homebrew/bin/uv``  — Homebrew on Apple Silicon
    * ``/usr/local/bin/uv``     — Homebrew on Intel macOS, manual installs
    * ``/snap/bin/uv``          — snap-packaged uv (Ubuntu / snap-enabled distros)
    * ``/root/.local/bin/uv``   — root user on Unraid / headless Linux

    Returns the first found path, or ``None`` if uv is not available.
    """
    found = shutil.which("uv")
    if found:
        return found
    candidates = [
        str(Path.home() / ".local" / "bin" / "uv"),
        "/opt/homebrew/bin/uv",
        "/usr/local/bin/uv",
        "/snap/bin/uv",
        "/root/.local/bin/uv",
    ]
    for path in candidates:
        if os.path.exists(path) and os.access(path, os.X_OK):
            return path
    return None


def _find_pip() -> str | None:
    """Locate a ``pip`` / ``pip3`` binary, checking PATH first then well-known locations.

    Mirrors ``_find_uv()``'s strategy: try ``shutil.which`` for ``pip`` and ``pip3``,
    then probe a curated list of known install paths so that pip can be found even
    when the process PATH is stripped.

    Returns the first found path, or ``None`` if no pip variant is available.
    """
    for name in ("pip", "pip3"):
        found = shutil.which(name)
        if found:
            return found
    candidates = [
        str(Path.home() / ".local" / "bin" / "pip"),
        str(Path.home() / ".local" / "bin" / "pip3"),
        "/opt/homebrew/bin/pip3",
        "/usr/local/bin/pip3",
        "/root/.local/bin/pip",
        "/root/.local/bin/pip3",
    ]
    for path in candidates:
        if os.path.exists(path) and os.access(path, os.X_OK):
            return path
    return None


def _get_install_info(dist_name: str = "muxplex") -> dict:
    """Detect how `dist_name` was installed using PEP 610 direct_url.json.

    This is the single source of truth `upgrade`/`doctor` use to decide what
    to reinstall -- never guess or substitute a different origin than what's
    recorded here. `direct_url.json` records exactly how pip/uv resolved the
    install; the five shapes it can take (verified against real installs of
    each):

      absent                          -> pypi
      vcs_info                        -> git       (a git remote + commit,
                                                     optionally pinned to a
                                                     tag/branch/ref)
      dir_info + editable=True        -> editable   (`pip install -e .`)
      dir_info (no editable)          -> local-dir  (`pip install /some/dir`)
      archive_info                    -> archive    (a local wheel/sdist file)

    Anything else is 'unknown' -- should not happen in practice, and is
    treated conservatively (never auto-upgraded) everywhere it's checked.

    `dist_name` generalizes this beyond muxplex itself (originally
    hardcoded) so the identical machinery reports on any first-party
    dependency's own install provenance -- see
    docs/plans/2026-08-09-tmuxkit-own-repo-and-pypi-plan.md §2.4.
    `importlib.metadata` normalizes names per PEP 503, so
    `_get_install_info("tmux-kit")` correctly finds a `tmux_kit-*.dist-info`
    directory. The default keeps every existing caller (which all ask about
    muxplex itself) unchanged.

    Returns dict with keys:
      source: 'pypi' | 'git' | 'editable' | 'local-dir' | 'archive'
              | 'not-installed' | 'unknown'
      version: installed version string
      commit: installed commit sha (git only, may be '')
      url: origin verbatim from direct_url.json -- a git remote URL for
           'git', a file:// URL for 'editable'/'local-dir'/'archive'; None
           for 'pypi'/'not-installed'/'unknown'
      ref: the exact ref requested at install time, i.e. PEP 610's
           vcs_info.requested_revision (git only) -- None if no ref was
           given (installed off the default branch), or for any non-git
           source. This is the pin `upgrade` must preserve; discarding it is
           what let `muxplex upgrade` silently move a tag-pinned install onto
           an unreleased default branch.
    """
    import json
    from importlib.metadata import PackageNotFoundError, distribution

    info: dict = {
        "source": "unknown",
        "version": "0.0.0",
        "commit": None,
        "url": None,
        "ref": None,
    }

    try:
        dist = distribution(dist_name)
        info["version"] = dist.metadata["Version"]

        du_text = dist.read_text("direct_url.json")
        if du_text:
            du = json.loads(du_text)

            if "vcs_info" in du:
                info["source"] = "git"
                info["commit"] = du["vcs_info"].get("commit_id", "")
                info["url"] = du.get("url", "")
                info["ref"] = du["vcs_info"].get("requested_revision") or None
            elif "dir_info" in du and du["dir_info"].get("editable"):
                info["source"] = "editable"
                info["url"] = du.get("url", "")
            elif "dir_info" in du:
                info["source"] = "local-dir"
                info["url"] = du.get("url", "")
            elif "archive_info" in du:
                info["source"] = "archive"
                info["url"] = du.get("url", "")
            else:
                info["source"] = "unknown"
        else:
            # No direct_url.json → PyPI
            info["source"] = "pypi"
    except PackageNotFoundError:
        # Distinct from "unknown" (an installed-but-unrecognized direct_url.json
        # shape): this dist isn't installed in this environment at all.
        info["source"] = "not-installed"

    return info


def _file_url_to_path(url: str) -> Path | None:
    """Convert a file:// URL (as PEP 610 records for editable/local-dir/archive
    installs) to a filesystem Path. Returns None if `url` isn't a file:// URL
    (or is empty) -- e.g. an archive installed straight from an http(s) URL.
    """
    from urllib.parse import unquote, urlparse

    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme != "file":
        return None
    return Path(unquote(parsed.path))


def _latest_tag(tag_names) -> str:
    """Pick the highest-version tag from a collection of tag name strings.

    Tolerant of a leading 'v' (muxplex tags are `v*`, per AGENTS.md's release
    section) and non-numeric trailing suffixes -- any non-numeric segment
    sorts as 0 rather than crashing the comparison, so an odd/malformed tag
    just sorts low instead of blowing up the whole check.
    """

    def version_key(tag: str) -> tuple[int, ...]:
        stripped = tag[1:] if tag[:1] in ("v", "V") else tag
        parts = []
        for chunk in stripped.split("."):
            digits = "".join(ch for ch in chunk if ch.isdigit())
            parts.append(int(digits) if digits else 0)
        return tuple(parts)

    return max(tag_names, key=version_key)


def _git_ref_kind_and_target(
    url: str, requested_revision: str | None
) -> tuple[str, str | None, str | None]:
    """Classify a git install's pinned ref and resolve what to track.

    Ref-kind semantics (preserve installer intent -- see upgrade-source-
    awareness design):
      pinned to a tag    -> track the latest tag (not branch HEAD)
      pinned to a branch -> track that branch's HEAD
      no ref recorded    -> track the default branch HEAD
      anything else (an exact commit sha, or a ref that no longer exists on
      the remote) -> treat as an exact pin; it never moves on its own.

    Returns (kind, target_ref, error):
      kind='default' -- no ref was requested; target_ref=None (caller tracks
                         the remote's default branch HEAD, unchanged from the
                         original behavior)
      kind='tag'     -- target_ref is the latest tag on the remote (may equal
                         requested_revision if already current)
      kind='branch'  -- target_ref is requested_revision itself (installing
                         '@branch' always tracks that branch's live HEAD)
      kind='commit'  -- target_ref is requested_revision itself, unchanged
      kind='error'   -- could not query the remote; `error` explains why
    """
    if not requested_revision:
        return "default", None, None

    try:
        tags_result = subprocess.run(
            ["git", "ls-remote", "--tags", url],
            capture_output=True,
            text=True,
            timeout=10,
        )
        heads_result = subprocess.run(
            ["git", "ls-remote", "--heads", url],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        return "error", None, f"could not query remote refs: {exc}"

    if tags_result.returncode != 0 or heads_result.returncode != 0:
        return "error", None, "could not query remote refs"

    tag_names: set[str] = set()
    for line in tags_result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        ref = parts[1].removesuffix("^{}")
        if ref.startswith("refs/tags/"):
            tag_names.add(ref[len("refs/tags/") :])

    branch_names: set[str] = set()
    for line in heads_result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        ref = parts[1]
        if ref.startswith("refs/heads/"):
            branch_names.add(ref[len("refs/heads/") :])

    if requested_revision in tag_names:
        return "tag", _latest_tag(tag_names), None
    if requested_revision in branch_names:
        return "branch", requested_revision, None
    return "commit", requested_revision, None


def _provenance_label(info: dict) -> str:
    """Human-readable "where this came from" line for `doctor`.

    Purely informational -- running from git, a local checkout, or an
    archive is a legitimate, deliberate choice, not something to warn about.
    See doctor()'s update-check line for the one case that DOES warn
    (updates not checkable from this source).
    """
    source = info["source"]

    if source == "pypi":
        return "PyPI"

    if source == "git":
        url = info.get("url") or "?"
        label = f"git+{url}"
        if info.get("ref"):
            label += f" @ {info['ref']}"
        if info.get("commit"):
            label += f" ({info['commit'][:8]})"
        return label

    if source == "editable":
        path = _file_url_to_path(info.get("url") or "")
        return f"editable checkout at {path}" if path else "editable checkout"

    if source == "local-dir":
        path = _file_url_to_path(info.get("url") or "")
        return f"local directory at {path}" if path else "local directory"

    if source == "not-installed":
        return "not installed"

    if source == "archive":
        url = info.get("url") or "?"
        path = _file_url_to_path(url)
        return f"local archive at {path}" if path else f"archive at {url}"

    return "unrecognized install record"


def _declared_dependency_pin(dep_name: str, dist_name: str = "muxplex") -> str | None:
    """Read the exact `==` version pin for `dep_name` from `dist_name`'s own
    published metadata (`Requires-Dist`, exposed by importlib.metadata as
    `Distribution.requires`).

    Used by `doctor` to detect a tmux-kit install that has drifted from what
    muxplex itself declares it needs (docs/plans/2026-08-09-tmuxkit-own-repo-
    and-pypi-plan.md §2.4's pin-vs-installed warning) -- e.g. the venv was
    modified by hand outside `muxplex upgrade`.

    Returns the pinned version string (e.g. "0.1.0") if `dep_name` is
    declared with an exact `==` pin, else None -- `dist_name` not installed,
    `dep_name` not among its requirements, or declared with something other
    than an exact pin (a range, no version at all, etc.). None means "can't
    compare", never "matches".
    """
    import re
    from importlib.metadata import PackageNotFoundError, distribution

    try:
        dist = distribution(dist_name)
    except PackageNotFoundError:
        return None

    requires = dist.requires or []
    target = dep_name.strip().lower().replace("_", "-")

    for requirement in requires:
        # Requirement strings look like "tmux-kit==0.1.0" or
        # "fastapi>=0.115.0" or "beautifulsoup4>=4.12 ; extra == 'dev'".
        # The name is everything before the first version/marker/extras
        # delimiter.
        name_part = re.split(r"[<>=!~ ;\[]", requirement, maxsplit=1)[0].strip()
        if name_part.lower().replace("_", "-") != target:
            continue
        match = re.search(r"==\s*([0-9][A-Za-z0-9.\-]*)", requirement)
        return match.group(1) if match else None

    return None


def _installed_version_on_disk() -> str | None:
    """Read muxplex's installed version straight off the filesystem.

    NOT importlib.metadata: it resolves and caches at import time, and this
    process IS the old build -- after an upgrade it still reports the version we
    started on, no matter what just landed on disk. That is the same
    ask-the-thing-that-cannot-know blind spot one level up from the bug this
    guards, so it cannot be used to check the guard.

    Reading the dist-info directory name instead reflects what is on disk right
    now. Deliberately no subprocess: shelling out to a fresh interpreter would
    also work, but it is slower, drags in PATH and interpreter-resolution
    failure modes, and makes every test that stubs subprocess.run interact with
    version checking for no reason.

    Returns None if it cannot be determined, which callers MUST treat as
    "unknown" -- never as "fine".
    """
    import sysconfig

    seen = set()
    for key in ("purelib", "platlib"):
        directory = sysconfig.get_paths().get(key)
        if not directory or directory in seen:
            continue
        seen.add(directory)
        try:
            for info in Path(directory).glob("muxplex-*.dist-info"):
                version = info.name[len("muxplex-") : -len(".dist-info")]
                if version:
                    return version
        except OSError:
            continue
    return None


def _check_for_update(info: dict) -> tuple[bool, str]:
    """Check if an update is available. Returns (update_available, message).

    Every source is handled explicitly -- there is no catch-all "can't tell,
    so upgrade anyway" branch. When a real check can't be performed (missing
    ref info, network failure, an install source we don't recognize), the
    message says so and is prefixed 'not checkable', and update_available is
    always False: claiming an update is available without having actually
    checked is exactly what turned `muxplex upgrade` into a way to silently
    replace a working install (a tag-pinned git install compared against
    default-branch HEAD; an unrecognized source defaulting to "upgrade to be
    safe" and overwriting a local build). Doctor surfaces 'not checkable'
    messages as a warning (nothing is watching the version) -- never as an
    "update available" nudge to run upgrade.

    For git: no ref recorded -> compares installed commit against the
    remote's default-branch HEAD (unchanged from the original behavior).
    Pinned to a tag -> compares against the latest tag. Pinned to a branch
    -> compares against that branch's HEAD. Pinned to anything else (an
    exact commit sha, most likely) -> not checkable; an exact pin has no
    "latest".
    For pypi: compares installed version against latest PyPI version.
    For editable/local-dir/archive: never checkable -- these are installs
    the user manages directly; see _upgrade_target for what `upgrade` does
    with each.
    """
    import json
    import urllib.request

    source = info["source"]

    if source == "editable":
        return False, "editable install — manage updates manually"

    if source == "git":
        url = info.get("url") or ""
        requested = info.get("ref")
        local_commit = info.get("commit") or ""

        if not requested:
            # No ref recorded -> track the remote's default branch HEAD,
            # exactly as before this fix.
            try:
                result = subprocess.run(
                    ["git", "ls-remote", url, "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except Exception:
                return False, "not checkable — could not query remote"
            if result.returncode != 0:
                return False, "not checkable — could not query remote"

            remote_sha = (
                result.stdout.strip().split()[0] if result.stdout.strip() else ""
            )
            if not remote_sha:
                return False, "not checkable — remote returned no HEAD sha"

            if local_commit == remote_sha:
                return False, f"up to date (commit {local_commit[:8]})"
            return True, f"update available ({local_commit[:8]} → {remote_sha[:8]})"

        kind, target_ref, err = _git_ref_kind_and_target(url, requested)
        if kind == "error":
            return False, f"not checkable — {err}"

        if kind == "tag":
            if target_ref == requested:
                return False, f"up to date ({requested} — latest tag)"
            return True, f"update available ({requested} → {target_ref})"

        if kind == "branch":
            try:
                result = subprocess.run(
                    ["git", "ls-remote", url, f"refs/heads/{requested}"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except Exception:
                return False, "not checkable — could not query remote"
            if result.returncode != 0 or not result.stdout.strip():
                return False, "not checkable — could not query remote"
            remote_sha = result.stdout.strip().split()[0]
            if local_commit == remote_sha:
                return False, f"up to date (branch {requested} @ {local_commit[:8]})"
            return (
                True,
                "update available"
                f" (branch {requested}: {local_commit[:8]} → {remote_sha[:8]})",
            )

        # kind == "commit": pinned to an exact ref that isn't a known tag or
        # branch (a raw commit sha, most likely) -- there's no "latest" for
        # an exact pin, so there's nothing to check.
        return False, f"not checkable — pinned to exact ref {requested}"

    if source == "pypi":
        try:
            req = urllib.request.Request(
                "https://pypi.org/pypi/muxplex/json",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                latest = data["info"]["version"]
                if latest == info["version"]:
                    return False, f"up to date (v{info['version']})"
                return True, f"update available (v{info['version']} → v{latest})"
        except Exception:
            return False, "not checkable — could not reach PyPI"

    if source == "local-dir":
        return False, "not checkable — local directory install"

    if source == "archive":
        return False, "not checkable — local archive install"

    # Unrecognized install record. Never true here -- surfacing an honest
    # "can't tell" instead of the old "unknown -> upgrade to be safe", which
    # is exactly what silently overwrote installs _get_install_info couldn't
    # classify.
    return False, "not checkable — install source not recognized"


def _upgrade_target(info: dict) -> tuple[str | None, str | None]:
    """Decide the exact string to hand the installer, derived STRICTLY from
    the recorded install source (never guessed, never substituted).

    Returns (target, refuse_reason) -- exactly one is not None:
      target set         -- install exactly this (a pip/uv requirement spec,
                             a local directory path, or an archive path/URL).
      refuse_reason set  -- do not install automatically; this explains why,
                             for the caller to print and stop.

    This is the fix for the defect where `upgrade` would silently convert a
    git install to the PyPI package (because it happened to also be
    uv-managed), or reinstall canonical upstream over a local build it
    couldn't classify. `target`'s shape always matches `info["source"]` by
    construction -- `_target_matches_source` re-checks this mechanically
    right before install, as a second line of defense.
    """
    source = info["source"]

    if source == "pypi":
        return "muxplex", None

    if source == "git":
        url = info.get("url") or ""
        if not url:
            return None, "git install has no recorded remote URL"
        requested = info.get("ref")
        kind, target_ref, err = _git_ref_kind_and_target(url, requested)
        if kind == "error":
            return None, f"could not resolve git ref to upgrade to: {err}"
        if target_ref:
            return f"git+{url}@{target_ref}", None
        return f"git+{url}", None

    if source == "editable":
        return (
            None,
            "editable install — manage it yourself (e.g. git pull in the checkout)",
        )

    if source == "local-dir":
        path = _file_url_to_path(info.get("url") or "")
        if path is None:
            return None, "local directory install has no recorded path"
        if not path.exists():
            return None, f"original directory no longer exists ({path})"
        return str(path), None

    if source == "archive":
        url = info.get("url") or ""
        path = _file_url_to_path(url)
        if path is not None and not path.exists():
            return None, f"original archive no longer exists ({path})"
        return url, None

    return (
        None,
        "install source not recognized — reinstall manually with the method"
        " you used originally",
    )


def _target_matches_source(info: dict, target: str) -> bool:
    """Defense-in-depth: confirm `target`'s shape actually matches the
    recorded install source, right before it's handed to the installer.

    `_upgrade_target` is built to derive `target` strictly from `info`, so
    this should always be True. It exists to mechanically catch a future
    regression that reintroduces a hardcoded or substituted target (this is
    precisely how the git-install-silently-becomes-PyPI defect happened),
    rather than relying solely on `_upgrade_target` staying correct forever.
    """
    source = info["source"]
    if source == "pypi":
        return target == "muxplex"
    if source == "git":
        return target.startswith("git+") and (info.get("url") or "") in target
    if source in ("local-dir", "archive"):
        expected = info.get("url") or ""
        expected_path = _file_url_to_path(expected)
        return target == expected or (
            expected_path is not None and target == str(expected_path)
        )
    return False


# ---------------------------------------------------------------------------
# amplifier-agent bootstrap ("ensure-agent")
# ---------------------------------------------------------------------------
#
# amplifier-agent (muxplex/agent_embedded/) is git-only -- see pyproject.toml's
# `agent` extra + [tool.uv.sources] entry. Those two mechanisms cover a git
# CHECKOUT of muxplex (`uv sync --extra agent` / `uv lock`, which honor
# [tool.uv.sources] for the project actually being resolved) but NOT a
# `uv tool install` of muxplex from EITHER source: [tool.uv.sources] never
# enters a published wheel's Requires-Dist (the same rule proven for
# tmux-kit -- see AGENTS.md's "tmux-kit pin/tag agreement" section), and
# `agent` is an OPTIONAL extra that a bare `uv tool install muxplex` (or
# `uv tool install git+.../muxplex`, no extras selected) never resolves on
# its own. So neither a PyPI install nor a plain git tool-install gets
# amplifier-agent today -- regardless of which source produced THIS
# muxplex, that is the gap `ensure_agent()` closes.
#
# Verified empirically (2026-08-16, this design's own load-bearing spike, on
# a clean box with zero amplifier packages pre-cached):
#
#   uv tool install muxplex \
#     --with 'amplifier-agent @ git+https://github.com/microsoft/amplifier-agent@v0.12.0'
#
# resolves amplifier-agent's ENTIRE transitive tree with NO extra --with
# flags needed: amplifier-core comes from PyPI (published there for real, by
# Microsoft), and amplifier-foundation comes from git -- amplifier-agent's
# OWN [tool.uv.sources] entry for it is honored by uv even though it is a
# transitive dependency pulled in via someone else's --with, and even
# against a PyPI-target install whose own wheel metadata carries no
# [tool.uv.sources] at all.
#
# Unlike tmux-kit's --with override (a base/required dependency, so a git
# muxplex target's OWN [tool.uv.sources] entry for it is ALWAYS already in
# play, and adding --with on top gives uv two url-bearing origins for the
# identical package -- see _install_cmd_preserves_kit_override's docstring
# for the v0.47.11 incident that taught us this), amplifier-agent is an
# OPTIONAL extra that a bare install target (git or PyPI) never resolves on
# its own. So --with is safe to add UNCONDITIONALLY here, regardless of
# whether muxplex's own install source is git or PyPI -- verified by
# reproducing the exact git-muxplex + --with-agent combination in isolation
# (a scratch UV_TOOL_DIR against a local git+file:// source): no
# "conflicting URLs" error, because muxplex's own project resolution never
# creates an amplifier-agent requirement in the first place unless the
# `agent` extra is explicitly selected on the target -- which ensure_agent()
# never does (it always uses --with, never `muxplex[agent]`).

_AGENT_DIST_NAME = "amplifier-agent"
_AGENT_REPO_URL = "https://github.com/microsoft/amplifier-agent"
# Backstop only -- _agent_target_pin() prefers the pin actually declared by
# THIS install's own metadata (via _declared_dependency_pin), which is
# always correct for the code that is actually running (see ensure_agent's
# docstring for why both of its call sites see the right pin there). This
# constant is read only when that lookup comes back empty (e.g. corrupted or
# unreadable dist-info metadata) -- keep it equal to the `agent` extra's pin
# in pyproject.toml; test_amplifier_agent_pin_source_agreement.py fails the
# suite if the two drift.
_AGENT_FALLBACK_PIN = "0.12.0"


def _agent_python_supported() -> bool:
    """Return True if THIS interpreter meets amplifier-agent's Python floor.

    amplifier-agent requires Python >=3.12 at every released version
    (v0.9.0 through v0.13.0) -- muxplex's own floor is only >=3.11
    (`pyproject.toml`'s `requires-python`). The `agent` extra already
    encodes this with a `python_version>='3.12'` marker
    (`pyproject.toml`'s `agent` extra); this function must encode the
    SAME constraint, not a second hand-maintained copy of it --
    `test_amplifier_agent_pin_source_agreement.py` asserts the two never
    drift apart, the same discipline as the existing pin-agreement check.

    Every entry point that might invoke amplifier-agent tooling on an
    unsupported interpreter -- `ensure_agent()` and `doctor()`'s agent
    block -- consults this FIRST, so the uv resolver is never handed a
    requirement (`amplifier-agent==X; python_version>='3.12'`) it cannot
    possibly satisfy on Python 3.11. See `ensure_agent()`'s docstring for
    why that matters: on 3.11 the resolver would otherwise dump a raw
    "unsatisfiable" traceback instead of a clear explanation.
    """
    return sys.version_info >= (3, 12)


def _agent_target_pin() -> str:
    """Return the amplifier-agent version THIS install of muxplex declares
    (via its own `agent` extra's `==` pin), falling back to
    `_AGENT_FALLBACK_PIN` only if that metadata can't be read.
    """
    return _declared_dependency_pin(_AGENT_DIST_NAME) or _AGENT_FALLBACK_PIN


def _agent_import_probe() -> tuple[str | None, str | None]:
    """Try to import amplifier_agent_lib fresh and report its version.

    Returns (version, error) -- exactly one is None. `importlib.invalidate_caches()`
    first so a package just installed by a PRIOR call in this same process (or
    by a subprocess that just wrote new dist-info next to this interpreter's
    site-packages) is actually seen -- see `_verify_install_shape_preserved`'s
    docstring for the identical importlib-caching gotcha.
    """
    import importlib

    importlib.invalidate_caches()
    try:
        module = importlib.import_module("amplifier_agent_lib")
    except ImportError as exc:
        return None, str(exc)
    version = getattr(module, "__version__", None)
    if not version:
        return None, "amplifier_agent_lib has no __version__ attribute"
    return version, None


# ---------------------------------------------------------------------------
# amplifier-agent PROVIDER bootstrap ("the muxplex-fx2 gap")
# ---------------------------------------------------------------------------
#
# 2026-08 incident: `ensure_agent()` above reported success fleet-wide (every
# device had `amplifier_agent_lib` importable at the pinned version) while the
# embedded chat panel was completely dead on every one of them -- every real
# turn failed with "No module named 'anthropic'". Root cause: `uv tool install
# muxplex --with 'amplifier-agent @ git+...@vX'` resolves amplifier-agent's OWN
# pyproject dependencies (amplifier-core, amplifier-foundation) -- it does NOT
# touch anything declared in amplifier-agent's *bundle* (bundle.md's
# `providers:`/`session.orchestrator`/`session.context`/`tools:`/`hooks:`
# blocks), because those are installed by a SEPARATE, bundle-managed step
# (`amplifier_foundation.bundle.Bundle.prepare(install_deps=True)`), normally
# triggered by amplifier-agent's own `amplifier-agent-post-install` entry
# point or a session's first cold-prepare -- and `--with` never runs either.
# So `amplifier_agent_lib` importing proved only that the LIBRARY was present,
# never that a turn could actually run.
#
# Verified empirically (2026-08-17, this fix's own load-bearing spike, in a
# fully isolated `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR` on a clean box):
#
#   1. Reproduced the bug: `uv tool install <muxplex-target> --with
#      'amplifier-agent @ git+...@v0.12.0'` alone leaves
#      `amplifier_module_provider_anthropic` (and the `anthropic` SDK itself)
#      genuinely `ModuleNotFoundError` in that exact venv.
#   2. Calling amplifier-agent's own bundle loader
#      (`amplifier_agent_lib.bundle.loader.load_and_prepare_bundle(
#      install_deps=True)`) from that venv's own interpreter makes every
#      bundle module a REAL, pip-registered editable install (verified via
#      `importlib.metadata` dist-info, not just a `sys.path` shim) in that
#      SAME venv: all 5 provider modules (anthropic, openai, azure-openai,
#      ollama, github-copilot), the `loop-streaming` orchestrator, the
#      `context-simple` context module, and every declared tool/hook.
#   3. A full real streamed turn against the live Anthropic API (through
#      `muxplex.agent_embedded.runner.stream_embedded_chat_completion`,
#      the exact code path a real chat message takes) then completes
#      end-to-end from that same venv.
#
# IMPORTANT CORRECTION to an earlier version of this fix (same 2026-08-17
# spike, second round): the documented CLI entry point for this,
# `amplifier-agent-post-install`, looked like the "canonical" way to trigger
# step 2 -- but its own `main()` (post_install.py) short-circuits the INSTANT
# a `manifest.json` exists under
# `~/.amplifier-agent/cache/prepared/<aaa_version>/<bundle_sha256_prefix>/`,
# printing "cache already prepared" and returning 0 WITHOUT EVER PREPARING
# ANYTHING -- and that cache key is (amplifier-agent version, bundle.md
# content hash) ONLY, with NO scoping to which venv is asking. Reproduced
# directly: with that manifest already on disk from an earlier venv's real
# prepare, running `amplifier-agent-post-install` from a brand-new,
# never-prepared second venv (sharing the same $HOME) reported success while
# installing nothing at all into it -- a SILENT no-op for the modules this
# fix exists to guarantee. `uv tool install --reinstall --force` (exactly
# what `ensure_agent()` runs on every reinstall) recreates the venv fresh
# each time, so this isn't a corner case -- it's the common case on any
# machine that has ever prepared this bundle once before. Calling
# `load_and_prepare_bundle()` directly (below) bypasses that shared,
# version+hash-keyed cache layer entirely; the per-module install it performs
# has its OWN, CORRECTLY-scoped idempotency instead
# (`ModuleActivator._distribution_installed()` re-checks THIS interpreter's
# actual site-packages before deciding a module needs installing, not a
# cache note some other venv left behind).
#
# This is why the fix below calls amplifier-agent's bundle loader directly,
# not "hand-list provider modules in a second `--with` flag" and not "shell
# out to amplifier-agent-post-install": it installs precisely and only what
# THAT PINNED VERSION's bundle.md declares, so muxplex never needs a second,
# independently-drifting provider pin of its own -- whichever amplifier-agent
# version `_agent_target_pin()` resolves is the version whose OWN bundle
# decides what gets installed, and the two can never disagree. A live network
# reachability check per provider was considered and rejected: `ensure_agent()`
# runs before any credential is ever configured (during install/upgrade, well
# before a user opens Settings -> Agent), so a live API call would
# deterministically fail on "no key" and would make every install/upgrade
# depend on the provider's own uptime for no real signal -- an import check
# answers exactly the question that matters here ("is the module on disk"),
# which is a packaging concern, not a credential-validation one.

#: Provider short-names the embedded chat panel can actually offer a user
#: (mirrors `agent_embedded.credentials.ALLOWED_PROVIDERS` -- duplicated,
#: not imported: `agent_embedded` is muxplex's OWN optional package, and
#: this module must keep working via `muxplex ensure-agent` even before
#: that package's own dependencies exist. Both lists are the same two
#: providers on purpose; nothing here special-cases which one is "the
#: default" -- `runner.active_provider()` can mount either one a user
#: picks, so both must be ready.)
_AGENT_PANEL_PROVIDERS: tuple[str, ...] = ("anthropic", "openai")


def _provider_module_import_name(provider: str) -> str:
    """Python import name for a provider's amplifier-module package.

    Mirrors amplifier-agent's own bundle.md naming convention (``module:
    provider-<name>`` installs a package importable as
    ``amplifier_module_provider_<name>``) -- verified against the actual
    installed packages for both ``anthropic`` and ``openai`` in the
    2026-08-17 spike referenced above.
    """
    return f"amplifier_module_provider_{provider.replace('-', '_')}"


def _agent_providers_importable(
    providers: tuple[str, ...] = _AGENT_PANEL_PROVIDERS,
) -> tuple[bool, str]:
    """Return (all_importable, detail) for *providers*' amplifier-module packages.

    THIS is the check that actually answers "can the embedded runner
    complete a turn?" -- `_agent_import_probe()` (import amplifier_agent_lib)
    answers a narrower, insufficient one: see the module-level comment above
    for why amplifier_agent_lib being importable proved nothing about the
    provider modules a turn actually needs.

    Checks every provider the Settings -> Agent panel can offer
    (`_AGENT_PANEL_PROVIDERS` above), not just the bundle's own
    ``default_provider`` -- `runner.active_provider()` mounts whichever
    provider a resolved credential names, and a user who picks the
    non-default one must not hit this bug either.

    Returns ``(True, "")`` if every provider imports cleanly, else
    ``(False, detail)`` where *detail* names every provider that failed and
    why -- never a bare ``False`` with no explanation.
    """
    import importlib

    importlib.invalidate_caches()
    missing: list[str] = []
    for provider in providers:
        module_name = _provider_module_import_name(provider)
        try:
            importlib.import_module(module_name)
        except ImportError as exc:
            missing.append(f"{provider} ({module_name}): {exc}")
    if missing:
        return False, "; ".join(missing)
    return True, ""


def _agent_providers_importable_subprocess(
    providers: tuple[str, ...] = _AGENT_PANEL_PROVIDERS,
) -> tuple[bool, str]:
    """Same question as `_agent_providers_importable()` -- can every
    *provider*'s amplifier-module package actually be imported -- but
    answered by spawning a FRESH subprocess of THIS venv's own interpreter
    (``sys.executable``) rather than importing in the process that is
    already running.

    THIS is the authoritative check to run immediately after
    `_run_agent_post_install()`, and ONLY there -- see the call site in
    `ensure_agent()` for why its OTHER call (the pre-install fast-path
    check) deliberately keeps using the cheaper in-process
    `_agent_providers_importable()` instead.

    Diagnosed empirically during a fleet rollout (2026-08-17): the
    in-process check -- `_agent_providers_importable()`, its own
    `importlib.invalidate_caches()` included -- is a reproducible FALSE
    NEGATIVE the instant it follows an install that just happened, in this
    same process, via `_run_agent_post_install()`'s subprocess. It failed
    on the very first `ensure_agent()` invocation on 6 of 6 fleet hosts,
    burning every retry, while a brand-new process (a second `muxplex
    ensure-agent` invocation, or a bare `python -c "import ..."`) run
    moments later -- against the identical, already-installed venv --
    always succeeded instantly. `importlib.invalidate_caches()` only
    invalidates path-finder DIRECTORY caches: it forces a rescan of
    `sys.path` entries that are already registered finders, but it does
    NOT re-run the interpreter's `site` startup -- so an editable install's
    `.pth`-based import hook, written to site-packages *after* this
    interpreter already processed every `.pth` file at startup, stays
    invisible no matter how many times caches are invalidated. A freshly
    spawned interpreter of the SAME venv reprocesses every `.pth` file in
    site-packages from scratch, which is the only mechanism actually proven
    (this diagnosis) to reliably observe a just-completed install --
    exactly why this function exists instead of a second
    `invalidate_caches()` call.

    Returns ``(True, "")`` if every provider imports cleanly in the fresh
    subprocess, else ``(False, detail)`` naming every provider that failed
    and why -- never a bare ``False`` with no explanation, and a subprocess
    launch failure or malformed output is reported as a failure too, never
    silently treated as success.
    """
    if not providers:
        return True, ""

    script_lines = ["missing = []"]
    for provider in providers:
        module_name = _provider_module_import_name(provider)
        script_lines.append("try:")
        script_lines.append(f"    import {module_name}")
        script_lines.append("except ImportError as exc:")
        script_lines.append(
            f"    missing.append({provider!r} + ' (' + {module_name!r} + '): ' + str(exc))"
        )
    script_lines.append("import json, sys")
    script_lines.append("sys.stdout.write(json.dumps(missing))")
    script = "\n".join(script_lines)

    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return False, "provider import probe (fresh subprocess) timed out after 60s"
    except Exception as exc:
        return False, f"could not run provider import probe (fresh subprocess): {exc}"

    if result.returncode != 0:
        stderr_detail = result.stderr.strip()[-2000:]
        return False, (
            f"provider import probe (fresh subprocess) exited {result.returncode}"
            + (f": {stderr_detail}" if stderr_detail else "")
        )

    import json

    try:
        missing = json.loads(result.stdout.strip() or "[]")
    except ValueError:
        return False, (
            "provider import probe (fresh subprocess) produced unparseable"
            f" output: {result.stdout.strip()[-2000:]}"
        )

    if missing:
        return False, "; ".join(missing)
    return True, ""


#: The exact snippet run (via the target venv's OWN interpreter) to prepare
#: amplifier-agent's bundle. Calls the loader FUNCTION directly rather than
#: the `amplifier-agent-post-install` CLI script -- see the module-level
#: comment above `_AGENT_PANEL_PROVIDERS` for why that script's own
#: cache-existence short-circuit makes it unsafe to rely on here.
_AGENT_BUNDLE_PREPARE_SNIPPET = (
    "import asyncio\n"
    "from amplifier_agent_lib.bundle.loader import load_and_prepare_bundle\n"
    "asyncio.run(load_and_prepare_bundle(install_deps=True))\n"
)


def _run_agent_post_install(uv_path: str) -> tuple[bool, str]:
    """Prepare amplifier-agent's bundle -- every provider, the orchestrator,
    the context module, every tool, every hook it declares
    (``amplifier_agent_lib/bundle/bundle.md``) -- as REAL editable installs
    (``uv pip install -e``) into THIS venv, not merely a git-clone cache.

    Calls amplifier-agent's own bundle loader FUNCTION directly
    (``amplifier_agent_lib.bundle.loader.load_and_prepare_bundle(
    install_deps=True)``) via ``sys.executable`` -- the SAME venv
    `ensure_agent()` just verified/installed muxplex + amplifier-agent into
    (``sys.executable`` reports the tool venv's own ``bin/python`` path
    directly; verified empirically, 2026-08-17 spike). See the module-level
    comment above `_AGENT_PANEL_PROVIDERS` for why this does NOT shell out to
    the documented ``amplifier-agent-post-install`` CLI entry point instead:
    that script's own cache-existence short-circuit is a silent no-op for
    any venv other than the first one that ever primed it on a given
    machine -- exactly the case on every reinstall, since `ensure_agent()`
    recreates the venv fresh each time.

    The underlying per-module dependency installer shells out to a bare
    ``"uv"`` (no PATH-independent lookup, unlike this file's `_find_uv()`)
    -- so *uv_path*'s directory is prepended to the subprocess's PATH here,
    defending against the exact stripped-PATH failure mode `_find_uv()`'s
    own docstring describes for systemd/launchd contexts.

    Unlike ``amplifier-agent-post-install`` (which always exits 0 by
    design, swallowing every failure), this subprocess propagates a genuine
    module-activation failure as a non-zero exit -- but the caller's own
    follow-up call to `_agent_providers_importable_subprocess()` (a FRESH
    interpreter, not this same process -- see that function's docstring
    for why) remains the AUTHORITATIVE gate either way; never trust a 0
    exit alone as proof of a working install.

    Returns (ok, detail) -- detail is the subprocess's stderr (progress /
    error text) either way.
    """
    env = dict(os.environ)
    uv_dir = str(Path(uv_path).parent)
    env["PATH"] = f"{uv_dir}{os.pathsep}{env.get('PATH', '')}"

    print(
        "  Preparing amplifier-agent bundle (providers, orchestrator, tools, hooks)..."
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", _AGENT_BUNDLE_PREPARE_SNIPPET],
            capture_output=True,
            text=True,
            env=env,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return False, "bundle preparation timed out after 600s"
    except Exception as exc:
        return False, f"could not run bundle preparation: {exc}"

    if result.returncode != 0:
        return (
            False,
            f"bundle preparation exited {result.returncode}: {result.stderr.strip()[-2000:]}",
        )
    return True, result.stderr.strip()


def ensure_agent(*, force: bool = False) -> bool:
    """Idempotently ensure amplifier-agent -- AND every provider its
    embedded chat panel can offer -- is installed into muxplex's OWN
    uv-tool environment -- regardless of whether THIS muxplex came from
    PyPI or git (see the module note above for why neither source gets it
    any other way).

    Two things must both be true for a turn to actually work, checked and
    (re)installed independently:
      1. ``amplifier_agent_lib`` importable at the pin muxplex declares
         (`_agent_import_probe()` / `_agent_target_pin()`).
      2. Every panel-selectable provider module actually importable --
         checked with `_agent_providers_importable()` (in-process) BEFORE
         any install runs, but re-checked with
         `_agent_providers_importable_subprocess()` (fresh interpreter)
         immediately AFTER `_run_agent_post_install()` runs, since that
         second check is racing an install that just happened in this
         same process -- see each function's own docstring, and the
         "muxplex-fx2 gap" comment above `_AGENT_PANEL_PROVIDERS`, for why
         (1) alone shipped a fleet-wide dead chat panel: `--with
         amplifier-agent@vX` resolves (1) but never touches (2), which is
         a separate, bundle-managed install step
         (`amplifier-agent-post-install`).

    Fast path (the common case on every call after the first): if BOTH are
    already true, this is a handful of import-and-compare checks -- no
    subprocess, no network. If only (1) needs work, the full
    `uv tool install` reinstall runs and (2) is prepared straight
    afterward. If (1) is already fine but (2) isn't (e.g. a device that
    ran the OLD version of this function before this fix shipped), the
    `uv tool install` reinstall is skipped entirely -- only the bundle
    (provider) preparation step runs, since amplifier-agent itself doesn't
    need touching.

    Called from two places, both AFTER muxplex itself is already on disk at
    the version whose pin matters:
      - `service.service_install()` -- the documented next command after
        `uv tool install muxplex` (README's "Install as a Service" section),
        and the first point a fresh PyPI install can pick this up without a
        manual step.
      - `upgrade()` -- unconditionally, right after the main muxplex
        (+ tmux-kit) reinstall succeeds, so every update keeps the agent
        current too -- independent of whether a service manager is present
        (some hosts have neither systemd nor launchd, and `service_install()`
        is only reached from `upgrade()` when one of them is).

    Deliberately NOT called from `serve()` itself: `serve()` IS the
    long-running process this venv serves requests from, and rewriting the
    venv a process is currently executing from is exactly the fragility
    tower's own supervisor avoids by installing BEFORE serve, never from
    inside it. An explicit `muxplex ensure-agent` subcommand covers the bare
    `muxplex serve` (no service) flow, and `doctor()` surfaces the gap
    loudly if nobody ran it.

    Returns True if amplifier-agent AND every panel provider module are
    (now) importable, False otherwise -- NEVER raises and NEVER reports
    success on faith (every exit prints exactly what happened before
    returning False). Callers decide whether False is fatal: `muxplex
    ensure-agent` exits 1; `service_install()`/`upgrade()` print the
    failure loudly but continue (amplifier-agent is an optional capability
    -- losing it must not brick muxplex's own service install or update).
    """
    import importlib

    importlib.invalidate_caches()
    target_pin = _agent_target_pin()

    lib_version, lib_err = _agent_import_probe()
    lib_ok = not force and lib_version == target_pin

    if lib_ok:
        providers_ok, providers_detail = _agent_providers_importable()
        if providers_ok:
            print(
                f"  \u2713 amplifier-agent {lib_version} installed"
                f" (providers ready: {', '.join(_AGENT_PANEL_PROVIDERS)})"
            )
            return True
        print(
            f"  amplifier-agent {lib_version} installed but provider"
            f" module(s) not ready ({providers_detail}) -- preparing bundle..."
        )
    elif not force:
        if lib_version is not None:
            print(
                f"  amplifier-agent {lib_version} installed but muxplex pins"
                f" {target_pin} -- reinstalling to match"
            )
        else:
            print(f"  amplifier-agent not installed ({lib_err}) -- installing...")

    info = _get_install_info()
    if info["source"] == "editable":
        print(
            "  amplifier-agent: skipping -- muxplex is an editable checkout."
            " Install it yourself: uv sync --extra agent"
        )
        return False

    # Python floor guard (muxplex-x60 Phase 1): amplifier-agent requires
    # Python >=3.12 at every released version, but muxplex itself only
    # requires >=3.11 -- so on 3.11 the uv resolver would be handed a
    # requirement it can NEVER satisfy, producing a raw "unsatisfiable"
    # traceback instead of an explanation. Check this BEFORE `_find_uv()`
    # and never construct/run the install command at all below the floor
    # -- there is nothing for uv to attempt. This is a correctly-reported
    # unsupported configuration, not a failure: return True (non-fatal)
    # so `ensure_agent()`'s automatic call sites (`upgrade()`,
    # `service_install()`) and a manual `muxplex ensure-agent` all treat
    # it as a clean no-op rather than a scary-looking error the user can't
    # do anything about.
    if not _agent_python_supported():
        print(
            "  amplifier-agent (embedded agent panel) requires Python"
            f" >=3.12; you are on {sys.version_info[0]}.{sys.version_info[1]}."
            " muxplex itself is unaffected -- everything except the agent"
            " panel works. To enable the panel, reinstall muxplex under a"
            " 3.12+ interpreter, e.g.: uv tool install --python 3.12 --force"
            " muxplex. Skipping."
        )
        return True

    uv_path = _find_uv()
    if not uv_path:
        print(
            "  ERROR: cannot install amplifier-agent -- uv not found on PATH."
            " pip has no equivalent of uv's --with; install uv first"
            " (https://docs.astral.sh/uv/), or from a git checkout run:"
            " uv sync --extra agent"
        )
        return False

    # Only reinstall amplifier-agent itself (a whole separate `uv tool
    # install`) when the library is actually missing or at the wrong pin --
    # if it's only the bundle/providers that need preparing, skip straight
    # to `_run_agent_post_install` below.
    if force or not lib_ok:
        install_target, refuse_reason = _upgrade_target(info)
        if install_target is None:
            print(f"  ERROR: cannot ensure amplifier-agent -- {refuse_reason}")
            return False
        if not _target_matches_source(info, install_target):
            # Same defense-in-depth as upgrade()'s own check -- never hand the
            # installer a target that doesn't match the recorded source.
            print(
                "  ERROR: cannot ensure amplifier-agent -- computed install"
                f" target does not match muxplex's recorded install source"
                f" ({info['source']}): {install_target!r}"
            )
            return False

        agent_with = f"{_AGENT_DIST_NAME} @ git+{_AGENT_REPO_URL}@v{target_pin}"
        install_cmd = [
            uv_path,
            "tool",
            "install",
            "--reinstall",
            "--refresh",
            "--force",
            install_target,
            "--with",
            agent_with,
        ]
        print(f"  Installing amplifier-agent v{target_pin} (git, pinned)...")
        result = subprocess.run(install_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(
                "  ERROR: failed to install amplifier-agent -- git fetch or uv"
                f" resolution failed:\n{result.stderr}"
            )
            return False

        # Defense-in-depth: muxplex's own install source must not have changed
        # shape as a side effect of this reinstall (mirrors
        # _verify_install_shape_preserved's role in `upgrade()`).
        importlib.invalidate_caches()
        after_info = _get_install_info()
        if after_info["source"] != info["source"]:
            print(
                "  ERROR: muxplex's install source changed shape while ensuring"
                f" amplifier-agent: {info['source']} -> {after_info['source']}"
            )
            return False

        # Never report success on faith -- prove it imports at the pin we asked for.
        lib_version, lib_err = _agent_import_probe()
        if lib_version != target_pin:
            print(
                "  ERROR: amplifier-agent install command succeeded but the"
                f" library is still not importable at v{target_pin}"
                f" (got: {lib_version or lib_err})"
            )
            return False

    # amplifier_agent_lib is now confirmed importable at the pinned version
    # -- but that alone never proved a turn could run (see the module-level
    # comment above `_AGENT_PANEL_PROVIDERS`). Prepare the bundle (every
    # provider, the orchestrator, context, tools, hooks) for real.
    #
    # Bundle preparation activates ~20 modules concurrently
    # (amplifier_foundation's ModuleActivator.activate_all runs one
    # `uv pip install -e` per module via asyncio.gather); a transient
    # per-module failure (network blip, resource contention on a busy
    # host, or -- observed directly, 2026-08-17 spike, on a container
    # filesystem -- a brief lag between a grandchild `uv pip install -e`
    # process writing dist-info and this process's own import check seeing
    # it) is swallowed internally by activate_all() (it only raises in
    # strict mode, which bundle.prepare() doesn't request), so a 0 exit
    # here is NOT proof every module actually installed OR immediately
    # importable. Observed repeatedly in that spike: an identical fresh
    # venv succeeded outright on one attempt within THIS process and needed
    # a moment to become visible on another -- with no code difference
    # between runs, and a brand-new, wholly separate `muxplex ensure-agent`
    # invocation moments later always found the SAME modules already
    # correctly installed (confirming the install itself lands; only
    # visibility from within the original process's retry loop can lag).
    # Retrying is safe and cheap: a module already installed is skipped
    # (`ModuleActivator`'s own `_distribution_installed()` check), so a
    # retry only redoes whatever didn't land the first time. Bounded --
    # not an unbounded loop -- with a short pause between attempts to give
    # any filesystem-visibility lag a moment to clear.
    providers_ok = False
    providers_detail = ""
    _RETRY_PAUSE_SECONDS = 2.0
    _MAX_ATTEMPTS = 3
    for attempt in range(_MAX_ATTEMPTS):
        post_install_ok, post_install_detail = _run_agent_post_install(uv_path)
        if not post_install_ok:
            print(
                f"  ERROR: amplifier-agent {lib_version} installed but preparing"
                f" its bundle (providers, orchestrator, tools) failed --"
                f" {post_install_detail}"
            )
            return False

        # A 0 exit only means the subprocess ran to completion -- never
        # proof every module installed or is yet visible (see comment
        # above). This re-check is the actual gate -- run in a FRESH
        # subprocess (`_agent_providers_importable_subprocess`), not
        # in-process: this check immediately follows an install that just
        # happened IN THIS SAME PROCESS, which is exactly the case the
        # in-process `_agent_providers_importable()` gets wrong (see that
        # function's docstring for the fleet-diagnosed false-negative).
        providers_ok, providers_detail = _agent_providers_importable_subprocess()
        if providers_ok:
            break
        if attempt < _MAX_ATTEMPTS - 1:
            print(
                f"  amplifier-agent bundle prepared but provider module(s)"
                f" not yet importable ({providers_detail}) -- retrying"
                f" (attempt {attempt + 2}/{_MAX_ATTEMPTS})..."
            )
            time.sleep(_RETRY_PAUSE_SECONDS)

    if not providers_ok:
        print(
            f"  ERROR: amplifier-agent {lib_version} bundle prepare completed"
            f" but provider module(s) still not importable after"
            f" {_MAX_ATTEMPTS} attempts ({providers_detail})"
        )
        return False

    print(
        f"  \u2713 amplifier-agent {lib_version} installed"
        f" (providers ready: {', '.join(_AGENT_PANEL_PROVIDERS)})"
    )
    return True


def generate_federation_key() -> None:
    """Generate a random federation key and write it to FEDERATION_KEY_PATH."""
    import muxplex.settings as settings_mod

    path = settings_mod.FEDERATION_KEY_PATH
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = _secrets.token_urlsafe(32)
    path.write_text(key + "\n")
    path.chmod(0o600)
    print(f"Federation key written to {path}")
    print(f"Key: {key}")


def reset_secret() -> None:
    """Regenerate the signing secret and warn that all sessions are now invalid."""
    path = get_secret_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = _secrets.token_urlsafe(32)
    path.write_text(secret + "\n")
    path.chmod(0o600)
    print(f"Secret written to {path}")
    print("Warning: all active sessions are now invalid.")


def reset_device_id_command() -> None:
    """Regenerate the device identity UUID and warn about orphaned session keys."""
    from muxplex.identity import (
        IDENTITY_PATH,
        load_device_id,
        reset_device_id,
    )

    old_id = load_device_id()
    new_id = reset_device_id()
    print(f"New device_id: {new_id}")
    print(f"Identity file: {IDENTITY_PATH}")
    print(f"Previous device_id: {old_id}")
    print("Warning: existing session keys are now orphaned.")


def show_password() -> None:
    """Print the current muxplex password or indicate PAM mode."""
    auth_mode = os.environ.get("MUXPLEX_AUTH", "").lower()
    if auth_mode != "password" and pam_available():
        print("Auth mode: PAM — no password file used")
        return
    pw = load_password()
    if pw:
        print(f"Password: {pw}")
    else:
        print("No password file found. Start muxplex to auto-generate one.")


def _fetch_local_instance_info(port: int, timeout: float = 2.0) -> dict | None:
    """Fetch ``/api/instance-info`` from whatever is serving *port* on localhost.

    Probes https then http (TLS is optional in muxplex, so the scheme cannot be
    assumed).  Certificate verification is disabled deliberately: we are talking
    to ourselves on loopback and may not have the local CA installed. Returns
    the parsed JSON dict on the first 200 response that decodes as a dict, or
    None if nothing answered (port free, wrong service, refused, timeout, TLS
    mismatch, garbage body).

    DELIBERATELY SHARED, RAW FETCH ONLY -- do not add decision logic here.
    Two callers use this:
      - :func:`_port_holder_is_healthy_muxplex` (safety-critical: decides
        whether the startup path is allowed to SIGTERM the port holder).
      - ``muxplex doctor`` (cosmetic: reports the running version alongside
        the installed one).
    Sharing the network probe avoids duplicating the finicky
    https-then-http-with-cert-bypass dance in two places, but each caller
    still makes its OWN decision about what the response means. A change to
    doctor's reporting must never be able to alter the port-kill safety
    logic, so that logic stays entirely in
    :func:`_port_holder_is_healthy_muxplex`, not here.
    """
    import json
    import ssl
    import urllib.request

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    for scheme in ("https", "http"):
        url = f"{scheme}://127.0.0.1:{port}/api/instance-info"
        try:
            with urllib.request.urlopen(url, timeout=timeout, context=ctx) as resp:
                if resp.status != 200:
                    continue
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            continue  # refused / timeout / TLS mismatch / garbage -> nothing there
    return None


def _launchd_job_pid_and_exit() -> tuple[int | None, int | None]:
    """(pid, last_exit_status) for the com.muxplex launchd job.

    `launchctl list` prints one row per job: PID, last exit status, label. A pid
    of "-" means nothing is running right now -- and the exit status then tells
    you whether it died or was never started. This is the only cheap way to tell
    "loaded and healthy" from "loaded and crash-looping", which `launchctl print`
    returning 0 cannot distinguish.
    """
    try:
        out = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return (None, None)
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] == "com.muxplex":
            pid = int(parts[0]) if parts[0].isdigit() else None
            status = int(parts[1]) if parts[1].lstrip("-").isdigit() else None
            return (pid, status)
    return (None, None)


def _instance_is_this_host(data: dict | None) -> bool | None:
    """Did this ``/api/instance-info`` payload come from THIS machine's muxplex?

    True = ours, False = someone else's, None = cannot tell (no payload, or no
    device_id in it).

    WHY THIS EXISTS -- localhost:PORT is NOT proof of locality. An
    ``ssh -N -L 8088:127.0.0.1:8088 otherhost`` tunnel makes another machine's
    muxplex answer on our own loopback, indistinguishable from ours by probing
    alone. Observed in the wild, and it cost hours: the tunnel held the port, so
    the local service could never bind and crash-looped on exit 1 forever, while
    `doctor` cheerfully probed the same port, reached the REMOTE server down the
    tunnel, and reported ITS version as "Running" -- pinning the blame on a
    stale local install that had in fact never started.

    device_id is the discriminator: per-install, persisted in identity.json,
    and already returned by /api/instance-info.
    """
    if not isinstance(data, dict):
        return None
    remote_id = data.get("device_id")
    if not remote_id:
        return None
    try:
        from muxplex.identity import load_device_id

        return str(remote_id) == load_device_id()
    except Exception:
        return None


def _port_holder_is_healthy_muxplex(port: int, timeout: float = 2.0) -> bool:
    """Return True if a live, responding muxplex is serving *port*.

    WHY THIS EXISTS -- do not "simplify" it away:
    Without this probe, :func:`_kill_stale_port_holder` cannot tell a hung/stale
    holder apart from a perfectly healthy running server, so ANY second
    invocation of the startup path silently SIGTERMs the live service.  A silent
    kill of a healthy server is indistinguishable from a mystery outage -- it
    produces a clean graceful shutdown in the logs with no crash and no
    ``Stopping`` line from systemd, which is extremely hard to diagnose.  This
    probe converts that silent kill into a loud, actionable refusal.
    """
    data = _fetch_local_instance_info(port, timeout=timeout)
    # A real muxplex always reports both of these.
    return isinstance(data, dict) and "device_id" in data and "version" in data


def _kill_stale_port_holder(port: int, force: bool = False) -> None:
    """Free *port* from a STALE holder, refusing to kill a healthy server.

    On service restart (``systemctl restart muxplex``), the old process may still
    be holding the port in TIME_WAIT state or simply not have exited yet.  Without
    this guard the new process fails to bind, exits with status=1, and systemd
    restarts it in an infinite loop (observed: 2075+ restarts before manual
    intervention).

    The original implementation killed *whatever* held the port, which meant a
    stray invocation of the startup path would silently terminate a healthy,
    serving muxplex.  Now the holder is probed first
    (:func:`_port_holder_is_healthy_muxplex`) and killed ONLY on positive
    evidence that it is not serving.  If it *is* serving, this raises
    ``SystemExit(1)`` with an actionable message instead of starting.

    Restart-race reasoning: during a legitimate ``systemctl restart``, systemd
    waits for the old process to exit before starting the new one, so normally no
    holder exists and the probe never runs.  If an old instance is still draining
    and answers the probe, the new process exits non-zero and systemd retries
    after ``RestartSec`` -- a bounded retry that resolves as soon as the old
    instance finishes draining.  That is strictly better than killing a healthy
    server, and cannot become a tight loop because each attempt costs a full
    ``RestartSec`` delay.

    Pass ``force=True`` (``muxplex serve --force-take-port``) to restore the old
    unconditional behaviour.

    A missing ``lsof`` or a permission error never prevents startup.
    """
    import signal
    import time

    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return  # lsof not available or other error — proceed; uvicorn will fail naturally

    if result.returncode != 0 or not result.stdout.strip():
        return  # nobody is holding the port

    my_pid = os.getpid()
    holders: list[int] = []
    for pid_str in result.stdout.strip().split("\n"):
        try:
            pid = int(pid_str.strip())
        except ValueError:
            continue
        if pid != my_pid:
            holders.append(pid)

    if not holders:
        return

    if not force and _port_holder_is_healthy_muxplex(port):
        pids = ", ".join(str(p) for p in holders)
        # A muxplex answered -- but is it OURS? A port-forward makes a remote
        # muxplex answer here, and the old message ("already served by a healthy
        # muxplex ... refusing to terminate it") sent people hunting a local
        # service that was in fact dead, because the thing answering lived on
        # another machine entirely.
        ours = _instance_is_this_host(_fetch_local_instance_info(port))
        if ours is False:
            print(
                f"ERROR: port {port} is held by a muxplex belonging to a DIFFERENT"
                f" machine (holder pid {pids}).\n"
                f"       Something is forwarding this port here -- typically an SSH"
                f" tunnel, e.g.\n"
                f"           ssh -N -L {port}:127.0.0.1:{port} otherhost\n"
                f"       This host's own muxplex cannot bind {port} while that is up,"
                f" and will keep\n"
                f"       exiting 1. Refusing to kill it: it is not ours to kill, and"
                f" the holder is\n"
                f"       probably the tunnel, not a server.\n"
                f"\n"
                f"       Find it:  lsof -nP -iTCP:{port} -sTCP:LISTEN\n"
                f"       Then either stop the forward, or point it at another local"
                f" port.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        print(
            f"ERROR: port {port} is already served by a healthy muxplex (pid {pids}).\n"
            f"       Refusing to terminate it.\n"
            f"\n"
            f"       To restart the service properly:\n"
            f"           muxplex service restart\n"
            f"       To take the port anyway (kills the running server):\n"
            f"           muxplex serve --force-take-port",
            file=sys.stderr,
        )
        raise SystemExit(1)

    for pid in holders:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    time.sleep(1)  # Brief wait for the port to be released


def configure_logging(level: int = logging.INFO) -> None:
    """Ensure muxplex's own log records reach a real handler when serving.

    ``uvicorn.run(..., log_level="info")`` only configures uvicorn's OWN
    loggers (``uvicorn``, ``uvicorn.error``, ``uvicorn.access``) via its
    internal dictConfig -- it never touches the root logger or any
    ``muxplex.*`` logger. Without a handler here, an accepted ``/input``
    call's audit line (``main.py``'s ``_log.info(...)`` in
    ``send_session_input``) is silently discarded: the root logger has no
    handlers and Python's handler-of-last-resort only surfaces WARNING and
    above -- which is exactly why a *rejected* input's ``_log.warning``
    reached the terminal while every *accepted* call's audit line vanished.

    Deliberately scoped to the ``muxplex`` logger namespace, not the root
    logger. Every module does ``logging.getLogger(__name__)`` (e.g.
    ``muxplex.main``, ``muxplex.sessions``), so all of them are children of
    the ``muxplex`` logger and pick up this level/handler via normal
    propagation -- one handler covers the whole package. Configuring root
    instead would also raise every third-party dependency's logger (httpx,
    websockets, etc.) to INFO, turning the operator's audit trail into a
    noisy firehose instead of the targeted signal it's meant to be.

    Idempotent: safe to call more than once (e.g. across multiple ``serve()``
    invocations in one process, as tests do) without installing duplicate
    handlers -- checked by handler name rather than by clearing/reassigning
    ``package_logger.handlers``, so a caller that added its own handler
    beforehand is left alone.
    """
    package_logger = logging.getLogger("muxplex")
    package_logger.setLevel(level)
    if not any(h.name == "muxplex-audit" for h in package_logger.handlers):
        handler = logging.StreamHandler()
        handler.name = "muxplex-audit"
        handler.setLevel(level)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        package_logger.addHandler(handler)


def serve(
    host: str | None = None,
    port: int | None = None,
    auth: str | None = None,
    session_ttl: int | None = None,
    tls_cert: str | None = None,
    tls_key: str | None = None,
    force_take_port: bool = False,
) -> None:
    """Start the muxplex server.

    Resolution order: CLI flag (if not None) > settings.json > hardcoded default.
    """
    import uvicorn

    from muxplex.settings import load_settings

    # Must happen before uvicorn.run(): see configure_logging()'s docstring --
    # uvicorn's own log_level="info" below does not configure muxplex's loggers.
    configure_logging()

    settings = load_settings()
    host = host if host is not None else settings.get("host", "127.0.0.1")
    port = port if port is not None else settings.get("port", 8088)
    auth = auth if auth is not None else settings.get("auth", "pam")
    session_ttl = (
        session_ttl if session_ttl is not None else settings.get("session_ttl", 604800)
    )
    tls_cert = tls_cert if tls_cert is not None else settings.get("tls_cert", "")
    tls_key = tls_key if tls_key is not None else settings.get("tls_key", "")

    os.environ["MUXPLEX_PORT"] = str(port)
    os.environ["MUXPLEX_AUTH"] = auth
    os.environ["MUXPLEX_SESSION_TTL"] = str(session_ttl)

    # Resolve SSL configuration BEFORE importing muxplex.main, and set
    # MUXPLEX_TLS_ENABLED alongside the env vars above -- main.py's
    # SERVER_TLS_ENABLED reads it at import time (same pattern SERVER_PORT
    # already uses for MUXPLEX_PORT). This is the single source of truth
    # main.py's bell hook (_arm_bell_hook / _bell_hook_curl) uses to pick
    # http vs https: it must dial the scheme uvicorn is actually about to
    # serve below, never assume http (see AGENTS.md's bell-hook incident).
    ssl_kwargs: dict = {}
    if tls_cert and tls_key:
        cert_path = Path(tls_cert)
        key_path = Path(tls_key)
        missing = [str(p) for p in (cert_path, key_path) if not p.exists()]
        if missing:
            print(f"  TLS {', '.join(missing)} not found, falling back to HTTP")
        else:
            ssl_kwargs = {"ssl_certfile": tls_cert, "ssl_keyfile": tls_key}
    os.environ["MUXPLEX_TLS_ENABLED"] = "1" if ssl_kwargs else "0"

    # Prevent crash-loop on restart: free the port from a STALE holder only.
    # Refuses to terminate a healthy running server -- see _kill_stale_port_holder.
    _kill_stale_port_holder(port, force=force_take_port)

    from muxplex.main import app

    scheme = "https" if ssl_kwargs else "http"
    print(f"  muxplex → {scheme}://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info", **ssl_kwargs)


def doctor() -> None:
    """Run diagnostic checks and report system status."""
    ok_mark = "\033[32m✓\033[0m"  # green check
    fail_mark = "\033[31m✗\033[0m"  # red x
    warn_mark = "\033[33m!\033[0m"  # yellow warning

    print("\nmuxplex doctor\n")

    # Python version
    py_version = platform.python_version()
    py_ok = tuple(int(x) for x in py_version.split(".")[:2]) >= (3, 11)
    print(
        f"  {ok_mark if py_ok else fail_mark} Python {py_version}"
        + ("" if py_ok else " (3.11+ required)")
    )

    # tmux
    tmux_path = shutil.which("tmux")
    if tmux_path:
        try:
            result = subprocess.run(
                ["tmux", "-V"], capture_output=True, text=True, timeout=5
            )
            tmux_version = result.stdout.strip()
            print(f"  {ok_mark} {tmux_version}")
        except Exception:
            print(f"  {ok_mark} tmux (version unknown)")
    else:
        print(f"  {fail_mark} tmux — not found")
        if sys.platform == "darwin":
            print("    Install: brew install tmux")
        else:
            print("    Install: sudo apt install tmux")

    # ttyd
    ttyd_path = shutil.which("ttyd")
    if ttyd_path:
        try:
            result = subprocess.run(
                ["ttyd", "--version"], capture_output=True, text=True, timeout=5
            )
            ttyd_version = result.stdout.strip() or result.stderr.strip()
            print(f"  {ok_mark} ttyd {ttyd_version}")
        except Exception:
            print(f"  {ok_mark} ttyd (version unknown)")
    else:
        print(f"  {fail_mark} ttyd — not found")
        if sys.platform == "darwin":
            print("    Install: brew install ttyd")
        else:
            print("    Install: sudo apt install ttyd")

    # muxplex version + install source + update check
    try:
        from importlib.metadata import version as pkg_version

        muxplex_version = pkg_version("muxplex")
    except Exception:
        muxplex_version = "dev"

    info = _get_install_info()
    print(f"  {ok_mark} muxplex {muxplex_version}")
    print(f"    \u21b3 from {_provenance_label(info)}")

    # tmux-kit's own install source -- the identical machinery above,
    # generalized (docs/plans/2026-08-09-tmuxkit-own-repo-and-pypi-plan.md
    # §2.4). Reports the version and provenance of whatever tmux-kit is
    # ACTUALLY installed in this environment, which may be a workspace/
    # editable checkout (monorepo dev), a plain PyPI pin, or a git+https
    # ref installed via a `--with` override on a managed device.
    kit_info = _get_install_info("tmux-kit")
    if kit_info["source"] == "not-installed":
        print(f"  {fail_mark} tmux-kit \u2014 not installed (muxplex requires it)")
    else:
        print(f"  {ok_mark} tmux-kit {kit_info['version']}")
        print(f"    \u21b3 from {_provenance_label(kit_info)}")

        # Drift warning: muxplex's own published pin vs what's actually
        # installed. A mismatch means the venv was modified by hand outside
        # `muxplex upgrade` (e.g. a manual `uv pip install tmux-kit==...`),
        # never something `doctor` should stay silent about.
        declared_pin = _declared_dependency_pin("tmux-kit")
        if declared_pin and kit_info["version"] != declared_pin:
            print(
                f"  {warn_mark} tmux-kit version mismatch: installed"
                f" v{kit_info['version']} but muxplex declares tmux-kit=={declared_pin}"
                " \u2014 the environment was modified outside 'muxplex upgrade'"
            )

    # amplifier-agent's own install status -- unlike tmux-kit above this is
    # OPTIONAL (the embedded agent panel degrades to a clean per-request
    # error when it's missing; see agent_embedded/runner.py's
    # EmbeddedAgentUnavailable), so an absent agent is a warning here, never
    # a failure mark. See cli.ensure_agent's module docstring for why a
    # bare `uv tool install muxplex` (from PyPI OR git) never gets this on
    # its own, and why `muxplex service install` / `muxplex upgrade` /
    # `muxplex ensure-agent` are the commands that do.
    agent_info = _get_install_info(_AGENT_DIST_NAME)
    if agent_info["source"] == "not-installed":
        if _agent_python_supported():
            print(
                f"  {warn_mark} amplifier-agent -- not installed (embedded agent"
                " panel unavailable until installed)"
            )
            print("    Run: muxplex ensure-agent")
        else:
            # Below the Python floor, recommending `muxplex ensure-agent`
            # is the specific friction muxplex-x60 Phase 1 removes: that
            # command cannot possibly succeed on this interpreter (see
            # `_agent_python_supported`'s docstring), so `doctor` explains
            # why instead of nagging a command that will only dump a
            # resolver traceback.
            print(
                f"  {warn_mark} amplifier-agent -- not installed; requires"
                f" Python >=3.12 (you are on {sys.version_info[0]}."
                f"{sys.version_info[1]}). The embedded agent panel is"
                " unavailable on this Python."
            )
    else:
        print(f"  {ok_mark} amplifier-agent {agent_info['version']}")
        print(f"    \u21b3 from {_provenance_label(agent_info)}")
        declared_agent_pin = _agent_target_pin()
        if agent_info["version"] != declared_agent_pin:
            print(
                f"  {warn_mark} amplifier-agent version mismatch: installed"
                f" v{agent_info['version']} but muxplex pins"
                f" amplifier-agent=={declared_agent_pin}"
            )
            print("    Run: muxplex ensure-agent")

    # Provenance above is purely informational (green): running from git, a
    # local checkout, or an archive is a legitimate, deliberate choice, not
    # something to warn about. The warning below is reserved for a genuinely
    # degraded capability -- this source can't be checked for updates at
    # all, so nothing is watching the installed version.
    update_available, update_msg = _check_for_update(info)
    if update_available:
        print(f"  {warn_mark} Update: {update_msg}")
        print("    Run: muxplex upgrade")
    elif update_msg.startswith("not checkable"):
        print(f"  {warn_mark} Update: {update_msg}")
    else:
        print(f"  {ok_mark} {update_msg}")

    # Settings file
    from muxplex.settings import SETTINGS_PATH

    if SETTINGS_PATH.exists():
        print(f"  {ok_mark} Settings: {SETTINGS_PATH}")
    else:
        print(
            f"  {warn_mark} Settings: {SETTINGS_PATH} (not yet created — will use defaults)"
        )

    # Serve config
    from muxplex.settings import load_settings

    cfg = load_settings()
    print(
        f"  {ok_mark} Serve config: {cfg['host']}:{cfg['port']}"
        f" (auth={cfg['auth']}, ttl={cfg['session_ttl']}s)"
    )

    # Running vs installed version. `_get_install_info`/`_check_for_update`
    # above only ever look at what's INSTALLED -- they cannot see that a
    # `uv tool install`/`upgrade` has not yet been picked up by the actual
    # running service, which needs a restart to load the new code. This is
    # exactly the gap that left a live server on v0.14.0 for hours after the
    # install moved to v0.15.0, with nothing anywhere saying so.
    running_info = _fetch_local_instance_info(cfg["port"])
    if running_info is None:
        # A perfectly normal state (muxplex not started, or started on a
        # different port) -- NOT an error, so it must read differently from
        # the "running but stale" case below.
        print(
            f"  {warn_mark} Running: not serving on {cfg['host']}:{cfg['port']}"
            " (nothing to compare against the installed version)"
        )
    else:
        running_version = running_info.get("version") or "unknown"
        # Something muxplex-shaped answered on our port -- but a port-forward
        # makes ANOTHER machine's muxplex answer here too, and reporting its
        # version as ours is actively misleading. Check identity before trusting
        # it: this exact case sent a real investigation after a phantom stale
        # install while the local service was dead and crash-looping.
        if _instance_is_this_host(running_info) is False:
            remote_name = running_info.get("name") or "another machine"
            print(
                f"  {warn_mark} Running: port {cfg['port']} is answered by a muxplex on"
                f" a DIFFERENT machine ({remote_name}, v{running_version})"
            )
            print(
                f"    This host's muxplex is NOT reachable here. Something is"
                f" forwarding port {cfg['port']}"
            )
            print(f"    Find it: lsof -nP -iTCP:{cfg['port']} -sTCP:LISTEN")
        elif running_version == muxplex_version:
            print(f"  {ok_mark} Running: v{running_version} (matches installed)")
        else:
            print(
                f"  {warn_mark} Running: v{running_version}"
                f" (installed v{muxplex_version} \u2014 restart the service to pick up the new install)"
            )
            print("    Run: muxplex service restart   (or) muxplex upgrade")

        # Bell hook: "armed" means tmux accepted `set-hook` -- NOT proof of
        # delivery (see main.py's _arm_bell_hook() and AGENTS.md's bell-hook
        # section; a prior revision required a real delivery probe here, but
        # that probe was itself a diagnostic `tmux run-shell` call and was
        # removed per AGENTS.md's "never render to a pane" rule). Surfaced
        # here the same way TLS expiry is below: a non-fatal advisory line,
        # not a hard failure. Skipped for a different machine's muxplex
        # (running_info above) -- that host's hook state says nothing about
        # this one. `.get()` also makes this silently absent against an
        # older peer that predates the field (version tolerance, per
        # AGENTS.md).
        if _instance_is_this_host(running_info) is not False:
            hook_armed = running_info.get("bell_hook_armed")
            if hook_armed is False:
                print(
                    f"  {warn_mark} Bell hook: NOT armed \u2014 bells will not"
                    " fire until this heals"
                )
                print(
                    "    Run: muxplex service restart   (or) POST"
                    " /api/internal/setup-hooks"
                )
            elif hook_armed is True:
                print(f"  {ok_mark} Bell hook: armed (registered with tmux)")

    # TLS status
    tls_cert = cfg.get("tls_cert", "")
    tls_key = cfg.get("tls_key", "")
    if tls_cert and tls_key:
        from datetime import datetime, timezone

        from muxplex.tls import get_cert_info

        cert_info = get_cert_info(tls_cert)
        if cert_info is not None:
            expires = cert_info["expires"]
            # Ensure timezone-aware for comparison
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if expires < now:
                days_ago = (now - expires).days
                print(
                    f"  {warn_mark} TLS: WARNING \u2014 cert expired {days_ago} days ago."
                    " Run muxplex setup-tls to renew"
                )
            else:
                expiry_str = expires.strftime("%Y-%m-%d")
                print(f"  {ok_mark} TLS: enabled (cert expires {expiry_str})")
        else:
            print(f"  {warn_mark} TLS: configured but cert not readable ({tls_cert})")
    else:
        # Only show TLS warning if host is not localhost
        host = cfg.get("host", "127.0.0.1")
        if host != "127.0.0.1":
            # Network host without TLS: show nudge
            print(
                f"  {warn_mark} TLS: disabled — clipboard won't work on remote devices"
            )
            print("    Run: muxplex setup-tls")

    # Auth status
    pw_path = get_password_path()
    if pam_available():
        import pwd

        username = pwd.getpwuid(os.getuid()).pw_name
        print(f"  {ok_mark} Auth: PAM available (user: {username})")
    elif pw_path.exists():
        print(f"  {ok_mark} Auth: password file ({pw_path})")
    elif os.environ.get("MUXPLEX_PASSWORD"):
        print(f"  {ok_mark} Auth: password (env var)")
    else:
        print(f"  {warn_mark} Auth: no PAM, no password — will auto-generate on serve")

    # tmux sessions (if tmux is available)
    if tmux_path:
        try:
            result = subprocess.run(
                ["tmux", "list-sessions", "-F", "#{session_name}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                sessions = [s for s in result.stdout.strip().split("\n") if s]
                print(f"  {ok_mark} tmux sessions: {len(sessions)} active")
            else:
                print(f"  {warn_mark} tmux server not running (no sessions)")
        except Exception:
            print(f"  {warn_mark} tmux server not running")

    # Platform + service status
    print(f"  {ok_mark} Platform: {sys.platform} ({platform.machine()})")
    if sys.platform == "darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / "com.muxplex.plist"
        if plist.exists():
            if _have_launchctl():
                uid = os.getuid()
                result = subprocess.run(
                    ["launchctl", "print", f"gui/{uid}/com.muxplex"],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    # REGISTERED IS NOT RUNNING. `launchctl print` succeeding only
                    # means the label is loaded; the process behind it can be dead
                    # and relaunching on a loop. Probing the port does not settle it
                    # either -- a forwarded port answers happily while this host's
                    # service is down. Observed: a job stuck in exactly that state
                    # (no pid, last exit 1, 15 MB of stderr) reported as a green
                    # "launchd agent running" for hours. Ask launchd for the pid.
                    from muxplex.settings import load_settings

                    _cfg = load_settings()
                    _port = _cfg.get("port", 8088)
                    _pid, _last_exit = _launchd_job_pid_and_exit()
                    if _pid is not None:
                        print(
                            f"  {ok_mark} Service: launchd agent running (pid {_pid})"
                        )
                    elif _last_exit not in (None, 0):
                        print(
                            f"  {warn_mark} Service: launchd agent is LOADED BUT NOT"
                            f" RUNNING \u2014 last exit status {_last_exit}"
                        )
                        print("    Logs: tail -50 /tmp/muxplex.err")
                    else:
                        print(
                            f"  {warn_mark} Service: launchd agent registered but"
                            f" not serving on port {_port}"
                        )
                else:
                    print(
                        f"  {warn_mark} Service: launchd agent installed but not running ({plist})"
                    )
            else:
                print(
                    f"  {warn_mark} Service: launchctl not found — cannot check status"
                )
        else:
            print(
                f"  {warn_mark} Service: not installed (run: muxplex service install)"
            )
    else:
        if not _have_systemctl():
            print(f"  {warn_mark} Service: systemd not available on this platform")
        else:
            systemd_user = (
                Path.home() / ".config" / "systemd" / "user" / "muxplex.service"
            )
            if systemd_user.exists():
                _active = subprocess.run(
                    ["systemctl", "--user", "is-active", "muxplex"],
                    capture_output=True,
                    text=True,
                )
                if _active.returncode == 0:
                    print(
                        f"  {ok_mark} Service: systemd user unit installed ({systemd_user})"
                    )
                else:
                    _state = _active.stdout.strip() or "unknown"
                    print(
                        f"  {warn_mark} Service: systemd user unit installed but"
                        f" not active — state: {_state} ({systemd_user})"
                    )
            elif _system_service_path.exists():
                print(
                    f"  {ok_mark} Service: systemd system unit installed ({_system_service_path})"
                )
            else:
                print(
                    f"  {warn_mark} Service: not installed (run: muxplex service install)"
                )

    print()  # trailing newline


def _check_dependencies() -> None:
    """Verify required external programs are installed.

    Checks for tmux and ttyd. Prints a helpful error message and exits with
    code 1 if any are missing.
    """
    missing = []
    if shutil.which("tmux") is None:
        missing.append(("tmux", "sudo apt install tmux  /  brew install tmux"))
    if shutil.which("ttyd") is None:
        missing.append(("ttyd", "sudo apt install ttyd  /  brew install ttyd"))

    if missing:
        print("\n  ERROR: Required dependencies not found:\n", file=sys.stderr)
        for name, install_hint in missing:
            print(f"    {name}: {install_hint}", file=sys.stderr)
        print(
            "\n  For details: https://github.com/bkrabach/muxplex#prerequisites\n",
            file=sys.stderr,
        )
        sys.exit(1)


def _verify_version_moved(before: str, update_was_available: bool) -> bool:
    """Confirm an upgrade actually landed. Print and return False if it did not.

    A zero exit from the installer means "the installer did what I asked", NOT
    "the version changed". Those come apart whenever the resolver picks the
    version already installed -- a stale index being the common cause -- and the
    difference is invisible from the exit code alone. Checking it is the whole
    point: an upgrade that silently no-ops leaves you running old code while
    every message on screen says you are current.

    Only an upgrade we KNEW was available is required to move. A --force
    reinstall of the current version is a legitimate no-op, not a failure.
    """
    after = _installed_version_on_disk()
    if after is None:
        print(
            "  ERROR: install reported success but the installed version could"
            " not be read back.\n"
            "  Check it yourself: uv tool list  (or) pip show muxplex"
        )
        return False
    if update_was_available and after == before:
        print(
            f"  ERROR: install reported success but the version did not change"
            f" (still v{after}).\n"
            f"  The resolver almost certainly served a cached index that predates"
            f" the release.\n"
            f"  Fix it with:\n"
            f"      uv tool install --reinstall --refresh --force muxplex"
        )
        return False
    if after != before:
        print(f"  Version: v{before} \u2192 v{after}")
    return True


def _read_remote_tmux_kit_pin(repo_url: str, ref: str) -> tuple[str | None, str | None]:
    """Shallow-clone `repo_url` at `ref` and read its pyproject.toml's own
    `tmux-kit==X.Y.Z` dependency pin.

    Used to derive the tmux-kit git ref to preserve across a muxplex upgrade
    (docs/plans/2026-08-09-tmuxkit-own-repo-and-pypi-plan.md §2.5 step 3): the
    ref must come from the TARGET muxplex's own pin, not the currently
    installed one, or a muxplex version bump would silently leave tmux-kit
    pinned to a stale ref forever. Uses the same git+https transport the
    device has already proven working (no new network assumption) -- a
    shallow, single-branch clone rather than `git archive --remote`, which
    GitHub's own https transport does not support.

    Returns (version, error) -- exactly one is None.
    """
    import re
    import tempfile

    try:
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", ref, repo_url, tmp],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                return (
                    None,
                    f"could not clone {repo_url}@{ref}: {result.stderr.strip()}",
                )

            pyproject_path = Path(tmp) / "pyproject.toml"
            if not pyproject_path.exists():
                return None, f"no pyproject.toml found at {repo_url}@{ref}"

            text = pyproject_path.read_text(encoding="utf-8")
            match = re.search(r'"tmux-kit==([0-9][\w.\-]*)"', text)
            if not match:
                return (
                    None,
                    f"no tmux-kit==X.Y.Z pin found in {repo_url}@{ref}'s pyproject.toml",
                )
            return match.group(1), None
    except Exception as exc:
        return None, f"could not read {repo_url}@{ref}'s tmux-kit pin: {exc}"


def _resolve_upgrade_kit_ref(
    info_mux: dict, mux_target: str, info_kit: dict
) -> tuple[str | None, str | None]:
    """Decide which git ref to pin tmux-kit's `--with` override to across an
    upgrade, when tmux-kit is git-sourced (docs/plans/2026-08-09-tmuxkit-own-
    repo-and-pypi-plan.md §2.5 step 3).

    The ref MUST come from the TARGET muxplex's own `tmux-kit==` pin, read
    from that target's pyproject.toml over git+https -- never guessed, never
    left as the stale currently-installed ref (a stale ref would silently
    keep the OLD tmux-kit paired with the NEW muxplex forever, the exact
    drift the `==` pin exists to make visible).

    Returns (kit_ref, warning): `warning` is None on success. When the pin
    cannot be read (muxplex isn't git-sourced itself, the clone fails, or no
    pin is found), `kit_ref` falls back to the CURRENTLY recorded ref --
    never silently dropped -- and `warning` explains why, for the caller to
    print loudly and let uv's resolver conflict rather than proceed unseen.
    """
    current_ref = info_kit.get("ref")

    mux_repo_url = info_mux.get("url") if info_mux.get("source") == "git" else None
    target_ref = None
    if mux_target.startswith("git+") and "@" in mux_target:
        target_ref = mux_target.rsplit("@", 1)[-1]

    if not mux_repo_url or not target_ref:
        return current_ref, (
            "could not determine the target muxplex's own tmux-kit pin (the"
            " muxplex install target has no resolvable git ref) -- keeping the"
            f" currently recorded tmux-kit ref ({current_ref or 'none'})"
        )

    version, err = _read_remote_tmux_kit_pin(mux_repo_url, target_ref)
    if version is None:
        return current_ref, (
            f"could not read the target muxplex's tmux-kit pin ({err}) --"
            f" keeping the currently recorded tmux-kit ref ({current_ref or 'none'})"
        )

    return f"v{version}", None


def _install_cmd_preserves_kit_override(
    install_cmd: list[str], info_kit: dict, mux_install_target: str
) -> bool:
    """Defense-in-depth (mirrors `_target_matches_source`'s role, but for the
    PAIR rather than muxplex alone): confirm the constructed install command
    pins a git-sourced tmux-kit CORRECTLY for the muxplex target it is
    paired with.

    Catches a future regression that reconstructs `install_cmd` incorrectly
    -- originally written to require a `--with tmux-kit @ git+...` override
    unconditionally whenever tmux-kit's recorded source is git (exactly how
    the bare-name uv-managed shortcut silently dropped it before the first
    fix here -- docs/plans/2026-08-09-tmuxkit-own-repo-and-pypi-plan.md
    §2.5 step 5).

    **That unconditional rule was itself wrong, and shipped a real
    production failure (2026-08-15, v0.47.11):** when `mux_install_target`
    is ALSO a git target (`git+https://.../muxplex@vX`), that target's own
    `pyproject.toml` already carries `[tool.uv.sources] tmux-kit = { git =
    ..., tag = ... }` -- uv resolves tmux-kit from THAT pin on its own, with
    no override needed. Adding a `--with tmux-kit @ git+...@vX` override on
    top of it gives uv TWO url-bearing requirement origins for the same
    package and it refuses to resolve -- `Requirements contain conflicting
    URLs for package 'tmux-kit'` -- even when both origins name the
    byte-identical URL. Reproduced in isolation in a scratch `UV_TOOL_DIR`:
    `uv tool install git+.../muxplex@v0.47.11 --with 'tmux-kit @
    git+.../tmux-kit@v0.4.0'` fails with that error; the identical install
    WITHOUT `--with` succeeds and resolves tmux-kit from git at the
    expected ref (verified via the installed package's own
    `direct_url.json` showing `vcs_info`). So a git muxplex target
    satisfies this guarantee by the ABSENCE of `--with`, not its presence
    -- do not "fix" this back to requiring it unconditionally; that
    reintroduces the exact failure above. It is PyPI-sourced muxplex
    targets (and any other non-git target) that need the override: a
    published wheel's metadata strips `[tool.uv.sources]` entirely (see
    AGENTS.md's "tmux-kit pin/tag agreement" section), so `--with` is the
    ONLY thing pinning tmux-kit to git in that case, and its absence there
    would silently drop the pin -- the original failure mode this function
    was written to catch, and it must still catch it.

    Why the git-target/PyPI-target distinction can't be read off
    `install_cmd` alone: both a bare `"muxplex"` (PyPI) and a `"git+..."`
    element could in principle appear in the list, and the *correct*
    element to look for depends on which shape was actually requested --
    passing `mux_install_target` explicitly (rather than re-deriving it by
    scanning the built command) keeps this check anchored to what was
    ACTUALLY decided to install, not a guess reconstructed from strings.
    """
    if info_kit["source"] != "git":
        return True

    has_override = "--with" in install_cmd and any(
        isinstance(arg, str) and arg.startswith("tmux-kit @ git+")
        for arg in install_cmd
    )

    if mux_install_target.startswith("git+"):
        # The muxplex git target's own [tool.uv.sources] pin already
        # resolves tmux-kit -- an ADDED --with here is the regression (see
        # docstring above), not its absence.
        return not has_override

    return has_override


def _install_cmd_targets_install_target(
    install_cmd: list[str], install_target: str
) -> bool:
    """Defense-in-depth for MUXPLEX'S OWN target (companion check to
    `_install_cmd_preserves_kit_override`, which guards tmux-kit's pairing):
    confirm the constructed install command actually installs
    `install_target` verbatim, never a substituted bare package name or any
    other string.

    This is the mechanical check that would have caught the v0.49.0
    incident: `upgrade()`'s uv-managed branch used to decide whether to
    install the bare "muxplex" shortcut or the explicit `install_target`
    by checking tmux-kit's recorded source (`info_kit["source"]`) instead
    of muxplex's own (`info["source"]`). Whenever muxplex was git-sourced
    but tmux-kit happened not to be, that branch silently built
    `["uv", "tool", "install", "--reinstall", "--refresh", "--force",
    "muxplex"]` -- a git install reinstalled from PyPI with no trace of the
    git URL anywhere in the command. `install_target` itself was computed
    correctly by `_upgrade_target` and validated by `_target_matches_source`
    the whole time; the defect was that a *later* branch discarded it in
    favor of a hardcoded literal. `_target_matches_source` cannot catch
    this class of bug because it only checks the intermediate
    `install_target` string, never the actually-constructed `install_cmd`
    -- exactly the gap this function closes, mirroring
    `_install_cmd_preserves_kit_override`'s role for the tmux-kit pairing.

    The invariant is intentionally the simplest one that is universally
    true across every branch that builds `install_cmd` (uv-managed bare-name
    shortcut, uv-managed explicit target, non-managed uv, and the pip
    fallback): `install_target` must appear in the command verbatim. For a
    PyPI source, `install_target` IS the bare string `"muxplex"` (per
    `_upgrade_target`), so this reduces to the same check in that case --
    there is no separate literal to keep in sync.
    """
    return install_target in install_cmd


def _verify_install_shape_preserved(
    before_mux_source: str, before_kit_source: str
) -> tuple[bool, str]:
    """Confirm neither muxplex's nor tmux-kit's install SOURCE SHAPE changed
    across this upgrade (e.g. git -> pypi in either slot).

    This is the permanent guard from docs/plans/2026-08-09-tmuxkit-own-repo-
    and-pypi-plan.md §2.5 step 4: uv's own preserve-vs-replace semantics for
    `--with` overrides are unproven and version-dependent (the plan's ledger
    #11), so this re-reads BOTH direct_url.json records fresh after the
    install and refuses to call the upgrade successful if either shape
    silently changed -- this is what enforces the property forever,
    independent of whatever a given uv version actually does.

    `importlib.metadata` caches distribution lookups within a running
    process (see `_installed_version_on_disk`'s docstring for the same
    caveat) -- `invalidate_caches()` first so this reads what is actually on
    disk right now, not what was true when this process started.

    Returns (ok, message) -- message is empty on success, else names exactly
    which slot changed shape and how.
    """
    import importlib

    importlib.invalidate_caches()
    after_mux = _get_install_info()
    after_kit = _get_install_info("tmux-kit")

    if after_mux["source"] != before_mux_source:
        return False, (
            f"muxplex's install source changed shape: {before_mux_source}"
            f" -> {after_mux['source']}"
        )
    if after_kit["source"] != before_kit_source:
        return False, (
            f"tmux-kit's install source changed shape: {before_kit_source}"
            f" -> {after_kit['source']}"
        )
    return True, ""


# ---------------------------------------------------------------------------
# muxplex-lf6: post-install steps run in a FRESH interpreter, never in-process
# ---------------------------------------------------------------------------
#
# `upgrade()` overwrites muxplex's on-disk package files, then used to keep
# executing its post-install steps (ensure_agent, service-file regeneration,
# restart, verification) in the SAME process. That process already has the
# OLD `muxplex.cli` / `muxplex.service` modules cached in `sys.modules`, so a
# lazy cross-module import of a symbol that only exists in the NEW code (e.g.
# `service.py`'s own `from muxplex.cli import ensure_agent`) can resolve
# against the stale cached module instead of what was just written to disk --
# this is the real incident: `ImportError: cannot import name 'ensure_agent'
# from 'muxplex.cli'`, hit via `service_install()`.
#
# The fix: after a successful install, `upgrade()` hands off the post-install
# steps to `_finish_upgrade()` -- but NOT by calling it directly. It launches
# a genuinely NEW OS process (`subprocess.run([*entrypoint, "_finish-upgrade"])`)
# via the just-installed muxplex's own entrypoint, so every import inside
# `_finish_upgrade()` resolves against a fresh interpreter that has never
# imported anything -- there is no stale cache to fall into.


def _installed_muxplex_entrypoint() -> list[str] | None:
    """Resolve the argv token list for launching a FRESH `muxplex` process.

    Deliberately duplicates `service.py`'s own entrypoint-resolution
    precedence (`_resolve_muxplex_bin_for_launchd`'s ordering) rather than
    importing anything from `muxplex.service` -- this helper is used by
    `upgrade()` specifically to launch a subprocess of the
    freshly-installed muxplex; importing this process's own (about to be
    stale) `muxplex.*` modules here would risk exactly the cached-module
    problem this whole mechanism exists to avoid. Stdlib-only by design.

    Precedence:
      1. ``shutil.which("muxplex")`` -- PATH lookup; picks up whatever the
         installer just wrote (uv tool install symlinks it into
         ``~/.local/bin``, which is typically already on PATH).
      2. ``~/.local/bin/muxplex`` if it exists and is executable -- the
         stable uv-tool console-script symlink, checked directly in case
         PATH doesn't include it in this process's own environment.
      3. ``[sys.executable, "-m", "muxplex"]`` -- last resort; always
         resolvable since `sys.executable` is this process's own
         interpreter and `muxplex` is what is currently running.

    Returns ``None`` only if no launchable entrypoint could be determined
    at all (in practice unreachable, since step 3 always succeeds if
    `sys.executable` is set) -- callers must not assume a non-``None``
    result without checking; `upgrade()` treats ``None`` as F1 (entrypoint
    missing/unlaunchable).
    """
    which = shutil.which("muxplex")
    if which:
        return [which]

    local_bin = Path.home() / ".local" / "bin" / "muxplex"
    if local_bin.exists() and os.access(str(local_bin), os.X_OK):
        return [str(local_bin)]

    if sys.executable:
        return [sys.executable, "-m", "muxplex"]

    return None


def _finish_upgrade() -> int:
    """Run `upgrade()`'s post-install steps -- called ONLY as a fresh
    subprocess launched by `upgrade()` itself, via the hidden
    ``muxplex _finish-upgrade`` subcommand. Never call this directly from
    within an already-running muxplex process -- see the module-level
    comment above `_installed_muxplex_entrypoint` for why: every import in
    this function must resolve against a genuinely fresh interpreter that
    has never cached an older `muxplex.cli` / `muxplex.service`.

    Order (matches the sequence this replaces, previously run in-process
    inside `upgrade()`):
      1. `ensure_agent()` -- keep amplifier-agent current too. Best-effort:
         a failure here is printed loudly by `ensure_agent()` itself but
         must not skip the (mandatory) service restart below --
         amplifier-agent is an optional capability.
      2. Regenerate the service file (`service.service_install()`, only if
         a service manager is present) -- picks up any changed
         plist/unit-file content.
      3. Restart the service -- ALWAYS attempted, in a `finally`, even if
         step 1 or 2 raised or failed. This mirrors `upgrade()`'s own
         original try/finally shape: a best-effort restart must run
         whether or not everything before it succeeded.
      4. Wait for the service to actually answer
         (`_wait_for_service_ready`) and run `doctor()` -- this process's
         stdio is inherited directly from `upgrade()` (no capture), so
         this output appears to the user exactly as it did when it ran
         in-process.

    Returns 0 if the service was confirmed running afterward (or there is
    no service manager to confirm against), 1 if a service manager IS
    present but the service could not be confirmed running. `upgrade()`
    treats a non-zero exit here as F2 (the handoff ran but failed) --
    it does NOT re-run its own restart logic, since this function's own
    `finally` block already attempted it.
    """
    try:
        print("  Ensuring amplifier-agent is current...")
        ensure_agent()

        if sys.platform == "darwin" or _have_systemctl():
            print("  Regenerating service file...")
            from muxplex.service import service_install

            service_install()
        else:
            print("  ! systemctl not found -- skipping service file regeneration")
    finally:
        # Restart ALWAYS runs, best-effort -- see this function's own
        # docstring point 3.
        label = "com.muxplex"
        uid = os.getuid()
        plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        _service_restart_failed = False

        if sys.platform == "darwin":
            if _have_launchctl():
                if plist.exists():
                    print("  Starting launchd service...")
                    result = subprocess.run(
                        ["launchctl", "bootstrap", f"gui/{uid}", str(plist)],
                        capture_output=True,
                        text=True,
                    )
                    if result.returncode != 0:
                        # Fallback to legacy load for older macOS
                        subprocess.run(
                            ["launchctl", "load", str(plist)], capture_output=True
                        )
                    if _verify_service_started():
                        print("  Service started")
                    else:
                        print(
                            "  ERROR: launchd agent registered but the service is"
                            " not responding after upgrade.\n"
                            "  Check /tmp/muxplex.err for details."
                        )
                        _service_restart_failed = True
                else:
                    print("  Service file not found -- run: muxplex service install")
            else:
                print("  ! launchctl not found -- skipping service restart")
        elif _have_systemctl():
            result = subprocess.run(
                ["systemctl", "--user", "is-enabled", "muxplex"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                print("  Restarting systemd service...")
                # daemon-reload FIRST: picks up any regenerated unit file so
                # the start command sees the correct ExecStart.
                subprocess.run(
                    ["systemctl", "--user", "daemon-reload"], capture_output=True
                )
                subprocess.run(
                    ["systemctl", "--user", "start", "muxplex"], capture_output=True
                )
                if not _verify_service_started():
                    # Unit may have landed in 'failed' state (e.g. port race
                    # on first start). Reset the failure counter and retry once.
                    subprocess.run(
                        ["systemctl", "--user", "reset-failed", "muxplex"],
                        capture_output=True,
                    )
                    subprocess.run(
                        ["systemctl", "--user", "start", "muxplex"],
                        capture_output=True,
                    )
                    if _verify_service_started():
                        print("  Service started")
                    else:
                        print(
                            "  ERROR: muxplex service is not active after upgrade.\n"
                            "  Run: systemctl --user status muxplex"
                        )
                        _service_restart_failed = True
                else:
                    print("  Service started")
            else:
                print("  Service not enabled -- run: muxplex service install")
        else:
            # No service manager -- ask the user to restart manually
            pid_hint = ""
            try:
                pid_result = subprocess.run(
                    ["pgrep", "-f", "muxplex serve"],
                    capture_output=True,
                    text=True,
                )
                pid_str = pid_result.stdout.strip()
                if pid_str:
                    pid_hint = f" (running PID: {pid_str})"
            except Exception:
                pass
            print(
                "  ! systemd not detected -- restart muxplex manually to pick up"
                " the new version" + pid_hint
            )

    if _service_restart_failed:
        print(
            "\n  ERROR: upgrade installed successfully but the service failed to"
            " restart.\n"
            "  The new version is installed but the service is NOT running.\n"
            "  Run: muxplex service start\n"
        )
        return 1

    from muxplex.settings import load_settings

    _serve_cfg = load_settings()
    print("\n  Verifying...")
    if not _wait_for_service_ready(_serve_cfg["port"]):
        print(
            f"  ! Service did not respond on port {_serve_cfg['port']} within"
            " the timeout -- it may still be starting, or may have failed to"
            " come up. Checking anyway:"
        )
    doctor()
    return 0


def upgrade(*, force: bool = False) -> None:
    """Upgrade muxplex to the latest version and restart the service."""
    print("\nmuxplex upgrade\n")

    # Show current install info
    info = _get_install_info()
    commit_suffix = f" (commit {info['commit'][:8]})" if info["commit"] else ""
    ref_suffix = f" @ {info['ref']}" if info.get("ref") else ""
    print(
        f"  Installed: v{info['version']}{commit_suffix} via {info['source']}{ref_suffix}"
    )

    # tmux-kit's own install source -- the shape that must survive this
    # upgrade unchanged when it's git-sourced (§2.5). Shown here for the
    # same reason muxplex's own line is: so a human watching the upgrade can
    # see what was recorded BEFORE anything runs.
    info_kit = _get_install_info("tmux-kit")
    kit_commit_suffix = (
        f" (commit {info_kit['commit'][:8]})" if info_kit["commit"] else ""
    )
    kit_ref_suffix = f" @ {info_kit['ref']}" if info_kit.get("ref") else ""
    print(
        f"  tmux-kit : v{info_kit['version']}{kit_commit_suffix} via"
        f" {info_kit['source']}{kit_ref_suffix}"
    )

    # Editable installs are always user-managed -- muxplex must never
    # reinstall over a checkout the user is actively developing in, --force
    # or not. See the install-source table in _upgrade_target's docstring.
    if info["source"] == "editable":
        print(
            "\n  Editable install — muxplex does not manage this.\n"
            "  Update it yourself, e.g.: git -C <your checkout> pull\n"
        )
        return

    update_available = False
    if not force:
        update_available, message = _check_for_update(info)
        print(f"  Status: {message}")

        if not update_available:
            print(
                f"\n  {message}. Use 'muxplex upgrade --force' to reinstall anyway.\n"
            )
            return
    else:
        print("  Status: --force specified — skipping version check")

    # install_target is derived STRICTLY from the recorded install source
    # (direct_url.json) -- never guessed, never substituted. This is the fix
    # for the defect where a git install got silently converted to the PyPI
    # package (because it also happened to be uv-managed), or a local build
    # got silently overwritten with upstream canonical. See
    # _upgrade_target's docstring.
    install_target, refuse_reason = _upgrade_target(info)
    if install_target is None:
        print(f"\n  Refusing to upgrade: {refuse_reason}\n")
        sys.exit(1)

    # Safety net (defense-in-depth, independent of the above being correct):
    # if what we're about to install doesn't match the recorded source's
    # shape, stop and show both rather than silently installing something
    # else. --force overrides this specific gate only -- the user still has
    # to have explicitly asked for it.
    if not _target_matches_source(info, install_target):
        print(
            "\n  REFUSING: the computed install target does not match the"
            " recorded install source.\n"
            f"    Recorded source : {info['source']} ({info.get('url') or 'n/a'})\n"
            f"    Computed target : {install_target}\n"
        )
        if not force:
            print("  Not proceeding without --force.\n")
            sys.exit(1)
        print("  --force specified — proceeding anyway.\n")

    # Bug 3: Detect whether this install is managed by uv tool. This decides
    # WHICH uv invocation shape to use below (a --reinstall of the existing
    # tool environment vs a fresh `uv tool install`) -- it must never decide
    # WHAT to install; install_target (above) already settled that from the
    # recorded source alone.
    _uv_tools_dir = str(Path.home() / ".local" / "share" / "uv" / "tools")
    _muxplex_script = shutil.which("muxplex")
    _is_uv_managed = False
    if _muxplex_script:
        try:
            if _uv_tools_dir in str(Path(_muxplex_script).resolve()):
                _is_uv_managed = True
        except Exception:
            pass

    uv_path = _find_uv()

    # Pre-compute macOS service identifiers — used in both stop and finally blocks.
    label = "com.muxplex"
    uid = os.getuid()
    plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"

    # 1. Stop service
    if sys.platform == "darwin":
        # Bug 2a: guard every launchctl call with _have_launchctl()
        if not _have_launchctl():
            print("  ! launchctl not found — skipping service management step")
        elif plist.exists():
            print("  Stopping launchd service...")
            subprocess.run(
                ["launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True
            )
        else:
            print("  No launchd service found (skipping stop)")
    else:
        # Linux/WSL — check systemd
        if not _have_systemctl():
            print("  ! systemctl not found — skipping service management step")
        else:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", "muxplex"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                print("  Stopping systemd service...")
                subprocess.run(
                    ["systemctl", "--user", "stop", "muxplex"], capture_output=True
                )
            else:
                print("  No active systemd service found (skipping stop)")

    # 2+4. Install (try) with guaranteed service restart in finally.
    # Bug 1+2b: try/finally ensures the start step always runs — success OR failure.
    _install_failed = False
    _service_restart_failed = False
    # muxplex-lf6: True once the fresh-interpreter handoff (_finish-upgrade)
    # actually ran -- suppresses this function's OWN restart logic in
    # `finally` below, since `_finish_upgrade()` already attempted its own
    # restart in the child process. See `_finish_upgrade`'s docstring.
    _finish_handed_off = False
    print("  Installing latest version...")
    try:
        # tmux-kit override construction (§2.5 step 3): whenever tmux-kit is
        # git-sourced, the --with override that pins it must be RE-DERIVED
        # and RE-ISSUED on every upgrade -- uv's own preserve-vs-replace
        # semantics for --with are unproven and version-dependent (the
        # plan's ledger #11), so this never assumes the override survives on
        # its own. If no ref can be determined at all, refuse rather than
        # silently drop the override and let tmux-kit fall through to an
        # index a managed device may not be able to reach.
        #
        # CORRECTED 2026-08-15 (v0.47.11 incident): the paragraph above is
        # true ONLY when muxplex's own install target is NOT itself git. A
        # git muxplex target's pyproject.toml already carries
        # [tool.uv.sources] for tmux-kit (see AGENTS.md's "tmux-kit pin/tag
        # agreement" section) -- uv honors that pin on its own, no override
        # needed. Adding --with on TOP of that pin gives uv two url-bearing
        # requirement origins for the identical package, and it refuses to
        # resolve even when both origins name the byte-identical URL:
        #   `uv tool install git+.../muxplex@vX --with 'tmux-kit @
        #   git+.../tmux-kit@vY'` -> "Requirements contain conflicting URLs
        #   for package `tmux-kit`"
        # -- reproduced in isolation in a scratch UV_TOOL_DIR; the identical
        # install WITHOUT --with succeeds and resolves tmux-kit from git at
        # the expected ref. This is exactly what broke a real `muxplex
        # update` in production (git muxplex + git tmux-kit, identical
        # pinned URLs on both sides). See
        # _install_cmd_preserves_kit_override's docstring for the full
        # writeup and the corrected guard.
        mux_target_is_git = install_target.startswith("git+")
        kit_with_args: list[str] = []
        if info_kit["source"] == "git" and mux_target_is_git:
            print(
                "  tmux-kit: git-sourced, pinned via the muxplex git"
                " target's own [tool.uv.sources] -- no --with override"
                " needed (adding one would conflict with that pin)."
            )
        elif info_kit["source"] == "git":
            kit_url = info_kit.get("url") or ""
            if not kit_url:
                print(
                    "  ERROR: tmux-kit is recorded as a git install but has no"
                    " recorded remote URL -- refusing to upgrade rather than"
                    " silently drop the override."
                )
                _install_failed = True
            else:
                kit_ref, kit_ref_warning = _resolve_upgrade_kit_ref(
                    info, install_target, info_kit
                )
                if kit_ref_warning:
                    print(f"  ! {kit_ref_warning}")
                if not kit_ref:
                    print(
                        "  ERROR: tmux-kit is git-sourced but no ref could be"
                        " determined (neither derived from the target nor"
                        " previously recorded) -- refusing to upgrade rather"
                        " than silently fall through to an index."
                    )
                    _install_failed = True
                else:
                    kit_with_args = ["--with", f"tmux-kit @ git+{kit_url}@{kit_ref}"]

        # Bug 3 / v0.49.0 incident: dispatch -- uv-tool-managed gets a
        # --reinstall bare-name shortcut ONLY when MUXPLEX ITSELF is
        # PyPI-sourced; every other muxplex install (git, local-dir,
        # archive, ...) always installs EXPLICITLY via install_target.
        #
        # CORRECTED 2026-08-16 (v0.49.0 incident): this branch used to key
        # off `info_kit["source"]` (tmux-kit's own source) to decide
        # whether muxplex's OWN install used the bare "muxplex" shortcut or
        # the explicit `install_target`. That conflated two independent
        # questions -- "does tmux-kit need a --with override?" (answered by
        # info_kit["source"], already computed above into kit_with_args)
        # and "what should muxplex itself install as?" (must be answered by
        # info["source"] alone). Whenever muxplex was git-sourced but
        # tmux-kit happened NOT to be git-sourced -- true of every git
        # install made before v0.45.1 added tmux-kit's own git pin, and
        # reachable again any time tmux-kit's install drifts independently
        # of muxplex's -- the old code took the `else` branch and hardcoded
        # bare "muxplex", silently reinstalling a git-sourced host from
        # PyPI. This is the exact "upgrade silently switches install
        # method" defect the owner has now hit twice, just one layer
        # deeper than the git+git `--with` conflict v0.47.12 already fixed.
        # Reproduced in isolation (mux=git, tmux-kit=pypi, uv-managed):
        # the old code emitted `uv tool install --reinstall --refresh
        # --force muxplex` -- no git URL anywhere in the command.
        #
        # `install_target` is used literally in BOTH branches below (never
        # a separate hardcoded "muxplex" string) specifically so the two
        # can't diverge again: for a pypi source, `_upgrade_target` already
        # returns the bare string "muxplex", so using `install_target`
        # there is byte-identical to the old hardcode and changes nothing
        # for the common PyPI case; for every other source it is the
        # explicit target that must be preserved.
        if not _install_failed and uv_path:
            if _is_uv_managed and info["source"] == "pypi":
                # uv-tool install: always reinstall the package by name
                # --refresh is load-bearing, not belt-and-braces. The target is
                # unpinned ("muxplex" = latest), so uv answers it from its cached
                # PyPI index. A cache that predates the release resolves "latest"
                # to the version already installed, reinstalls it, and exits 0 --
                # a perfectly successful no-op upgrade. Observed on a real Mac:
                # three consecutive upgrades reported success while the venv
                # never left 0.31.2.
                install_cmd = [
                    uv_path,
                    "tool",
                    "install",
                    "--reinstall",
                    "--refresh",
                    "--force",
                    install_target,
                    *kit_with_args,
                ]
            else:
                # Non-PyPI muxplex source (git today; local-dir/archive are
                # the other recognized shapes) -- OR not uv-managed at all.
                # Either way install EXACTLY install_target; the bare-name
                # shortcut above is a PyPI-only optimization and must never
                # apply here.
                install_cmd = [
                    uv_path,
                    "tool",
                    "install",
                    "--force",
                    "--refresh",
                    install_target,
                    *kit_with_args,
                ]

            # §2.5 step 5: defense-in-depth pair check, mirroring
            # _target_matches_source's role for muxplex alone -- catches a
            # future regression that reconstructs install_cmd without the
            # override.
            if not _install_cmd_preserves_kit_override(
                install_cmd, info_kit, install_target
            ):
                print(
                    "\n  REFUSING: the constructed install command has no"
                    " --with tmux-kit override, but tmux-kit is recorded as a"
                    " git install.\n"
                    f"    Recorded tmux-kit source: git ({info_kit.get('url') or 'n/a'})\n"
                    f"    Computed command        : {install_cmd}\n"
                )
                _install_failed = True
            # Mechanical safety net for muxplex's OWN target (mirrors the
            # kit-pair check just above, but for muxplex itself): whatever
            # branch just ran, the constructed command must literally
            # install `install_target` -- never a substituted bare name.
            # This is the check that would have caught the v0.49.0 defect
            # documented above by construction, independent of which branch
            # produced install_cmd, and it stays correct even if a future
            # edit adds more branches here.
            elif not _install_cmd_targets_install_target(install_cmd, install_target):
                print(
                    "\n  REFUSING: the constructed install command does not"
                    " target the recorded install source.\n"
                    f"    Recorded source : {info['source']} ({info.get('url') or 'n/a'})\n"
                    f"    Expected target : {install_target}\n"
                    f"    Computed command: {install_cmd}\n"
                )
                _install_failed = True
            else:
                result = subprocess.run(install_cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    print(f"  ERROR: uv tool install failed:\n{result.stderr}")
                    _install_failed = True
                elif not _verify_version_moved(info["version"], update_available):
                    _install_failed = True
                else:
                    shape_ok, shape_msg = _verify_install_shape_preserved(
                        info["source"], info_kit["source"]
                    )
                    if not shape_ok:
                        print(f"  ERROR: {shape_msg}")
                        _install_failed = True
                    else:
                        print("  Installed successfully")
        elif not _install_failed:
            # uv absent → fall back to pip (probe known locations off PATH).
            # pip has no equivalent of uv's --with, so a git-sourced tmux-kit
            # override cannot be expressed here at all -- refuse loudly
            # rather than silently drop it (same rule as above).
            if info_kit["source"] == "git":
                print(
                    "  ERROR: tmux-kit is git-sourced but uv is not available"
                    " -- pip cannot express a --with override. Refusing to"
                    " upgrade rather than silently drop the override."
                )
                _install_failed = True
            else:
                pip_path = _find_pip()
                if pip_path:
                    result = subprocess.run(
                        [pip_path, "install", "--upgrade", install_target],
                        capture_output=True,
                        text=True,
                    )
                    if result.returncode != 0:
                        print(f"  ERROR: pip install failed:\n{result.stderr}")
                        _install_failed = True
                    elif not _verify_version_moved(info["version"], update_available):
                        _install_failed = True
                    else:
                        shape_ok, shape_msg = _verify_install_shape_preserved(
                            info["source"], info_kit["source"]
                        )
                        if not shape_ok:
                            print(f"  ERROR: {shape_msg}")
                            _install_failed = True
                        else:
                            print("  Installed successfully")
                else:
                    print("  ERROR: neither uv nor pip found — cannot upgrade")
                    _install_failed = True

        if not _install_failed:
            # muxplex-lf6: hand off the post-install steps (ensure_agent,
            # service-file regeneration, restart, verification) to a FRESH
            # interpreter of the muxplex version JUST installed above --
            # never run them in THIS (now-stale) process. See the
            # module-level comment above `_installed_muxplex_entrypoint`
            # for the ImportError incident this avoids: this process still
            # has the OLD `muxplex.cli` / `muxplex.service` cached in
            # `sys.modules` even though the files on disk just changed.
            entrypoint = _installed_muxplex_entrypoint()
            if entrypoint is None:
                # F1: no launchable entrypoint could be determined at all.
                # The post-install steps never ran -- tell the user how to
                # complete them by hand. The parent's OWN restart below
                # still runs (best-effort; `_finish_handed_off` stays
                # False), same as every other failure path here.
                print(
                    "  ERROR: could not determine how to relaunch the"
                    " freshly-installed muxplex to finish the upgrade"
                    " (ensure_agent, service regeneration). Run 'muxplex"
                    " service install' to complete it manually."
                )
                _install_failed = True
            else:
                print("  Finishing upgrade in a fresh interpreter...")
                try:
                    finish_result = subprocess.run([*entrypoint, "_finish-upgrade"])
                except OSError as exc:
                    # F1: entrypoint resolved but could not actually be
                    # launched (e.g. permission denied, binary missing).
                    # Same handling as entrypoint is None above.
                    print(
                        "  ERROR: could not launch the freshly-installed"
                        f" muxplex to finish the upgrade ({exc}). Run"
                        " 'muxplex service install' to complete it manually."
                    )
                    _install_failed = True
                else:
                    # F2: the handoff genuinely ran -- `_finish_upgrade()`
                    # (in the child) already attempted its own restart in
                    # ITS OWN finally block, so the parent's finally below
                    # must NOT restart again (no double-restart).
                    _finish_handed_off = True
                    if finish_result.returncode != 0:
                        print(
                            "  ERROR: finishing the upgrade failed (exit"
                            f" {finish_result.returncode}). The new version"
                            " is installed; see the output above for"
                            " details."
                        )
                        _install_failed = True
    finally:
        # 4. Restart service — ALWAYS runs after install, even on failure
        # (best-effort) — UNLESS the fresh-interpreter handoff above
        # actually ran: `_finish_upgrade()` already attempted its own
        # restart in that case, and restarting again here would be a
        # redundant double-restart racing the one that already happened.
        if _finish_handed_off:
            pass
        elif sys.platform == "darwin":
            # Bug 2a: guard launchctl
            if _have_launchctl():
                if plist.exists():
                    print("  Starting launchd service...")
                    result = subprocess.run(
                        ["launchctl", "bootstrap", f"gui/{uid}", str(plist)],
                        capture_output=True,
                        text=True,
                    )
                    if result.returncode != 0:
                        # Fallback to legacy load for older macOS
                        subprocess.run(
                            ["launchctl", "load", str(plist)], capture_output=True
                        )
                    # Verify the agent is actually serving (not just registered)
                    if _verify_service_started():
                        print("  Service started")
                    else:
                        print(
                            "  ERROR: launchd agent registered but the service is"
                            " not responding after upgrade.\n"
                            "  Check /tmp/muxplex.err for details."
                        )
                        _service_restart_failed = True
                else:
                    print("  Service file not found — run: muxplex service install")
        elif _have_systemctl():
            result = subprocess.run(
                ["systemctl", "--user", "is-enabled", "muxplex"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                print("  Restarting systemd service...")
                # daemon-reload FIRST: picks up any regenerated unit file so
                # the start command sees the correct ExecStart (spark-1 fix).
                subprocess.run(
                    ["systemctl", "--user", "daemon-reload"], capture_output=True
                )
                subprocess.run(
                    ["systemctl", "--user", "start", "muxplex"], capture_output=True
                )
                if not _verify_service_started():
                    # Unit may have landed in 'failed' state (e.g. port race
                    # on first start).  Reset the failure counter and retry once.
                    subprocess.run(
                        ["systemctl", "--user", "reset-failed", "muxplex"],
                        capture_output=True,
                    )
                    subprocess.run(
                        ["systemctl", "--user", "start", "muxplex"],
                        capture_output=True,
                    )
                    if _verify_service_started():
                        print("  Service started")
                    else:
                        print(
                            "  ERROR: muxplex service is not active after upgrade.\n"
                            "  Run: systemctl --user status muxplex"
                        )
                        _service_restart_failed = True
                else:
                    print("  Service started")
            else:
                print("  Service not enabled — run: muxplex service install")
        else:
            # No service manager — ask the user to restart manually
            pid_hint = ""
            try:
                pid_result = subprocess.run(
                    ["pgrep", "-f", "muxplex serve"],
                    capture_output=True,
                    text=True,
                )
                pid_str = pid_result.stdout.strip()
                if pid_str:
                    pid_hint = f" (running PID: {pid_str})"
            except Exception:
                pass
            print(
                "  ! systemd not detected — restart muxplex manually to pick up the new version"
                + pid_hint
            )

    # Bug 1: propagate install failure as a non-zero exit so callers / scripts
    # can detect the partial upgrade.  Service restart was attempted above (best-effort).
    if _install_failed:
        print(
            "\n  ERROR: upgrade failed — muxplex service has been restarted (best-effort).\n"
        )
        sys.exit(1)

    if _service_restart_failed:
        print(
            "\n  ERROR: upgrade installed successfully but the service failed to restart.\n"
            "  The new version is installed but the service is NOT running.\n"
            "  Run: muxplex service start\n"
        )
        sys.exit(1)

    # 5. Wait for the service to be ready + run doctor() to verify --
    # muxplex-lf6: this step now happens INSIDE the fresh-interpreter
    # handoff above (`_finish_upgrade()`, step 4 of its own docstring),
    # with its stdio inherited directly by this process, so the user
    # already saw that output before we reach here. Nothing left to do.


def cmd_env() -> None:
    """Print a shell-eval-able TMUX_TMPDIR export for THIS muxplex instance.

    Designed for `eval "$(muxplex env)"` (the ssh-agent/direnv convention).
    Prints ONLY the export line to stdout -- no banners, no extra output --
    so `eval` is always safe. Any human-facing notes go to stderr.

    Why this exists: muxplex looks for tmux sessions under a specific
    socket directory (`tmux_socket_dir` setting, mapped to tmux's
    TMUX_TMPDIR env var). Any OTHER tool that creates a tmux session
    without setting the same TMUX_TMPDIR lands on a DIFFERENT tmux server
    and is silently invisible to this muxplex instance -- see AGENTS.md's
    "tmux socket" section and settings.py's tmux_socket_dir comment. Running
    `eval "$(muxplex env)"` before creating a session is the one-line fix.

    Best-effort resolution: this CLI process's own environment is not
    necessarily the same as the running muxplex *service* process's
    environment (a systemd/launchd unit's env commonly differs from an
    interactive shell's). When `tmux_socket_dir` is explicitly configured
    in settings.json, that value is authoritative and this caveat doesn't
    apply. When it's unset, the printed value is inferred from this
    process's own TMUX_TMPDIR (or tmux's compiled-in default) and may not
    exactly match what the service resolves -- see
    settings.resolve_tmux_socket_dir()'s docstring for the full precedence.
    """
    from muxplex.settings import resolve_tmux_socket_dir

    print(f'export TMUX_TMPDIR="{resolve_tmux_socket_dir()}"')
    print(
        'Run `eval "$(muxplex env)"` before creating tmux sessions you want '
        'this muxplex instance to see. See AGENTS.md\'s "tmux socket" section.',
        file=sys.stderr,
    )


def cmd_restore(
    dry_run: bool,
    *,
    yes: bool = False,
    force: bool = False,
    forget: bool = False,
) -> None:
    """Restore sessions lost to an unplanned tmux server death.

    This is SESSION_PERSISTENCE_DESIGN.md's "v1b -- explicit restore"
    milestone. The plan is computed from ``manifest.pending_restore``, which
    is populated by the poll loop ONLY when the tmux server's identity
    changes between cycles (a cold start -- see manifest.py's
    update_manifest() for the full discrimination rule). It is never
    populated by an ordinary muxplex restart with tmux left running, and
    never by a session the user deliberately killed while muxplex kept
    running (both cases leave ``pending_restore`` at ``None`` -- a
    tombstoned session cannot reach ``pending_restore`` in the first place,
    see manifest.compute_restore_plan()'s docstring). This command NEVER
    runs automatically; it only ever does something when a human invokes it.

    Execution happens entirely in THIS CLI process -- it does not require
    the muxplex service to be running, and does not route through it (see
    restore.py's module docstring for why). Sessions are created by
    replaying the same `new_session_template` the running server would use
    (sessions.spawn_session_command(), shared with the API's
    POST /api/sessions), sequentially, one at a time.

    Flags:
        --dry-run: show the plan, create/kill/restore nothing. Safe to run
            at any time, including against a live host.
        --yes: skip the interactive confirmation prompt (scripted use).
        --force: proceed even if the pending-restore record is older than
            manifest.RESTORE_MAX_AGE_SECONDS (7 days) -- a stale record is
            more likely to reflect sessions the user has long since
            recreated some other way. Also bypasses the two per-session
            fidelity refusals in restore.execute_restore() (the unrecorded-
            command-pair check and the renamed-session check added by
            docs/plans/2026-08-07-session-rename-plan.md \u00a79.3) for an
            operator who has confirmed the recorded/default command is
            safe to run anyway.
        --forget: clear pending_restore without creating anything -- for
            when the user has decided NOT to restore (e.g. they already
            recreated the sessions manually, or don't want them back).

    Exit code is 0 only when every session in the plan was verified live
    afterward. Any FAIL (session never appeared) makes the exit code 1 so
    scripted/CI callers cannot mistake a partial restore for a complete one.
    """
    import asyncio
    import time

    import muxplex.manifest as manifest_mod
    import muxplex.restore as restore_mod

    if forget:
        count = asyncio.run(restore_mod.forget())
        if count == 0:
            print("No cold start detected. Nothing to forget.")
        else:
            print(
                f"Cleared {count} pending session(s) from the restore record. "
                "Nothing was created or killed."
            )
        return

    plan = asyncio.run(restore_mod.load_plan(force=force))

    if plan is None:
        print("No cold start detected. Nothing to restore.")
        return

    detected_str = (
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(plan.detected_at))
        if plan.detected_at
        else "unknown time"
    )
    print(
        f"Cold start detected {detected_str} "
        f"(lost tmux server pid {plan.lost_server_pid})."
    )
    print(
        f"{plan.total_pending} session(s) were alive under the previous server "
        f"and are not running now.\n"
    )

    if not plan.names:
        print(
            "All previously pending sessions are already live. "
            "Nothing to restore -- this run is a no-op."
        )
        return

    for name in plan.names:
        print(f"  {name}")

    if plan.stale and not force:
        print(
            f"\nThis pending-restore record is more than "
            f"{manifest_mod.RESTORE_MAX_AGE_SECONDS / 86400:.0f} days old and "
            "may no longer reflect reality. Refusing to restore automatically.\n"
            "Use --force to restore anyway, or --forget to clear the record.",
            file=sys.stderr,
        )
        sys.exit(1)

    if dry_run:
        print(
            "\n[DRY RUN] No sessions were created, killed, or restored. "
            "Run `muxplex restore` (without --dry-run) to actually restore them."
        )
        return

    if not yes:
        try:
            answer = input(f"\nRestore {len(plan.names)} session(s)? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("Aborted. Nothing was created or killed.")
            return

    print()
    total = len(plan.names)
    report = asyncio.run(restore_mod.execute_restore(plan.names, force=force))
    for i, result in enumerate(report.results, start=1):
        label = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}[result.status]
        suffix = f"  {result.detail}" if result.detail else ""
        print(f"  [{i:>2}/{total}] {result.name:<28} {label}{suffix}")

    print(
        f"\n{report.ok_count} restored, {report.warn_count} with divergences, "
        f"{report.fail_count} failed."
    )
    print(
        "\nRestored sessions are FRESH SHELLS. Names, layout, and cwd are back;\n"
        "running processes and scrollback are not. For sessions that held an\n"
        "agent: `amplifier resume` in the workspace directory."
    )

    if report.any_failed:
        sys.exit(1)


def config_list() -> None:
    """Show all settings with current values."""
    from muxplex.settings import (
        DEFAULT_SETTINGS,
        SETTINGS_PATH,
        load_settings,
    )

    settings = load_settings()
    print(f"\nmuxplex config ({SETTINGS_PATH})\n")

    for key in DEFAULT_SETTINGS:
        value = settings.get(key)
        default = DEFAULT_SETTINGS[key]
        is_default = value == default
        marker = "" if is_default else " (modified)"
        if isinstance(value, str):
            display = f'"{value}"'
        elif value is None:
            display = "null"
        elif isinstance(value, bool):
            display = "true" if value else "false"
        elif isinstance(value, list):
            display = str(value) if value else "[]"
        else:
            display = str(value)
        print(f"  {key}: {display}{marker}")
    print()


def config_get(key: str) -> None:
    """Show one setting value."""
    from muxplex.settings import DEFAULT_SETTINGS, load_settings

    if key not in DEFAULT_SETTINGS:
        print(f"Unknown setting: {key}", file=sys.stderr)
        print(
            f"Valid keys: {', '.join(sorted(DEFAULT_SETTINGS.keys()))}", file=sys.stderr
        )
        sys.exit(1)

    settings = load_settings()
    value = settings.get(key)
    if isinstance(value, str):
        print(value)
    elif value is None:
        print("null")
    elif isinstance(value, bool):
        print("true" if value else "false")
    else:
        print(value)


def _fail_local_only_key(key: str) -> None:
    """Print the fail-loud error for a LOCAL_ONLY_KEYS key and exit(1).

    `patch_settings()` -- the function every `config set`/`config reset <key>`
    call goes through -- silently SKIPS any key in `settings.LOCAL_ONLY_KEYS`
    (logging only a warning) so that a Bearer-key-holding remote caller can
    never widen one of these fences through the API. That skip is correct
    behavior for `PATCH /api/settings`, but this CLI previously called
    `patch_settings()` unconditionally and printed "success" regardless --
    the exact same skip, but for the LOCAL OPERATOR, who has every right to
    change these keys and was being told nothing happened when in fact
    nothing did. This is one check covering all eight fenced keys
    (`input_enabled`, `input_allowed_sessions`, `new_session_template`,
    `delete_session_template`, `session_commands`, `tmux_socket_dir`,
    `tls_cert`, `tls_key`) -- see `settings.LOCAL_ONLY_KEYS`'s module comment
    for why each one is fenced. The legitimate path for all of them is a
    direct edit of `settings.json` (`save_settings()`/`load_settings()` apply
    no fence); `session_commands` additionally has `muxplex commands add`.
    """
    from muxplex.settings import SETTINGS_PATH

    print(
        f"error: {key!r} is local-file-only and cannot be set through "
        "patch_settings() (the write path `muxplex config set`/`config reset` use).",
        file=sys.stderr,
    )
    if key == "session_commands":
        print("       Use:  muxplex commands add ...", file=sys.stderr)
    print(f"       Or edit {SETTINGS_PATH} directly.", file=sys.stderr)
    sys.exit(1)


def config_set(key: str, raw_value: str) -> None:
    """Set a setting value. Auto-detects type from the default."""
    import json

    from muxplex.settings import DEFAULT_SETTINGS, LOCAL_ONLY_KEYS, patch_settings

    if key not in DEFAULT_SETTINGS:
        print(f"Unknown setting: {key}", file=sys.stderr)
        print(
            f"Valid keys: {', '.join(sorted(DEFAULT_SETTINGS.keys()))}", file=sys.stderr
        )
        sys.exit(1)

    if key in LOCAL_ONLY_KEYS:
        _fail_local_only_key(key)

    default = DEFAULT_SETTINGS[key]

    try:
        if isinstance(default, bool):
            value: object = raw_value.lower() in ("true", "1", "yes", "on")
        elif isinstance(default, int):
            value = int(raw_value)
        elif default is None:
            value = None if raw_value.lower() in ("null", "none", "") else raw_value
        elif isinstance(default, list):
            value = json.loads(raw_value) if raw_value else []
        else:
            value = raw_value
    except (ValueError, json.JSONDecodeError) as e:
        print(f"Invalid value for {key}: {e}", file=sys.stderr)
        sys.exit(1)

    patch_settings({key: value})
    print(f"  {key}: {value}")


def config_reset(key: str | None = None) -> None:
    """Reset one or all settings to defaults."""
    import copy

    from muxplex.settings import (
        DEFAULT_SETTINGS,
        LOCAL_ONLY_KEYS,
        SETTINGS_PATH,
        patch_settings,
        save_settings,
    )

    if key is not None:
        if key not in DEFAULT_SETTINGS:
            print(f"Unknown setting: {key}", file=sys.stderr)
            print(
                f"Valid keys: {', '.join(sorted(DEFAULT_SETTINGS.keys()))}",
                file=sys.stderr,
            )
            sys.exit(1)
        # Same fail-loud fence as config_set() -- `reset <key>` goes through
        # the identical patch_settings() choke point and was subject to the
        # identical silent-skip bug for a LOCAL_ONLY_KEYS key.
        if key in LOCAL_ONLY_KEYS:
            _fail_local_only_key(key)
        patch_settings({key: DEFAULT_SETTINGS[key]})
        print(f"  {key} reset to: {DEFAULT_SETTINGS[key]}")
    else:
        save_settings(copy.deepcopy(DEFAULT_SETTINGS))
        print(f"  All settings reset to defaults ({SETTINGS_PATH})")


# ---------------------------------------------------------------------------
# commands subcommand -- the local-operator write path for session_commands
# ---------------------------------------------------------------------------
#
# `session_commands` is in settings.LOCAL_ONLY_KEYS (see its module comment):
# arbitrary server-executed shell commands, so `PATCH /api/settings` must
# never accept it -- a Bearer-key holder must never be able to both DEFINE a
# pair and SELECT it at create time. That fence is enforced in
# patch_settings(), the API's write path. It is NOT enforced in
# save_settings()/load_settings() -- those are the local-operator path, and a
# CLI subcommand invoked by the human at the keyboard IS the intended writer,
# no different in kind from hand-editing settings.json (just less
# error-prone). This module writes via save_settings(), never
# patch_settings(), which is the whole distinction. See
# docs/API_SEMANTICS.md's "Named session command pairs" section and this
# feature's design doc for the full argument.


def commands_list() -> None:
    """List all configured session command pairs, including the built-in default.

    Uses resolve_session_commands() -- the same server-side resolution
    GET /api/session-commands returns -- so this never re-derives the V1-V7
    fold/validation rules a second time.
    """
    from muxplex.settings import resolve_session_commands

    commands, errors = resolve_session_commands()
    print("\nmuxplex session command pairs\n")
    for cmd in commands:
        marker = "  (built-in)" if cmd["id"] == "default" else ""
        print(f"  {cmd['id']}: {cmd['label']}{marker}")
        print(f"    create: {cmd['new_session_template']}")
        print(f"    delete: {cmd['delete_session_template']}")
    if errors:
        print("\n  Configuration errors (rejected, unavailable until fixed):")
        for err in errors:
            print(f"    - {err}")
    print()


def commands_add(
    cmd_id: str,
    label: str,
    create: str,
    delete: str,
    *,
    replace: bool = False,
    dry_run: bool = False,
) -> None:
    """Add (or, with --replace, overwrite) a named session command pair.

    Validates the FULL prospective `session_commands` list through
    resolve_session_commands() -- the identical V1-V7 rules the server
    applies -- BEFORE writing anything. This is one source of truth: the CLI
    never reimplements the validation rules a second time, it just calls the
    same function the server calls. Refuses to clobber an existing id unless
    *replace* is set. Writes via save_settings() (see module docstring above
    for why that -- not patch_settings() -- is correct here), which means
    this gets the settings-history/ snapshot for free (save_settings() is
    the choke point that writes it).
    """
    import json

    from muxplex.settings import (
        RESERVED_COMMAND_ID,
        load_settings,
        resolve_session_commands,
        save_settings,
    )

    if cmd_id == RESERVED_COMMAND_ID:
        print(
            f"error: id {cmd_id!r} is reserved for the built-in default pair",
            file=sys.stderr,
        )
        sys.exit(1)

    settings = load_settings()
    existing: list = list(settings.get("session_commands") or [])
    existing_idx = next(
        (
            i
            for i, e in enumerate(existing)
            if isinstance(e, dict) and e.get("id") == cmd_id
        ),
        None,
    )
    if existing_idx is not None and not replace:
        print(
            f"error: id {cmd_id!r} already exists. Use --replace to overwrite it.",
            file=sys.stderr,
        )
        sys.exit(1)

    new_entry = {
        "id": cmd_id,
        "label": label,
        "new_session_template": create,
        "delete_session_template": delete,
    }
    prospective = list(existing)
    if existing_idx is not None:
        prospective[existing_idx] = new_entry
    else:
        prospective.append(new_entry)

    settings_for_check = dict(settings)
    settings_for_check["session_commands"] = prospective
    resolved, errors = resolve_session_commands(settings_for_check)
    if cmd_id not in {c["id"] for c in resolved}:
        print("error: this pair would be rejected by validation:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)
    # Any OTHER error belongs to a pre-existing entry, not this one -- surface
    # it as a warning (this add should not be blocked by an unrelated,
    # already-broken entry), never silently.
    other_errors = [e for e in errors if cmd_id not in e]
    if other_errors:
        print(
            "warning: existing configuration has other issue(s), unaffected by this change:",
            file=sys.stderr,
        )
        for err in other_errors:
            print(f"  - {err}", file=sys.stderr)

    if dry_run:
        print(json.dumps(prospective, indent=2))
        return

    settings["session_commands"] = prospective
    save_settings(settings)
    action = "Replaced" if existing_idx is not None else "Added"
    print(f"  {action} command pair {cmd_id!r} ({label})")
    print(f"    create: {create}")
    print(f"    delete: {delete}")


def commands_remove(cmd_id: str) -> None:
    """Remove a named session command pair by id. Writes via save_settings()."""
    from muxplex.settings import RESERVED_COMMAND_ID, load_settings, save_settings

    if cmd_id == RESERVED_COMMAND_ID:
        print(f"error: cannot remove the built-in {cmd_id!r} pair", file=sys.stderr)
        sys.exit(1)

    settings = load_settings()
    existing = list(settings.get("session_commands") or [])
    filtered = [
        e for e in existing if not (isinstance(e, dict) and e.get("id") == cmd_id)
    ]
    if len(filtered) == len(existing):
        print(f"error: no command pair with id {cmd_id!r}", file=sys.stderr)
        sys.exit(1)

    settings["session_commands"] = filtered
    save_settings(settings)
    print(f"  Removed command pair {cmd_id!r}")


def tmux_status() -> None:
    """Show whether muxplex's tmux config is installed and actually loading."""
    from muxplex import tmux_config as tcfg

    st = tcfg.status()
    ver = (
        f"{st.tmux_version[0]}.{st.tmux_version[1]}" if st.tmux_version else "not found"
    )
    print("\nmuxplex tmux config\n")
    print(f"  tmux: {ver}")
    print(f"  tmux reads: {', '.join(str(p) for p in st.loaded) or '(no config yet)'}")
    print(f"  install target: {st.target}")
    print(f"  status: {'installed' if st.installed else 'not installed'}")
    if st.is_symlink:
        print(f"  note: target is a symlink -> {st.symlink_target}")

    if st.outranks_user:
        print("\n  WARNING: a muxplex block sits in a file that loads AFTER the")
        print("  install target, so muxplex settings would override your own:")
        for p in st.misplaced:
            print(f"    {p}")
        print("  Fix with: muxplex tmux install")

    if st.fragments:
        print("\n  fragments (applied in this order):")
        for f in st.fragments:
            print(f"    {f.name}")

    if st.installed:
        v = tcfg.verify()
        print(f"\n  live check: {'loading OK' if v['loaded'] else 'NOT LOADING'}")
        if not v["loaded"]:
            print(
                "  tmux started but muxplex settings were not applied.",
                file=sys.stderr,
            )
            sys.exit(1)
    print()


def tmux_install(dry_run: bool = False, allow_symlink: bool = False) -> None:
    """Install muxplex's tmux config. Safe, verified, reversible."""
    from muxplex import tmux_config as tcfg
    from muxplex.settings import load_settings

    theme = str(load_settings().get("tmux_theme") or "brand")
    copy_mode = str(load_settings().get("tmux_copy_mode") or "desktop")

    try:
        r = tcfg.install(
            theme=theme,
            copy_mode=copy_mode,
            allow_symlink=allow_symlink,
            dry_run=dry_run,
        )
    except tcfg.TmuxConfigError as e:
        print(f"\nRefusing to continue:\n\n  {e}\n", file=sys.stderr)
        sys.exit(1)

    if dry_run:
        print(f"Would render theme {theme!r} into {tcfg.TMUX_D_PATH}")
        print(f"Would edit {r['target']}\n")
        print(r["diff"] or "(no change needed)")
        return

    print(f"Rendered theme {theme!r} into {tcfg.TMUX_D_PATH}")
    if not r["changed"]:
        print(f"{r['target']} already set up -- nothing to change.")
    elif r["created"]:
        print(f"Created {r['target']}")
    else:
        print(f"Updated {r['target']}  (backup: {r['backup']})")
        print("  Added one line at the top, so anything in your own config wins.")

    v = tcfg.verify()
    if not v["loaded"]:
        print("\nFAILED: tmux did not load the config after install.", file=sys.stderr)
        print("Run 'muxplex tmux uninstall' to revert.", file=sys.stderr)
        sys.exit(1)
    print("Verified: started tmux and confirmed the settings are applied.")

    if tcfg.apply_live()["applied"]:
        print("Applied to your running tmux server -- no restart needed.")
    else:
        print("No tmux server running yet; it will apply on your next session.")


def tmux_uninstall(allow_symlink: bool = False) -> None:
    """Remove the managed block. Everything else is left exactly as it was."""
    from muxplex import tmux_config as tcfg

    try:
        r = tcfg.uninstall(allow_symlink=allow_symlink)
    except tcfg.TmuxConfigError as e:
        print(f"\nRefusing to continue:\n\n  {e}\n", file=sys.stderr)
        sys.exit(1)

    if not r["changed"]:
        print("Nothing to remove -- muxplex tmux config is not installed.")
        return
    for p in r["removed_from"]:
        print(f"Removed the muxplex block from {p}")
    print("Your own settings were left exactly as they were.")
    print(f"Fragments in {tcfg.TMUX_D_PATH} were kept (delete manually if you want).")


def setup_tls(method: str = "auto") -> None:
    """Generate TLS certificates and update settings.

    Auto-detection chain (method='auto'): Tailscale → mkcert → self-signed.
    Use --method to force a specific certificate source.
    """
    from muxplex.settings import (
        SETTINGS_PATH,
        load_settings,
        save_settings,
    )
    from muxplex.tls import (
        _default_hostnames,
        _default_lan_ip,
        _default_tailnet_name,
        detect_mkcert,
        detect_tailscale,
        generate_leaf_signed_by_ca,
        generate_local_ca,
        generate_mkcert,
        generate_self_signed,
        generate_tailscale,
        get_cert_info,
    )

    config_dir = SETTINGS_PATH.parent
    cert_path = config_dir / "muxplex.crt"
    key_path = config_dir / "muxplex.key"

    # Check for existing certificates and prompt before overwriting
    _settings = load_settings()
    _existing_cert = _settings.get("tls_cert", "")
    _existing_key = _settings.get("tls_key", "")
    if _existing_cert and _existing_key and Path(_existing_cert).exists():
        _info = get_cert_info(_existing_cert)
        if _info is not None:
            print(f"TLS already configured (expires {str(_info['expires'])[:10]}).")
        try:
            _answer = input("Regenerate? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            _answer = "n"
        if _answer.lower() not in ("y", "yes"):
            print("Keeping existing certificates.")
            return

    result = None
    tailscale_info = None

    # Step 1: Try Tailscale
    if method in ("auto", "tailscale"):
        tailscale_info = detect_tailscale()
        if tailscale_info:
            hostname = tailscale_info["hostname"]
            print(f"  Detected Tailscale: {hostname}")
            result = generate_tailscale(cert_path, key_path, hostname)
            if result:
                print("  Tailscale certificate obtained")
            else:
                print("  Tailscale certificate generation failed")
        if method == "tailscale" and result is None:
            print(
                "Error: Tailscale not available or certificate generation failed",
                file=sys.stderr,
            )
            sys.exit(1)

    # Step 2: Try mkcert
    if result is None and method in ("auto", "mkcert"):
        if detect_mkcert():
            print("  Detected mkcert, generating certificate...")
            extra_hostnames = None
            if tailscale_info:
                extra_hostnames = tailscale_info.get("cert_domains") or None
            result = generate_mkcert(
                cert_path, key_path, extra_hostnames=extra_hostnames
            )
        else:
            if method == "mkcert":
                print(
                    "Error: mkcert not found. Install from https://mkcert.dev",
                    file=sys.stderr,
                )
                sys.exit(1)

    # Step 3: Try self-signed
    if result is None and method in ("auto", "selfsigned"):
        result = generate_self_signed(cert_path, key_path)

    # Step 3.5: Try local CA (explicit opt-in only — not part of "auto").
    # Generates a persistent local CA in ~/.config/muxplex/ca/ and signs
    # a short-lived leaf with it. Install the CA on each client to get
    # browser-trusted HTTPS without Tailscale or a public domain.
    if result is None and method == "ca":
        ca_dir = config_dir / "ca"
        ca_cert_path = ca_dir / "muxplex-ca.crt"
        ca_key_path = ca_dir / "muxplex-ca.key"

        ca_info = generate_local_ca(ca_cert_path, ca_key_path)
        if ca_info["regenerated"]:
            print(f"  Generated local CA at {ca_cert_path}")
        else:
            print(f"  Reusing existing local CA at {ca_cert_path}")

        # Build the SAN list: defaults + tailnet name (if reachable) + LAN IP.
        leaf_hostnames: list[str] = list(_default_hostnames())
        tailnet_name = _default_tailnet_name()
        if tailnet_name and tailnet_name not in leaf_hostnames:
            leaf_hostnames.append(tailnet_name)

        leaf_ips: list[str] = ["127.0.0.1", "::1"]
        lan_ip = _default_lan_ip()
        if lan_ip and lan_ip not in leaf_ips:
            leaf_ips.append(lan_ip)

        result = generate_leaf_signed_by_ca(
            ca_cert_path,
            ca_key_path,
            cert_path,
            key_path,
            hostnames=leaf_hostnames,
            ip_addresses=leaf_ips,
        )
        # Decorate the result with the CA path so the success block can
        # surface install instructions.
        if result:
            result["ca_cert_path"] = str(ca_cert_path)
            result["ca_regenerated"] = ca_info["regenerated"]

    # Step 4: Final failure check
    if result is None:
        print(
            "Error: TLS certificate generation failed with all methods",
            file=sys.stderr,
        )
        sys.exit(1)

    # Update settings with cert/key paths. tls_cert/tls_key are in
    # settings.LOCAL_ONLY_KEYS (patch_settings() silently ignores them --
    # see that fence's module comment). This CLI command IS the local
    # operator action the fence is meant to allow, so it writes directly
    # via save_settings() rather than going through the API-facing
    # patch_settings() filter.
    _settings["tls_cert"] = str(cert_path)
    _settings["tls_key"] = str(key_path)
    save_settings(_settings)

    # Print cert info
    hostnames_str = ", ".join(result["hostnames"])
    expiry_str = (
        result["expires"].strftime("%Y-%m-%d")
        if hasattr(result["expires"], "strftime")
        else str(result["expires"])
    )

    print("TLS setup complete")
    print(f"  Certificate: {result['cert_path']}")
    print(f"  Key:         {result['key_path']}")
    print(f"  Hostnames:   {hostnames_str}")
    print(f"  Expires:     {expiry_str}")
    print()

    # Method-specific warnings
    method_used = result.get("method", "")
    if method_used == "selfsigned":
        print(
            "  Note: Browsers will show a security warning for self-signed certificates."
        )
        print("  Consider using mkcert or Tailscale for a trusted certificate.")
        print()
    elif method_used == "tailscale":
        print("  Note: Tailscale certificates expire after 90 days.")
        print("  Run 'muxplex setup-tls' to renew.")
        print()
    elif method_used == "ca":
        ca_cert_path_str = result.get("ca_cert_path", "")
        print(f"  Local CA:    {ca_cert_path_str}")
        print()
        print("  Install the CA on each client to eliminate browser warnings.")
        print("  The leaf rotates without re-trusting; the CA is what you trust.")
        print()
        print("  Windows (PowerShell, no admin needed):")
        print(
            "    Import-Certificate -FilePath <path-to-ca.crt> "
            "-CertStoreLocation Cert:\\CurrentUser\\Root"
        )
        print()
        print("  macOS:")
        print(
            "    sudo security add-trusted-cert -d -r trustRoot "
            "-k /Library/Keychains/System.keychain <path-to-ca.crt>"
        )
        print()
        print("  Linux (system-wide):")
        print("    sudo cp <path-to-ca.crt> /usr/local/share/ca-certificates/")
        print("    sudo update-ca-certificates")
        print()
        print("  Leaf cert rotates yearly — re-run 'muxplex setup-tls --method ca'")
        print("  to generate a fresh leaf signed by the same CA (no client re-trust).")
        print()

    print("  Restart service to apply: muxplex service restart")


def setup_tls_status() -> None:
    """Display the current TLS configuration status."""
    from muxplex.settings import load_settings
    from muxplex.tls import get_cert_info

    settings = load_settings()
    tls_cert = settings.get("tls_cert", "")
    tls_key = settings.get("tls_key", "")

    print("muxplex TLS status")
    print()

    if not tls_cert or not tls_key:
        print("  TLS: not configured")
        print("  Run: muxplex setup-tls")
        return

    print(f"  Certificate: {tls_cert}")
    print(f"  Key:         {tls_key}")

    cert_info = get_cert_info(tls_cert)
    if cert_info is None:
        print("  Status: configured but cert not readable")
        return

    hostnames_str = ", ".join(cert_info["hostnames"])
    expires = cert_info["expires"]
    expiry_str = (
        expires.strftime("%Y-%m-%d") if hasattr(expires, "strftime") else str(expires)
    )
    print(f"  Hostnames:   {hostnames_str}")
    print(f"  Expires:     {expiry_str}")
    print("  Status: enabled")


def _add_serve_flags(parser: argparse.ArgumentParser) -> None:
    """Add --host, --port, --auth, --session-ttl, --tls-cert, --tls-key flags to a parser.

    All default to None so serve() can distinguish 'not passed' from
    'passed the default value'.
    """
    parser.add_argument(
        "--host",
        default=None,
        help="Bind host (default: from settings.json, then 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port (default: from settings.json, then 8088)",
    )
    parser.add_argument(
        "--auth",
        choices=["pam", "password"],
        default=None,
        help="Auth method: pam or password (default: from settings.json, then pam)",
    )
    parser.add_argument(
        "--session-ttl",
        type=int,
        default=None,
        dest="session_ttl",
        help="Session TTL in seconds (default: from settings.json, then 604800; 0 = browser session)",
    )
    parser.add_argument(
        "--tls-cert",
        default=None,
        dest="tls_cert",
        help="Path to TLS certificate file (default: from settings.json)",
    )
    parser.add_argument(
        "--tls-key",
        default=None,
        dest="tls_key",
        help="Path to TLS private key file (default: from settings.json)",
    )
    parser.add_argument(
        "--force-take-port",
        action="store_true",
        dest="force_take_port",
        help=(
            "Terminate whatever holds the port, even a healthy running muxplex. "
            "Without this, startup refuses rather than killing a live server."
        ),
    )


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="muxplex",
        description="muxplex — web-based tmux session dashboard",
    )
    _add_serve_flags(parser)

    sub = parser.add_subparsers(dest="command")

    serve_parser = sub.add_parser("serve", help="Start the server (default)")
    _add_serve_flags(serve_parser)

    service_parser = sub.add_parser(
        "service", help="Manage the muxplex background service"
    )
    service_sub = service_parser.add_subparsers(dest="service_command")
    service_sub.add_parser("install", help="Install + enable + start the service")
    service_sub.add_parser("uninstall", help="Stop + disable + remove the service")
    service_sub.add_parser("start", help="Start the service")
    service_sub.add_parser("stop", help="Stop the service")
    service_sub.add_parser("restart", help="Stop + start the service")
    service_sub.add_parser("status", help="Show service status")
    service_sub.add_parser("logs", help="Tail service logs")

    sub.add_parser("show-password", help="Show the current muxplex password")

    sub.add_parser(
        "reset-secret", help="Regenerate signing secret (invalidates sessions)"
    )

    sub.add_parser(
        "reset-device-id",
        help="Regenerate device identity UUID (orphans existing session keys)",
    )

    sub.add_parser(
        "generate-federation-key",
        help="Generate a random federation key and write it to disk",
    )

    sub.add_parser("doctor", help="Check dependencies and system status")

    ensure_agent_parser = sub.add_parser(
        "ensure-agent",
        help="Install/verify amplifier-agent for the embedded agent panel",
    )
    ensure_agent_parser.add_argument(
        "--force",
        action="store_true",
        help="Reinstall even if amplifier-agent is already at the pinned version",
    )

    sub.add_parser(
        "env",
        help='Print `eval`-able TMUX_TMPDIR export (use: eval "$(muxplex env)")',
    )

    restore_parser = sub.add_parser(
        "restore",
        help="Recreate sessions lost to an unplanned tmux server death (see SESSION_PERSISTENCE_DESIGN.md)",
    )
    restore_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the restore plan without creating, killing, or restoring anything",
    )
    restore_parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt (for scripted use)",
    )
    restore_parser.add_argument(
        "--force",
        action="store_true",
        help="Restore even if the pending-restore record is older than 7 days",
    )
    restore_parser.add_argument(
        "--forget",
        action="store_true",
        help="Clear the pending-restore record without creating anything",
    )

    upgrade_parser = sub.add_parser(
        "upgrade",
        aliases=["update"],
        help="Upgrade muxplex to latest version and restart service",
    )
    upgrade_parser.add_argument(
        "--force",
        action="store_true",
        help="Force reinstall even if already up to date",
    )

    # Hidden (no `help=`, so it never appears in `--help` output -- see
    # argparse's own _SubParsersAction.add_parser: a subparser is only
    # added to the printed choices list when `help` is passed). Internal
    # only -- launched by `upgrade()` itself (muxplex-lf6) as a fresh
    # subprocess to run post-install steps against the just-installed
    # code. Never intended to be run by a human directly.
    sub.add_parser("_finish-upgrade")

    setup_tls_parser = sub.add_parser(
        "setup-tls", help="Generate TLS certificate and configure HTTPS"
    )
    setup_tls_parser.add_argument(
        "--method",
        choices=["auto", "tailscale", "mkcert", "selfsigned", "ca"],
        default="auto",
        help="Certificate generation method (default: auto). 'ca' creates "
        "a persistent local CA in ~/.config/muxplex/ca/ and signs a leaf "
        "cert with it — install the CA on each client to eliminate browser "
        "warnings without requiring Tailscale or a public domain.",
    )
    setup_tls_parser.add_argument(
        "--status",
        action="store_true",
        help="Show current TLS configuration status",
    )

    config_parser = sub.add_parser("config", help="View and manage settings")
    config_sub = config_parser.add_subparsers(dest="config_command")
    config_sub.add_parser("list", help="Show all settings (default)")
    config_get_parser = config_sub.add_parser("get", help="Show one setting")
    config_get_parser.add_argument("key", help="Setting key")
    config_set_parser = config_sub.add_parser("set", help="Set a setting value")
    config_set_parser.add_argument("key", help="Setting key")
    config_set_parser.add_argument("value", help="New value")
    config_reset_parser = config_sub.add_parser("reset", help="Reset to defaults")
    config_reset_parser.add_argument(
        "key", nargs="?", help="Setting key (omit to reset all)"
    )

    commands_parser = sub.add_parser(
        "commands",
        help="Manage named session command pairs (session_commands is "
        "local-file-only; this is the local-operator write path)",
    )
    commands_sub = commands_parser.add_subparsers(dest="commands_command")
    commands_sub.add_parser(
        "list", help="List configured command pairs, including the built-in default"
    )
    commands_add_parser = commands_sub.add_parser(
        "add", help="Add (or --replace) a named command pair"
    )
    commands_add_parser.add_argument(
        "--id",
        dest="cmd_id",
        required=True,
        help="Pair id (lowercase alphanumeric, _/-)",
    )
    commands_add_parser.add_argument("--label", required=True, help="Display label")
    commands_add_parser.add_argument(
        "--create",
        required=True,
        help="Create/new-session template; must contain {name}",
    )
    commands_add_parser.add_argument(
        "--delete",
        required=True,
        help="Delete/kill-session template; must contain {name}",
    )
    commands_add_parser.add_argument(
        "--replace", action="store_true", help="Overwrite an existing id"
    )
    commands_add_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resulting session_commands JSON without writing",
    )
    commands_remove_parser = commands_sub.add_parser(
        "remove", help="Remove a command pair by id"
    )
    commands_remove_parser.add_argument("--id", dest="cmd_id", required=True)

    tmux_parser = sub.add_parser("tmux", help="Manage muxplex's tmux configuration")
    tmux_sub = tmux_parser.add_subparsers(dest="tmux_command")
    tmux_sub.add_parser("status", help="Show whether the tmux config is active")
    tmux_install_parser = tmux_sub.add_parser(
        "install", help="Install muxplex's tmux config (safe, reversible)"
    )
    tmux_install_parser.add_argument(
        "--dry-run", action="store_true", help="Show the diff, change nothing"
    )
    tmux_install_parser.add_argument(
        "--allow-symlink",
        action="store_true",
        help="Permit writing through a symlinked tmux.conf (e.g. a dotfiles repo)",
    )
    tmux_uninstall_parser = tmux_sub.add_parser(
        "uninstall", help="Remove the managed block, keep everything else"
    )
    tmux_uninstall_parser.add_argument("--allow-symlink", action="store_true")

    args = parser.parse_args()

    if args.command == "show-password":
        show_password()
    elif args.command == "reset-secret":
        reset_secret()
    elif args.command == "reset-device-id":
        reset_device_id_command()
    elif args.command == "generate-federation-key":
        generate_federation_key()
    elif args.command == "doctor":
        doctor()
    elif args.command == "ensure-agent":
        ok = ensure_agent(force=getattr(args, "force", False))
        sys.exit(0 if ok else 1)
    elif args.command == "env":
        cmd_env()
    elif args.command == "restore":
        cmd_restore(
            dry_run=getattr(args, "dry_run", False),
            yes=getattr(args, "yes", False),
            force=getattr(args, "force", False),
            forget=getattr(args, "forget", False),
        )
    elif args.command in ("upgrade", "update"):
        upgrade(force=getattr(args, "force", False))
    elif args.command == "_finish-upgrade":
        sys.exit(_finish_upgrade())
    elif args.command == "config":
        cmd = getattr(args, "config_command", None)
        if cmd == "get":
            config_get(args.key)
        elif cmd == "set":
            config_set(args.key, args.value)
        elif cmd == "reset":
            config_reset(getattr(args, "key", None))
        else:
            # Default: list (no subcommand or explicit "list")
            config_list()
    elif args.command == "commands":
        cmd = getattr(args, "commands_command", None)
        if cmd == "add":
            commands_add(
                args.cmd_id,
                args.label,
                args.create,
                args.delete,
                replace=args.replace,
                dry_run=args.dry_run,
            )
        elif cmd == "remove":
            commands_remove(args.cmd_id)
        else:
            # Default: list (no subcommand or explicit "list")
            commands_list()
    elif args.command == "setup-tls":
        if args.status:
            setup_tls_status()
        else:
            setup_tls(method=args.method)
    elif args.command == "service":
        from muxplex.service import (
            service_install,
            service_logs,
            service_restart,
            service_start,
            service_status,
            service_stop,
            service_uninstall,
        )

        cmd = getattr(args, "service_command", None)
        if cmd == "install":
            service_install()
        elif cmd == "uninstall":
            service_uninstall()
        elif cmd == "start":
            service_start()
        elif cmd == "stop":
            service_stop()
        elif cmd == "restart":
            service_restart()
        elif cmd == "status":
            service_status()
        elif cmd == "logs":
            service_logs()
        else:
            service_parser.print_help()
    elif args.command == "tmux":
        cmd = getattr(args, "tmux_command", None)
        if cmd == "status":
            tmux_status()
        elif cmd == "install":
            tmux_install(dry_run=args.dry_run, allow_symlink=args.allow_symlink)
        elif cmd == "uninstall":
            tmux_uninstall(allow_symlink=args.allow_symlink)
        else:
            tmux_parser.print_help()
    else:
        _check_dependencies()
        serve(
            host=args.host,
            port=args.port,
            auth=args.auth,
            session_ttl=args.session_ttl,
            tls_cert=args.tls_cert,
            tls_key=args.tls_key,
            force_take_port=getattr(args, "force_take_port", False),
        )
