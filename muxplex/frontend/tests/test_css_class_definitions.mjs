// Regression guard for the "toggled class has no CSS rule" bug class.
//
// Incident: renderSyncGroupControls() in app.js did
//   btn.classList.toggle('header-btn--active', independent);
// on #sync-group-btn / #sync-group-btn-expanded, but `.header-btn--active`
// was never defined in style.css. The JS was entirely correct -- aria-pressed
// flipped, the title tooltip updated -- so every existing test suite passed:
// test_frontend_js.py checks JS source, test_frontend_html.py checks
// markup, frontend/tests/*.mjs check JS behavior. NONE of them cross-checks
// a class the JS applies against the stylesheet that's supposed to render
// it, so the button silently never changed appearance in the browser. This
// is the second cross-file blind spot found in this feature -- the first
// was the app.js/terminal.js global collision now guarded by
// test_shared_scope.mjs (read it for the same "why no existing test could
// catch this" shape).
//
// This test extracts every class name our own frontend JS applies to an
// element (via `classList.add/remove/toggle` and literal `.className =`
// assignments) and asserts each one is defined somewhere in the stylesheet
// that the same page actually loads for that script, per index.html /
// deck/index.html's own <link rel="stylesheet"> + <script src> tags:
//   - app.js + terminal.js  -> style.css   (both classic scripts loaded by
//     the root index.html, which also loads style.css; see AGENTS.md's
//     "Frontend classic scripts share one global scope" note -- they
//     share a global JS scope, and in practice also share one stylesheet)
//   - deck/deck.js          -> deck/deck.css (deck/index.html's own page)
//
// Only string-literal class names are checked -- fully dynamic class
// expressions (a bare variable, a template literal with `${}`) can't be
// statically resolved and are intentionally out of scope, the same way
// test_shared_scope.mjs only checks what it can parse deterministically.
// A literal prefix on an otherwise-dynamic assignment (e.g.
// `el.className = 'deck-key ' + faceClassName(face.role)`) still has its
// literal part checked; the dynamic suffix is silently skipped.
//
// KNOWN_EXCEPTIONS below is the deliberate escape hatch for classes that
// are legitimately applied by our JS but NOT expected to resolve in the
// paired stylesheet (e.g. a class owned by a vendor stylesheet like
// vendor/xterm.css, or a pure marker class with no visual rule by design).
// Every entry must carry an inline reason -- do not add one to silence a
// real gap. As of this writing the set is empty: every class our JS
// applies IS defined in its paired stylesheet.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const frontendDir = join(__dirname, '..');

/**
 * Deliberate exceptions: classes our JS applies that are NOT required to
 * resolve in the paired stylesheet. Keyed by script path (relative to
 * frontend/), value is a Map of className -> reason string.
 * @type {Record<string, Record<string, string>>}
 */
const KNOWN_EXCEPTIONS = {
  // 'app.js': { 'some-class': 'defined in vendor/xterm.css, not style.css' },
};

/**
 * Each script's own class-defining stylesheet(s), matching what the page
 * that actually loads the script also loads (see index.html / deck/index.html).
 * @type {{ scripts: string[], stylesheets: string[] }[]}
 */
const PAIRINGS = [
  { scripts: ['app.js', 'terminal.js'], stylesheets: ['style.css'] },
  { scripts: ['deck/deck.js'], stylesheets: ['deck/deck.css'] },
];

/**
 * Extract every class token defined by a CSS selector in the given
 * stylesheet source (comments stripped first so a class name mentioned only
 * inside a comment doesn't count as "defined").
 * @param {string} cssSource
 * @returns {Set<string>}
 */
