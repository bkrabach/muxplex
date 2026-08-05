"""muxplex CLI — web-based tmux session dashboard."""

import argparse
import logging
import os
import platform
import secrets as _secrets
import shutil
import subprocess
import sys
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


def _get_install_info() -> dict:
    """Detect how muxplex was installed using PEP 610 direct_url.json.

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

    Returns dict with keys:
      source: 'pypi' | 'git' | 'editable' | 'local-dir' | 'archive' | 'unknown'
      version: installed version string
      commit: installed commit sha (git only, may be '')
      url: origin verbatim from direct_url.json -- a git remote URL for
           'git', a file:// URL for 'editable'/'local-dir'/'archive'; None
           for 'pypi'/'unknown'
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
        dist = distribution("muxplex")
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
        pass

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

    if source == "archive":
        url = info.get("url") or "?"
        path = _file_url_to_path(url)
        return f"local archive at {path}" if path else f"archive at {url}"

    return "unrecognized install record"


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
        from muxplex.identity import load_device_id  # noqa: PLC0415

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

        # Bell hook: honest "armed" means a real delivery probe succeeded
        # (see main.py's _arm_bell_hook()), not merely that tmux accepted
        # `set-hook` -- surfaced here the same way TLS expiry is below: a
        # non-fatal advisory line, not a hard failure. Skipped for a
        # different machine's muxplex (running_info above) -- that host's
        # hook state says nothing about this one. `.get()` also makes this
        # silently absent against an older peer that predates the field
        # (version tolerance, per AGENTS.md).
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
                print(f"  {ok_mark} Bell hook: armed (delivery probe confirmed)")

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
                    from muxplex.settings import load_settings  # noqa: PLC0415

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
    print("  Installing latest version...")
    try:
        # Bug 3: dispatch — uv-tool-managed gets --reinstall; plain uv/pip otherwise
        if uv_path:
            if _is_uv_managed:
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
                    "muxplex",
                ]
            else:
                install_cmd = [
                    uv_path,
                    "tool",
                    "install",
                    install_target,
                    "--refresh",
                    "--force",
                ]
            result = subprocess.run(install_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"  ERROR: uv tool install failed:\n{result.stderr}")
                _install_failed = True
            elif not _verify_version_moved(info["version"], update_available):
                _install_failed = True
            else:
                print("  Installed successfully")
        else:
            # uv absent → fall back to pip (probe known locations off PATH)
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
                    print("  Installed successfully")
            else:
                print("  ERROR: neither uv nor pip found — cannot upgrade")
                _install_failed = True

        if not _install_failed:
            # 3. Regenerate service file (picks up any plist/unit changes)
            if sys.platform == "darwin" or _have_systemctl():
                print("  Regenerating service file...")
                from muxplex.service import service_install

                service_install()
            else:
                print("  ! systemctl not found — skipping service file regeneration")
    finally:
        # 4. Restart service — ALWAYS runs after install, even on failure (best-effort).
        if sys.platform == "darwin":
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

    # 5. Wait for the service to actually be ready before verifying. Systemd
    # reports the unit "active" (the check above) the instant the process
    # starts -- not once uvicorn has finished binding the configured
    # host:port -- so calling doctor() immediately races a server that is
    # actually healthy, just not listening yet, and its "Running:" check
    # reports a false "not serving" warning that a manual `muxplex doctor`
    # moments later would not show. See _wait_for_service_ready's docstring.
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
            recreated some other way.
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
    report = asyncio.run(restore_mod.execute_restore(plan.names))
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
    from muxplex import tmux_config as tcfg  # noqa: PLC0415

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
    from muxplex import tmux_config as tcfg  # noqa: PLC0415
    from muxplex.settings import load_settings  # noqa: PLC0415

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
    from muxplex import tmux_config as tcfg  # noqa: PLC0415

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
