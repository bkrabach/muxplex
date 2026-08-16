#!/usr/bin/env python3
"""Fail loud when the browser-verification container has drifted from this branch.

WHY THIS EXISTS
---------------
The container at MUXPLEX_CONTAINER_SRC is the only place browser verification
happens, and browser proof is this project's reality gate. Four separate times a
change was reported working on non-browser evidence and turned out dead,
clobbered, or wrong.

That gate only means something if the tree being clicked IS the tree being
committed. In muxplex-cxd the container was deliberately made a real git
checkout so edits would be reviewable and collisions visible. It then decayed
silently: work moved to host worktrees, the container was never re-synced, and
browser verification was done by hand-patching container files to approximate
whatever the branch held at the time. By the time anyone looked (muxplex-cky) it
was 54 commits behind with 3198 lines of uncommitted hand-patching on top.

Nothing detected that. A person happened to look. This script is the machine
that looks instead.

WHAT IT CHECKS
--------------
1. The container's HEAD equals this host checkout's HEAD.
2. The container's working tree is clean.

Either one failing means a browser result from that container is evidence about
some tree that is not this one.

EXIT CODES
----------
0  IN SYNC   -- container HEAD == host HEAD, container tree clean.
1  DRIFT     -- the thing this exists to catch. Loud, actionable, non-zero.
2  UNKNOWN   -- could not verify (no twin CLI, container not running, no git
                checkout in the container). Deliberately NOT 0: "I could not
                look" must never be reported in the same breath as "I looked
                and it was fine". It is not 1 either, because a developer
                without this container has no drift to fix -- see NOT-A-FALLBACK.

NOT-A-FALLBACK
--------------
UNKNOWN is not a soft pass. It is a third, named state with its own exit code,
printed as loudly as DRIFT. `make check` treats it as non-fatal because a
contributor with no LAN twin genuinely has no container to be stale; the check
still says, on screen, that it did not verify. What it never does is print a
reassuring "in sync" it has not earned.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

CONTAINER = os.environ.get("MUXPLEX_CONTAINER", "muxplex-lan-twin")
CONTAINER_SRC = os.environ.get("MUXPLEX_CONTAINER_SRC", "/opt/muxplex")
TWIN_CLI = "amplifier-digital-twin"
TIMEOUT_S = 60

IN_SYNC, DRIFT, UNKNOWN = 0, 1, 2


def _say(level: str, msg: str) -> None:
    print(f"[container-drift] {level}: {msg}")


def _host_git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _container_exec(script: str) -> tuple[int, str, str]:
    """Run a shell snippet in the container. Returns (exit_code, stdout, stderr).

    The twin CLI prints a JSON envelope on stdout carrying the real streams and
    the real exit code; a non-zero code inside the container still leaves the
    CLI itself at 0, so the envelope is the only honest source.
    """
    proc = subprocess.run(
        [TWIN_CLI, "exec", CONTAINER, "--", "bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        return 255, "", proc.stderr.strip() or f"{TWIN_CLI} exec failed"
    try:
        env = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return 255, "", f"unparseable {TWIN_CLI} response: {proc.stdout[:200]!r}"
    return int(env.get("exit_code", 255)), env.get("stdout", ""), env.get("stderr", "")


def main() -> int:
    repo = Path(__file__).resolve().parent.parent

    try:
        host_head = _host_git(repo, "rev-parse", "HEAD")
        host_branch = _host_git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    except subprocess.CalledProcessError as exc:
        _say("UNKNOWN", f"could not read host HEAD from {repo}: {exc}")
        return UNKNOWN

    if shutil.which(TWIN_CLI) is None:
        _say("UNKNOWN", f"{TWIN_CLI} not on PATH -- no container to compare against.")
        _say("UNKNOWN", "Not verified. This is not a pass.")
        return UNKNOWN

    try:
        code, out, err = _container_exec(
            f"cd {CONTAINER_SRC} 2>/dev/null || exit 90; "
            "git rev-parse HEAD 2>/dev/null || exit 91; "
            "git status --porcelain --untracked-files=all"
        )
    except subprocess.TimeoutExpired:
        _say("UNKNOWN", f"{TWIN_CLI} exec timed out after {TIMEOUT_S}s.")
        return UNKNOWN

    if code == 90:
        _say("UNKNOWN", f"{CONTAINER_SRC} does not exist in container {CONTAINER!r}.")
        return UNKNOWN
    if code == 91:
        _say("UNKNOWN", f"{CONTAINER_SRC} in {CONTAINER!r} is not a git checkout.")
        _say("UNKNOWN", "That is itself the muxplex-cxd decay mode. Re-establish it.")
        return UNKNOWN
    if code != 0:
        _say(
            "UNKNOWN", f"could not query container {CONTAINER!r}: {err or out}".strip()
        )
        _say("UNKNOWN", "Not verified. This is not a pass.")
        return UNKNOWN

    lines = out.splitlines()
    container_head = lines[0].strip() if lines else ""
    dirty = [ln for ln in lines[1:] if ln.strip()]

    head_ok = container_head == host_head
    if head_ok and not dirty:
        _say(
            "IN SYNC",
            f"{CONTAINER}:{CONTAINER_SRC} == {host_branch} @ {host_head[:7]}, clean.",
        )
        return IN_SYNC

    _say("DRIFT", f"container {CONTAINER!r} does not match this checkout.")
    print()
    print(f"  host  {host_branch:<24} {host_head}")
    print(f"  cont  {CONTAINER_SRC:<24} {container_head or '(unknown)'}")
    if not head_ok:
        behind = ""
        try:
            if container_head:
                n = _host_git(
                    repo, "rev-list", "--count", f"{container_head}..{host_head}"
                )
                behind = f"  ({n} commits behind this HEAD)"
        except subprocess.CalledProcessError:
            behind = (
                "  (commit not present on this host -- container has unknown history)"
            )
        print(f"  -> HEAD MISMATCH{behind}")
    if dirty:
        print(f"  -> WORKING TREE DIRTY ({len(dirty)} paths):")
        for ln in dirty[:20]:
            print(f"       {ln}")
        if len(dirty) > 20:
            print(f"       ... and {len(dirty) - 20} more")
    print()
    print("  Browser evidence from this container is NOT evidence about this branch.")
    print("  Reconcile before trusting any browser result. See scripts/README.md")
    print(
        "  ('container drift') for the bundle + file-push re-sync that muxplex-cky used,"
    )
    print(
        "  including how to check whether the dirty files hold anything unique first."
    )
    print()
    return DRIFT


if __name__ == "__main__":
    sys.exit(main())