function extractDefinedClasses(cssSource) {
  const stripped = cssSource.replace(/\/\*[\s\S]*?\*\//g, '');
  const classes = new Set();
  const re = /\.([A-Za-z_][A-Za-z0-9_-]*)/g;
  let m;
  while ((m = re.exec(stripped)) !== null) {
    classes.add(m[1]);
  }
  return classes;
}

/**
 * Split a JS call's argument-list source into top-level, comma-separated
 * argument strings, respecting nesting (parens/brackets/braces) and quotes
 * so a comma inside a string literal or a nested call doesn't split early.
 * @param {string} argsSource
 * @returns {string[]}
 */
function splitTopLevelArgs(argsSource) {
  const args = [];
  let current = '';
  let depth = 0;
  let quote = null;
  for (let i = 0; i < argsSource.length; i++) {
    const ch = argsSource[i];
    if (quote) {
      current += ch;
      if (ch === quote && argsSource[i - 1] !== '\\') quote = null;
      continue;
    }
    if (ch === "'" || ch === '"' || ch === '`') {
      quote = ch;
      current += ch;
      continue;
    }
    if (ch === '(' || ch === '[' || ch === '{') {
      depth++;
      current += ch;
      continue;
    }
    if (ch === ')' || ch === ']' || ch === '}') {
      depth--;
      current += ch;
      continue;
    }
    if (ch === ',' && depth === 0) {
      args.push(current.trim());
      current = '';
      continue;
    }
    current += ch;
  }
  if (current.trim()) args.push(current.trim());
  return args;
}

/**
 * If `arg` is (only) a single-quoted or double-quoted string literal,
 * return its contents; otherwise null (it's a dynamic expression -- a
 * variable, a comparison like `staleness === 'warn'`, a template literal
 * with `${}`, etc. -- and out of scope for static checking).
 * @param {string} arg
 * @returns {string | null}
 */
function literalStringValue(arg) {
  const m = /^(['"])((?:(?!\1).)*)\1$/.exec(arg.trim());
  return m ? m[2] : null;
}

/**
 * Extract every string-literal class name applied by this JS source via
 * `classList.add/remove/toggle(...)` or a literal `.className = '...'`
 * assignment. Returns each class with the source snippet it came from, for
 * readable failure messages.
 *
 * Only the class-NAME argument position is checked: for `add`/`remove`
 * every argument is a class name (multi-arg add is valid), but for
 * `toggle(className, force)` only the FIRST argument is a class name --
 * the second is a force/condition expression that commonly contains its
 * own unrelated string literals (e.g. `classList.toggle('is-stale',
 * staleness === 'warn')` -- 'warn' there is a comparison value, not a
 * class name, and must not be flagged).
 * @param {string} jsSource
 * @returns {{ className: string, via: string }[]}
 */
function extractAppliedClasses(jsSource) {
  const found = [];

  const classListRe = /classList\.(add|remove|toggle)\(/g;
  let m;
  while ((m = classListRe.exec(jsSource)) !== null) {
    const method = m[1];
    // Find the matching close-paren for this call (start just after the
    // open-paren the regex consumed) so nested calls/parens don't confuse it.
    const openIdx = m.index + m[0].length - 1;
    let depth = 0;
    let closeIdx = -1;
    for (let i = openIdx; i < jsSource.length; i++) {
      if (jsSource[i] === '(') depth++;
      else if (jsSource[i] === ')') {
        depth--;
        if (depth === 0) {
          closeIdx = i;
          break;
        }
      }
    }
    if (closeIdx === -1) continue; // malformed / truncated match, skip
    const argsSource = jsSource.slice(openIdx + 1, closeIdx);
    const args = splitTopLevelArgs(argsSource);
    const classNameArgs = method === 'toggle' ? args.slice(0, 1) : args;
    for (const arg of classNameArgs) {
      const lit = literalStringValue(arg);
      if (lit === null) continue; // dynamic class-name expression, out of scope
      for (const className of lit.split(/\s+/).filter(Boolean)) {
        found.push({ className, via: `classList.${method}('${className}')` });
      }
    }
  }

  // el.className = 'literal ...' -- only the literal immediately following
  // `=` is captured (handles both a bare literal and a literal-prefixed
  // concatenation like `'deck-key ' + expr`); a bare-variable assignment
  // with no leading literal is dynamic and intentionally skipped.
  const classNameRe = /\.className\s*=\s*(['"])((?:(?!\1).)*)\1/g;
  while ((m = classNameRe.exec(jsSource)) !== null) {
    for (const className of m[2].split(/\s+/).filter(Boolean)) {
      found.push({ className, via: `.className = '${className}'` });
    }
  }

  return found;
}

for (const { scripts, stylesheets } of PAIRINGS) {
  test(`classes applied by ${scripts.join(', ')} are defined in ${stylesheets.join(', ')}`, () => {
    const defined = new Set();
    for (const cssPath of stylesheets) {
      const cssSource = fs.readFileSync(join(frontendDir, cssPath), 'utf-8');
      for (const c of extractDefinedClasses(cssSource)) defined.add(c);
    }

    const failures = [];
    for (const scriptPath of scripts) {
      const jsSource = fs.readFileSync(join(frontendDir, scriptPath), 'utf-8');
      const applied = extractAppliedClasses(jsSource);
      assert.ok(
        applied.length > 0,
        `expected to find at least one classList/className usage in ${scriptPath} -- ` +
        `extraction patterns may be out of date`
      );

      const exceptions = KNOWN_EXCEPTIONS[scriptPath] || {};
      for (const { className, via } of applied) {
        if (className in exceptions) continue;
        if (!defined.has(className)) {
          failures.push(`${scriptPath}: '.${className}' (via ${via}) is not defined in ${stylesheets.join(', ')}`);
        }
      }
    }

    assert.deepEqual(
      failures,
      [],
      `Found class(es) applied by JS with no matching CSS rule -- the element's ` +
      `state changes (attributes, ARIA, text) but nothing will visually render ` +
      `differently, exactly like the header-btn--active bug this test guards ` +
      `against:\n  ${failures.join('\n  ')}\n` +
      `If a class is legitimately defined elsewhere (a vendor stylesheet, etc.), ` +
      `add it to KNOWN_EXCEPTIONS in this file with a reason.`
    );
  });
}
