// Regression test for the v0.31.3 hotfix: two frontend classic scripts
// declared colliding top-level global bindings (app.js's `function
// _ownDeviceId()` vs terminal.js's `let _ownDeviceId`). Both files loaded
// cleanly in ISOLATION (test_app.mjs / test_terminal.mjs each require() only
// their own file into a fresh require-cache entry), so no existing test
// could ever catch a cross-file collision -- see AGENTS.md's "Frontend
// classic scripts share one global scope" note.
//
// index.html loads every local script as a classic (non-module) <script>
// tag, in order, into ONE shared global scope. This test reproduces that
// exact loading model: it parses the real <script src=...> tags out of
// index.html (so newly added scripts are covered automatically, with no
// hardcoded file list) and evaluates each one, in order, as a SEPARATE
// vm.Script execution against a SINGLE shared vm context -- which is what
// actually triggers a "global declaration instantiation" collision (an
// early SyntaxError), the same way a second <script> tag does in a real
// browser. A single concatenated source file would not reproduce this: the
// spec only checks for redeclaration conflicts against the *existing*
// global scope when a new top-level script begins evaluating.
//
// /vendor/* bundles are third-party and excluded -- we only guard our own
// frontend's top-level bindings against each other.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const frontendDir = join(__dirname, '..');

/**
 * Parse `<script src="...">` tags out of index.html, in document order,
 * excluding third-party /vendor/* bundles.
 * @param {string} htmlPath
 * @returns {string[]} local script src paths, e.g. ['/app.js', '/terminal.js']
 */
function parseLocalScriptSrcs(htmlPath) {
  const html = fs.readFileSync(htmlPath, 'utf-8');
  const re = /<script\b[^>]*\bsrc=["']([^"']+)["'][^>]*>/gi;
  const srcs = [];
  let m;
  while ((m = re.exec(html)) !== null) {
    srcs.push(m[1]);
  }
  return srcs.filter((src) => !src.startsWith('/vendor/'));
}

/**
 * Build a fresh vm context that stands in for the browser `window` a classic
 * <script> tag sees. Deliberately minimal: just enough for each script's
 * TOP-LEVEL (load-time) code to run without a ReferenceError, so that any
 * SyntaxError surfaced is genuinely about global-binding collisions between
 * our own scripts, not about missing browser stubs. Functions are not
 * expected to be *called* here -- only declared and, in terminal.js's case,
 * assigned onto `window`.
 */
function buildSharedScopeContext() {
  const classList = () => ({ add() {}, remove() {}, toggle() {}, contains: () => false });
  const elementStub = () => ({ style: {}, classList: classList(), addEventListener() {}, appendChild() {}, textContent: '' });

  const sandbox = {
    console,
    TextEncoder,
    TextDecoder,
    setTimeout,
    clearTimeout,
    // Non-touch-matching UA so terminal.js's immediately-invoked mobile-scroll
    // IIFE takes its early-return branch without needing a DOM.
    navigator: { userAgent: 'node-shared-scope-test' },
    location: { protocol: 'http:', host: 'localhost', href: '' },
    localStorage: {
      _store: {},
      getItem(k) { return Object.prototype.hasOwnProperty.call(this._store, k) ? this._store[k] : null; },
      setItem(k, v) { this._store[k] = String(v); },
      removeItem(k) { delete this._store[k]; },
    },
    document: {
      getElementById: () => null,
      querySelector: () => null,
      querySelectorAll: () => [],
      createElement: elementStub,
      addEventListener() {},
      removeEventListener() {},
    },
    Notification: { permission: 'default', requestPermission: async () => 'default' },
    addEventListener() {}, // app.js registers a top-level `window.addEventListener('resize', ...)`
    removeEventListener() {},
  };
  // Classic scripts see `window === globalThis`; make the sandbox its own window.
  sandbox.window = sandbox;
  return vm.createContext(sandbox);
}

test('all frontend classic scripts share one global scope without a SyntaxError', () => {
  const srcs = parseLocalScriptSrcs(join(frontendDir, 'index.html'));
  assert.ok(srcs.length > 0, 'expected to find at least one local <script src> in index.html');

  const ctx = buildSharedScopeContext();

  for (const src of srcs) {
    const filePath = join(frontendDir, src.replace(/^\//, ''));
    const source = fs.readFileSync(filePath, 'utf-8');
    // Each file is run as its OWN vm.Script execution (not concatenated)
    // against the SAME context -- this mirrors how the browser evaluates
    // successive <script> tags and is what actually surfaces a top-level
    // redeclaration as a SyntaxError, exactly as it did in production for
    // v0.31.3 ("Identifier '_ownDeviceId' has already been declared").
    assert.doesNotThrow(
      () => new vm.Script(source, { filename: filePath }).runInContext(ctx),
      (err) => {
        throw new Error(
          `${src} failed to evaluate in the shared global scope alongside the ` +
          `scripts loaded before it -- likely a top-level binding collision ` +
          `(see AGENTS.md's "Frontend classic scripts share one global scope" ` +
          `note). Original error: ${err && err.message}`
        );
      }
    );
  }
});
