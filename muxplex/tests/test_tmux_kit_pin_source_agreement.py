"""The tmux-kit `==` pin and `[tool.uv.sources]` git tag must agree.

Mirrors ci.yml's `guard-tmux-kit-pin-source-agreement` job so the same
invariant fails loud in `make test`/`pytest`, not only in CI. See
AGENTS.md's "tmux-kit pin/tag agreement" section for the full rationale:

- A public `uv tool install muxplex` only ever sees [project.dependencies]'s
  plain `tmux-kit==X.Y.Z` pin -- [tool.uv.sources] never enters Requires-Dist,
  verified by inspecting a built wheel's METADATA.
- A managed-device `uv tool install git+.../muxplex` DOES resolve tmux-kit
  from the [tool.uv.sources] git entry (once `uv lock` has regenerated
  uv.lock to record it) -- verified via direct_url.json showing `vcs_info`.

If the pin and the source `tag` ever name different versions, those two
install paths silently ship DIFFERENT tmux-kit code from the SAME muxplex
release -- the same class of bug (PyPI vs. git users diverging silently)
that broke muxplex 0.44.0, just quieter: both installs succeed this time.
"""

from __future__ import annotations

from pathlib import Path

import tomllib

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _load_pyproject() -> dict:
    return tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))


def _kit_dependency_pin(data: dict) -> str:
    deps = data.get("project", {}).get("dependencies", [])
    kit_dep = next((d for d in deps if d.startswith("tmux-kit")), None)
    assert kit_dep is not None, (
        "no tmux-kit entry found in [project.dependencies] at all"
    )
    return kit_dep


def test_tmux_kit_dependency_is_a_plain_exact_pin():
    """PyPI rejects direct-URL deps in Requires-Dist -- this must stay a
    plain `tmux-kit==X.Y.Z`, never a git+/@ URL."""
    kit_dep = _kit_dependency_pin(_load_pyproject())
    assert "@" not in kit_dep and "git+" not in kit_dep and "/" not in kit_dep, (
        f"[project.dependencies] tmux-kit entry is not a plain version pin: "
        f"{kit_dep!r}."
    )
    assert "==" in kit_dep, (
        f"[project.dependencies] tmux-kit entry must be an exact '==' pin: {kit_dep!r}."
    )


def test_tmux_kit_source_is_a_git_entry_not_a_local_path():
    """A `path` source is the cross-repo dev loop's temporary `uv add
    --editable ../tmux-kit` -- it must be reverted before committing."""
    data = _load_pyproject()
    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    kit_source = sources.get("tmux-kit")
    assert kit_source is not None, (
        "no [tool.uv.sources] entry for tmux-kit. The managed-device (CISO) "
        "install path needs this to resolve tmux-kit from git automatically "
        "on a git-sourced muxplex install -- see AGENTS.md's 'tmux-kit "
        "pin/tag agreement' section."
    )
    assert "path" not in kit_source, (
        f"[tool.uv.sources] tmux-kit is a `path` source: {kit_source!r}. "
        "This is almost always a leftover cross-repo dev-loop override "
        "that should have been reverted before committing."
    )
    assert "git" in kit_source, (
        f"[tool.uv.sources] tmux-kit entry must be a git source, got: {kit_source!r}."
    )


def test_tmux_kit_pin_and_source_tag_agree():
    """The load-bearing invariant: `tmux-kit==X.Y.Z` and `tag = "vX.Y.Z"`
    must name the SAME version, or PyPI installs and git installs of the
    same muxplex release silently diverge."""
    data = _load_pyproject()
    kit_dep = _kit_dependency_pin(data)
    pinned_version = kit_dep.split("==", 1)[1].strip()

    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    kit_source = sources.get("tmux-kit")
    assert kit_source is not None, "no [tool.uv.sources] entry for tmux-kit"

    tag = kit_source.get("tag")
    expected_tag = f"v{pinned_version}"
    assert tag == expected_tag, (
        f"DRIFT: [project.dependencies] pins tmux-kit=={pinned_version} but "
        f"[tool.uv.sources] tmux-kit tag is {tag!r} (expected "
        f"{expected_tag!r}). These must agree -- see AGENTS.md's 'tmux-kit "
        "pin/tag agreement' section."
    )
