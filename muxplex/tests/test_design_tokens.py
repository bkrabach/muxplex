"""Guard the contract between the two design-token homes.

`muxplex/frontend/tokens.css` (docs/DESIGN_LANGUAGE.md) is the new single home
for every value the interface may use. `muxplex/frontend/style.css` still
carries its own historical ``:root`` block. Until that block is deleted, both
files define some of the same custom-property names.

CSS resolves a same-specificity collision by SOURCE ORDER, so whichever file
``index.html`` links second would win every shared name. That makes link order
load-bearing -- exactly the kind of invisible coupling this repo has been bitten
by before.

The contract that removes the hazard: **every name defined in both files
resolves to the same value**, so link order cannot change a rendered pixel. This
module asserts that structurally, in the spirit of the `.quick-link`
consolidation (d9061ba), whose lesson was that a guarantee nobody checks is a
guarantee that drifts. It checks properties of the FILES, not of one value, so a
future edit that diverges them fails here rather than on someone's screen.

Deliberately dependency-free and side-effect-free: it reads two text files and
compares strings. No server, no tmux, no network, no fixtures.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
TOKENS_CSS = FRONTEND / "tokens.css"
STYLE_CSS = FRONTEND / "style.css"
REPO_ROOT = Path(__file__).resolve().parents[2]

# A second token file is the failure this guard exists for -- see
# test_exactly_one_token_file_exists_in_the_repo and
# docs/DESIGN_LANGUAGE.md section 3.4.
TOKEN_FILENAMES = {"tokens.css", "tokens.json"}

# Values that carry essentially all spacing / radius / type in style.css today
# (see docs/DESIGN_LANGUAGE.md section 3.2 for the counts). The scale exists to
# name these; if one loses its token, the scale has stopped describing the app.
REQUIRED_SPACE_PX = {"2px", "4px", "6px", "8px", "12px", "16px"}
REQUIRED_RADIUS_PX = {"4px", "8px", "12px"}
REQUIRED_TEXT_PX = {"10px", "11px", "12px", "13px", "14px", "16px"}

# style.css READS these two names but nothing anywhere DEFINES them, so every
# call site renders its inline fallback -- and two different reds ship under
# --danger today (#c0392b in the follow-ups panel, #f85149 elsewhere). Defining
# either one in tokens.css would silently repaint whichever call sites disagree
# with the value chosen, which would break tokens.css's whole contract of
# changing nothing on adoption. Unifying them is a real, browser-verified
# style.css change (docs/DESIGN_LANGUAGE.md section 7, item 1) -- not something
# tokens.css may do by accident.
MUST_NOT_DEFINE = {"--warning", "--danger"}

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_DECL_RE = re.compile(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;]+);")
_VAR_RE = re.compile(r"^var\(\s*(--[A-Za-z0-9_-]+)\s*\)$")


def _strip_comments(css: str) -> str:
    return _COMMENT_RE.sub("", css)


def _top_level_root_bodies(css: str) -> list[str]:
    """Return the body of every ``:root`` block at the top level of the file.

    Blocks nested inside an at-rule (``@media``) are skipped on purpose:
    style.css overrides --tile-min-width inside a width media query, and that
    override is a breakpoint decision that belongs next to its breakpoint, not
    a competing definition of the base token.
    """
    bodies: list[str] = []
    for match in re.finditer(r":root\b", css):
        start = match.start()
        # Unclosed braces before this point == nesting depth. 0 means top level.
        if css.count("{", 0, start) - css.count("}", 0, start) != 0:
            continue
        open_brace = css.find("{", match.end())
        if open_brace == -1:
            continue
        depth = 0
        for i in range(open_brace, len(css)):
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
                if depth == 0:
                    bodies.append(css[open_brace + 1 : i])
                    break
    return bodies


def _declared_vars(path: Path) -> dict[str, str]:
    css = _strip_comments(path.read_text(encoding="utf-8"))
    declared: dict[str, str] = {}
    for body in _top_level_root_bodies(css):
        for name, value in _DECL_RE.findall(body):
            # Last declaration wins, matching CSS.
            declared[name] = " ".join(value.split())
    return declared


def _resolve(
    name: str, declared: dict[str, str], _seen: frozenset[str] = frozenset()
) -> str:
    """Resolve a value through plain ``var(--x)`` aliases within one file."""
    value = declared[name]
    alias = _VAR_RE.match(value)
    if alias is None:
        return value
    target = alias.group(1)
    if target in _seen or target not in declared:
        return value
    return _resolve(target, declared, _seen | {name})


def _px_values(declared: dict[str, str], prefix: str) -> set[str]:
    out = set()
    for name in declared:
        if not name.startswith(prefix):
            continue
        value = _resolve(name, declared)
        if value.endswith("px"):
            out.add(value)
    return out


def test_both_token_files_exist():
    assert TOKENS_CSS.is_file(), f"missing {TOKENS_CSS}"
    assert STYLE_CSS.is_file(), f"missing {STYLE_CSS}"


def _repo_token_files() -> list[Path]:
    """Every tokens.css / tokens.json in the working tree.

    Dot-directories are skipped, and that exclusion is load-bearing rather than
    tidiness: `.git` stores object data, and this repo is routinely checked out
    with sibling git worktrees under `.worktrees/`, each carrying its own
    legitimate copy of `muxplex/frontend/tokens.css`. Walking into those would
    fail this test on every developer who uses worktrees, for no real defect.
    `node_modules` and `__pycache__` are skipped for the same "not source we
    wrote" reason.

    Vendored third-party directories are deliberately NOT skipped. If a
    dependency ever ships its own tokens.css into the tree, that is exactly the
    situation a human should look at rather than a hole this guard quietly
    permits.
    """
    skip = {"node_modules", "__pycache__"}
    found: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.name not in TOKEN_FILENAMES:
            continue
        rel = path.relative_to(REPO_ROOT)
        if any(part.startswith(".") or part in skip for part in rel.parts):
            continue
        found.append(path)
    return sorted(found)


def test_exactly_one_token_file_exists_in_the_repo():
    """A second token file is the defect this guard exists to make impossible.

    The repo shipped two for months: `assets/branding/tokens.css` (2026-03-27,
    100 names) alongside `muxplex/frontend/tokens.css` (67 names). Git never
    flagged it, because the paths differ -- there is no conflict to raise. It
    surfaced only when someone diffed the two files against each other.

    They shared 13 custom-property names and disagreed on NINE of them:
    --text-md was 1rem (16px) against the 13px the app renders, --text-lg 20px
    against 14px, --radius-md 6px against 8px, plus both font stacks. CSS
    resolves a same-specificity collision by source order, so the failure mode
    was silent in both directions -- a developer reading the wrong file, or a
    page linking both, got a plausible wrong number with no error.

    Naming conventions do not fix this. The branded file's `--space-1..16`
    scale versus this one's t-shirt names was a deliberate anti-collision
    choice, and it worked -- for spacing, and for nothing else. Distinct naming
    protects one family at a time; a single file protects all of them.

    So the guard is structural, in the spirit of the `.quick-link`
    consolidation (d9061ba): it does not check that two files agree, it asserts
    that the second file cannot exist. Adding a token means adding a name to
    `muxplex/frontend/tokens.css` -- never a new file. See
    docs/DESIGN_LANGUAGE.md section 3.4 and work item muxplex-cnd.
    """
    if not (REPO_ROOT / "pyproject.toml").is_file():
        pytest.skip(
            f"not running from a source checkout (no pyproject.toml in {REPO_ROOT})"
        )

    found = _repo_token_files()
    extra = [p for p in found if p != TOKENS_CSS]
    assert not extra, (
        "the repo contains a second design-token file, which is the exact defect "
        "muxplex-cnd removed -- two token files at different paths, disagreeing "
        "silently because git cannot see a conflict between them: "
        + ", ".join(str(p.relative_to(REPO_ROOT)) for p in extra)
        + ". Add the value to muxplex/frontend/tokens.css instead. If a second "
        "file is genuinely required, docs/DESIGN_LANGUAGE.md section 3.4 has to "
        "change first -- it currently states there is exactly one."
    )
    assert TOKENS_CSS in found, (
        f"the canonical token file is missing from the tree: {TOKENS_CSS}"
    )


def test_shared_names_resolve_identically_so_link_order_cannot_matter():
    """The load-order safety property. See this module's docstring.

    If this fails, linking tokens.css before vs after style.css produces
    different pixels, and the "adopting tokens.css changes nothing" claim in
    docs/DESIGN_LANGUAGE.md is false.
    """
    tokens = _declared_vars(TOKENS_CSS)
    style = _declared_vars(STYLE_CSS)

    shared = sorted(set(tokens) & set(style))
    assert shared, "expected tokens.css to restate style.css's :root names"

    mismatches = {
        name: (_resolve(name, tokens), _resolve(name, style))
        for name in shared
        if _resolve(name, tokens) != _resolve(name, style)
    }
    assert not mismatches, (
        "tokens.css and style.css disagree on a shared custom property, so CSS "
        "link order now changes rendered output: "
        + "; ".join(
            f"{n}: tokens={t!r} style={s!r}" for n, (t, s) in sorted(mismatches.items())
        )
    )


def test_tokens_does_not_define_the_undefined_fallback_names():
    """tokens.css must not silently repaint the --warning/--danger call sites."""
    tokens = _declared_vars(TOKENS_CSS)
    defined = MUST_NOT_DEFINE & set(tokens)
    assert not defined, (
        f"tokens.css defines {sorted(defined)}, which style.css currently reads only "
        "via inline fallbacks. Defining them here repaints those call sites without "
        "anyone looking at the pixels -- see docs/DESIGN_LANGUAGE.md section 7 item 1."
    )


def test_token_aliases_never_dangle():
    """A var() alias pointing at nothing renders as the property's initial value.

    For `color` that is inherited dark-on-dark: invisible text, and a silent
    failure rather than a loud one. Every alias must resolve inside the file.
    """
    tokens = _declared_vars(TOKENS_CSS)
    dangling = []
    for name, value in tokens.items():
        alias = _VAR_RE.match(value)
        if alias and alias.group(1) not in tokens:
            dangling.append(f"{name} -> {alias.group(1)}")
    assert not dangling, "tokens.css aliases point at undefined names: " + ", ".join(
        sorted(dangling)
    )


def test_scale_still_covers_the_values_the_app_actually_uses():
    """The scale is a description of style.css, not a proposal for it.

    If a step is dropped, the app's most common values stop being derivable and
    the next contributor eyeballs one -- the failure this whole change exists to
    stop. Counts are in docs/DESIGN_LANGUAGE.md section 3.2.
    """
    tokens = _declared_vars(TOKENS_CSS)

    missing_space = REQUIRED_SPACE_PX - _px_values(tokens, "--space-")
    assert not missing_space, (
        f"--space-* scale no longer covers {sorted(missing_space)}"
    )

    missing_radius = REQUIRED_RADIUS_PX - _px_values(tokens, "--radius-")
    assert not missing_radius, (
        f"--radius-* scale no longer covers {sorted(missing_radius)}"
    )

    missing_text = REQUIRED_TEXT_PX - _px_values(tokens, "--text-")
    assert not missing_text, f"--text-* scale no longer covers {sorted(missing_text)}"


def test_adopted_touch_floor_is_present_and_unchanged():
    """48x48 CSS px, adopted in deck/DESIGN_RESPONSIVE.md 2.1 and already cited
    by name inside style.css. It satisfies Apple HIG, Material, WCAG 2.2 AA and
    WCAG 2.1 AAA simultaneously; lowering it silently drops one of those."""
    tokens = _declared_vars(TOKENS_CSS)
    assert tokens.get("--touch-min") == "48px", (
        f"--touch-min is {tokens.get('--touch-min')!r}, expected '48px' -- the adopted floor"
    )


def test_layer_ladder_is_strictly_ordered():
    """Names must sort the same way as their numbers, or the ladder lies."""
    tokens = _declared_vars(TOKENS_CSS)
    expected = [
        "--z-raised",
        "--z-view-overlay",
        "--z-float",
        "--z-popover",
        "--z-panel",
        "--z-modal-backdrop",
        "--z-modal",
        "--z-modal-nested",
        "--z-hover-preview",
    ]
    missing = [n for n in expected if n not in tokens]
    assert not missing, f"layer ladder is missing {missing}"

    values = [int(tokens[n]) for n in expected]
    assert values == sorted(values), (
        "layer tokens are not in ascending order, so the ladder in "
        "docs/DESIGN_LANGUAGE.md section 3.3 no longer describes them: "
        + ", ".join(f"{n}={v}" for n, v in zip(expected, values))
    )
