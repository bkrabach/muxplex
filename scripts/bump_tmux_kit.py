#!/usr/bin/env python3
"""Bump the tmux-kit dependency pin and its git source tag together.

`pyproject.toml` carries tmux-kit's version in TWO places that must always
agree (see AGENTS.md's "tmux-kit pin/tag agreement" section):

    [project.dependencies]
    "tmux-kit==X.Y.Z"

    [tool.uv.sources]
    tmux-kit = { git = "https://github.com/bkrabach/tmux-kit", tag = "vX.Y.Z" }

This script is the single place that knows how to move both, in lockstep,
via targeted regex substitution -- not a TOML parse/dump round-trip -- so
every surrounding comment (both entries sit inside long explanatory blocks)
and the rest of the file's formatting is left byte-for-byte untouched.

It does no network I/O and knows nothing about GitHub, git, or CI. Finding
"what's the newest tmux-kit release" and "open a PR" are the calling
workflow's job (mechanism split from policy); this script only answers
"what's pinned now" and "rewrite the pin to X".

Subcommands:

    bump_tmux_kit.py check [--pyproject PATH]
        Print {"pin_version": ..., "tag_version": ..., "agree": bool} and
        exit 0 if the pin and tag agree, 1 if they don't (this should never
        happen given the CI guard, but a caller must not silently bump on
        top of pre-existing drift).

    bump_tmux_kit.py bump NEW_VERSION [--pyproject PATH]
        Rewrite both locations to NEW_VERSION, but ONLY if NEW_VERSION is
        strictly newer than the current pin. Always prints a JSON result
        and exits 0 for either outcome (bumped or no-op is not an error);
        exits 1 only for a real failure (malformed file, pre-existing
        drift, bad version string).

Example:
    >>> bump_tmux_kit.py check
    {"pin_version": "0.3.5", "tag_version": "0.3.5", "agree": true}

    >>> bump_tmux_kit.py bump 0.4.0
    {"bumped": true, "from": "0.3.5", "to": "0.4.0"}
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# Matches the exact-pin line in [project.dependencies], e.g.:
#     "tmux-kit==0.3.5",
_DEP_PATTERN = re.compile(r'"tmux-kit==([0-9]+\.[0-9]+\.[0-9]+)"')

# Matches the [tool.uv.sources] git source line, e.g.:
#     tmux-kit = { git = "https://github.com/bkrabach/tmux-kit", tag = "v0.3.5" }
_SOURCE_PATTERN = re.compile(
    r'(tmux-kit = \{ git = "https://github\.com/bkrabach/tmux-kit", tag = "v)'
    r"([0-9]+\.[0-9]+\.[0-9]+)"
    r'(" \})'
)


class PyprojectError(SystemExit):
    """Raised (as a SystemExit) for a malformed or unexpected pyproject.toml."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


def _version_tuple(version: str) -> tuple[int, ...]:
    parts = version.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise PyprojectError(f"not a plain X.Y.Z version: {version!r}")
    return tuple(int(p) for p in parts)


def read_pin_version(text: str) -> str:
    matches = _DEP_PATTERN.findall(text)
    if not matches:
        raise PyprojectError(
            "no [project.dependencies] tmux-kit==X.Y.Z pin found in pyproject.toml"
        )
    if len(matches) > 1:
        raise PyprojectError(
            f"expected exactly one tmux-kit==X.Y.Z pin, found {len(matches)}"
        )
    return matches[0]


def read_source_tag_version(text: str) -> str:
    matches = _SOURCE_PATTERN.findall(text)
    if not matches:
        raise PyprojectError(
            "no [tool.uv.sources] tmux-kit git tag found in pyproject.toml"
        )
    if len(matches) > 1:
        raise PyprojectError(
            f"expected exactly one [tool.uv.sources] tmux-kit tag, found {len(matches)}"
        )
    return matches[0][1]


def rewrite(text: str, new_version: str) -> str:
    """Rewrite both the pin and the source tag to new_version.

    Callers must already know new_version is strictly newer -- this
    function performs the substitution unconditionally.
    """
    updated, dep_count = _DEP_PATTERN.subn(f'"tmux-kit=={new_version}"', text)
    if dep_count != 1:
        raise PyprojectError(
            f"expected exactly one tmux-kit==X.Y.Z pin to rewrite, touched {dep_count}"
        )
    updated, source_count = _SOURCE_PATTERN.subn(rf"\g<1>{new_version}\g<3>", updated)
    if source_count != 1:
        raise PyprojectError(
            f"expected exactly one [tool.uv.sources] tmux-kit tag to rewrite, touched {source_count}"
        )
    return updated


def cmd_check(args: argparse.Namespace) -> int:
    text = args.pyproject.read_text(encoding="utf-8")
    pin_version = read_pin_version(text)
    tag_version = read_source_tag_version(text)
    agree = pin_version == tag_version
    print(
        json.dumps(
            {"pin_version": pin_version, "tag_version": tag_version, "agree": agree}
        )
    )
    return 0 if agree else 1


def cmd_bump(args: argparse.Namespace) -> int:
    new_version = args.new_version
    _version_tuple(new_version)  # validate shape early, fail loud on garbage input

    text = args.pyproject.read_text(encoding="utf-8")
    pin_version = read_pin_version(text)
    tag_version = read_source_tag_version(text)
    if pin_version != tag_version:
        raise PyprojectError(
            f"pre-existing drift: pin={pin_version!r} tag={tag_version!r} -- "
            "refusing to bump on top of an already-broken invariant"
        )

    if _version_tuple(new_version) <= _version_tuple(pin_version):
        print(
            json.dumps(
                {
                    "bumped": False,
                    "current": pin_version,
                    "requested": new_version,
                }
            )
        )
        return 0

    updated = rewrite(text, new_version)
    args.pyproject.write_text(updated, encoding="utf-8")
    print(json.dumps({"bumped": True, "from": pin_version, "to": new_version}))
    return 0


def main(argv: list[str] | None = None) -> int:
    # --pyproject is accepted both before AND after the subcommand (e.g.
    # `bump_tmux_kit.py check --pyproject x` and
    # `bump_tmux_kit.py --pyproject x check` both work) via a shared parent
    # parser, since callers naturally reach for either order.
    pyproject_parent = argparse.ArgumentParser(add_help=False)
    pyproject_parent.add_argument(
        "--pyproject",
        default=Path("pyproject.toml"),
        type=Path,
        help="path to pyproject.toml (default: ./pyproject.toml)",
    )

    parser = argparse.ArgumentParser(description=__doc__, parents=[pyproject_parent])
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "check",
        help="print the current pin/tag, verify they agree",
        parents=[pyproject_parent],
    )

    bump_parser = subparsers.add_parser(
        "bump",
        help="bump the pin+tag to new_version if it's strictly newer",
        parents=[pyproject_parent],
    )
    bump_parser.add_argument("new_version", help="new tmux-kit version, e.g. 0.4.0")

    args = parser.parse_args(argv)

    if args.command == "check":
        return cmd_check(args)
    return cmd_bump(args)


if __name__ == "__main__":
    raise SystemExit(main())
