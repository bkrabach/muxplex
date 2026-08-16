"""The amplifier-agent `==` pin and [tool.uv.sources] git tag must agree --
and cli.py's own `_AGENT_FALLBACK_PIN` backstop must not drift from either.

Mirrors test_tmux_kit_pin_source_agreement.py's invariant (see that file's
docstring and AGENTS.md's "tmux-kit pin/tag agreement" section for the full
rationale) for amplifier-agent's own three copies of the same version:

1. `[project.optional-dependencies].agent`'s `amplifier-agent==X.Y.Z` pin
   (tolerant of the trailing `python_version>='3.12'` environment marker --
   unlike tmux-kit's base dependency, this one carries a marker because
   amplifier-agent itself requires Python >=3.12, stricter than muxplex's
   own floor).
2. `[tool.uv.sources]`'s `tag = "vX.Y.Z"` for amplifier-agent -- used by a
   git CHECKOUT of muxplex (`uv sync --extra agent` / `uv lock`); never
   entered into a published wheel's Requires-Dist (same PyPI restriction
   proven for tmux-kit).
3. `cli._AGENT_FALLBACK_PIN` -- the backstop `ensure_agent()` falls back to
   only when `_declared_dependency_pin("amplifier-agent")` can't read the
   metadata pin at runtime (see `_agent_target_pin`'s docstring).

If any of the three name a different version, a git checkout's dev install
and `ensure_agent()`'s own bootstrap logic can silently disagree about which
amplifier-agent to run.
"""

from __future__ import annotations

import re
from pathlib import Path

import tomllib

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
_CLI_PY = Path(__file__).resolve().parents[1] / "cli.py"


def _load_pyproject() -> dict:
    return tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))


def _agent_extra_dependency(data: dict) -> str:
    extras = data.get("project", {}).get("optional-dependencies", {})
    agent_deps = extras.get("agent", [])
    agent_dep = next((d for d in agent_deps if d.startswith("amplifier-agent")), None)
    assert agent_dep is not None, (
        "no amplifier-agent entry found in [project.optional-dependencies].agent"
    )
    return agent_dep


def _agent_extra_pin(data: dict) -> str:
    agent_dep = _agent_extra_dependency(data)
    match = re.search(r"amplifier-agent==([0-9][\w.\-]*)", agent_dep)
    assert match is not None, (
        f"[project.optional-dependencies].agent's amplifier-agent entry is not"
        f" an exact '==' pin: {agent_dep!r}"
    )
    return match.group(1)


def test_amplifier_agent_extra_is_an_exact_pin_not_a_direct_url():
    """PyPI rejects direct-URL deps in Requires-Dist -- this must stay a
    plain `amplifier-agent==X.Y.Z[; marker]`, never a git+/@ URL."""
    agent_dep = _agent_extra_dependency(_load_pyproject())
    assert "git+" not in agent_dep, (
        f"[project.optional-dependencies].agent's amplifier-agent entry embeds"
        f" a git URL directly: {agent_dep!r}. It must stay a plain '==' pin --"
        " the git source belongs in [tool.uv.sources] only."
    )
    assert "==" in agent_dep, (
        f"[project.optional-dependencies].agent's amplifier-agent entry must be"
        f" an exact '==' pin: {agent_dep!r}."
    )


def test_amplifier_agent_source_is_a_git_entry_not_a_local_path():
    """A `path` source is the cross-repo dev loop's temporary `uv add
    --editable ../amplifier-agent` -- it must be reverted before committing."""
    data = _load_pyproject()
    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    agent_source = sources.get("amplifier-agent")
    assert agent_source is not None, (
        "no [tool.uv.sources] entry for amplifier-agent. A git checkout of"
        " muxplex (`uv sync --extra agent`) needs this to resolve"
        " amplifier-agent from git automatically."
    )
    assert "path" not in agent_source, (
        f"[tool.uv.sources] amplifier-agent is a `path` source: {agent_source!r}."
        " This is almost always a leftover cross-repo dev-loop override that"
        " should have been reverted before committing."
    )
    assert "git" in agent_source, (
        f"[tool.uv.sources] amplifier-agent entry must be a git source, got:"
        f" {agent_source!r}."
    )


def test_amplifier_agent_pin_and_source_tag_agree():
    """The load-bearing invariant: `amplifier-agent==X.Y.Z` and `tag =
    "vX.Y.Z"` must name the SAME version, or a git checkout's dev install
    and a `uv tool install` (which reads the same pin via
    ensure_agent()/_declared_dependency_pin) silently diverge."""
    data = _load_pyproject()
    pinned_version = _agent_extra_pin(data)

    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    agent_source = sources.get("amplifier-agent")
    assert agent_source is not None, "no [tool.uv.sources] entry for amplifier-agent"

    tag = agent_source.get("tag")
    expected_tag = f"v{pinned_version}"
    assert tag == expected_tag, (
        f"DRIFT: [project.optional-dependencies].agent pins"
        f" amplifier-agent=={pinned_version} but [tool.uv.sources]"
        f" amplifier-agent tag is {tag!r} (expected {expected_tag!r})."
    )


def test_cli_fallback_pin_agrees_with_pyproject_pin():
    """cli.py's `_AGENT_FALLBACK_PIN` backstop must not drift from the real
    pin declared in pyproject.toml -- ensure_agent() falls back to it only
    when the metadata lookup fails, and a stale fallback would silently
    install the wrong version in exactly that degraded case."""
    pinned_version = _agent_extra_pin(_load_pyproject())

    cli_source = _CLI_PY.read_text(encoding="utf-8")
    match = re.search(r'_AGENT_FALLBACK_PIN\s*=\s*"([^"]+)"', cli_source)
    assert match is not None, "no _AGENT_FALLBACK_PIN constant found in cli.py"
    assert match.group(1) == pinned_version, (
        f"DRIFT: cli._AGENT_FALLBACK_PIN is {match.group(1)!r} but"
        f" pyproject.toml pins amplifier-agent=={pinned_version!r}."
    )
