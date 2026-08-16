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
  // A generic DOM element stub. chat.js's init() (called at top-level load
  // time -- see the performance stub's comment below) is far more
  // defensive than app.js/terminal.js's, and calls a long tail of DOM
  // element methods this test has no interest in enumerating one by one
  // (setAttribute, removeAttribute, focus, scrollIntoView, remove, ...).
  // A Proxy answers any method call with a no-op and any unset property
  // read with a stub value, so init() can run to completion without this
  // fixture growing a new named stub every time chat.js calls one more
  // DOM method -- exactly the "just enough to run without a
  // ReferenceError" contract this function's own docstring commits to,
  // generalized instead of hand-enumerated.
  const elementStub = () =>
    new Proxy(
      {
        style: {},
        classList: classList(),
        textContent: '',
        dataset: {},
        // querySelector/querySelectorAll return null/empty (not another
        // stub) -- callers that use these already null-check (see e.g.
        // chat.js's activeViewBody()), and stubs-of-stubs buy nothing
        // this test needs.
        querySelector: () => null,
        querySelectorAll: () => [],
      },
      {
        get(target, prop) {
          if (prop in target) return target[prop];
          // Any other method this fixture didn't predefine (setAttribute,
          // removeAttribute, focus, blur, remove, scrollIntoView,
          // appendChild, insertBefore, closest, matches, ...) becomes a
          // no-op -- init() only needs the CALL to not throw, never the
          // real DOM side effect, at top-level load time.
          return () => undefined;
        },
        set(target, prop, value) {
          target[prop] = value;
          return true;
        },
      }
    );

  const sandbox = {
    console,
    TextEncoder,
    TextDecoder,
    setTimeout,
    clearTimeout,
    // chat.js's init() runs at top-level load time (see the bottom of that
    // file: `document.readyState === "loading"` is false in this sandbox,
    // so it calls `init()` immediately rather than deferring to
    // DOMContentLoaded). init() can hit a missing #chat-live element in
    // this DOM-less sandbox and call `console.warn(...)` -- which this
    // file's own capture hooks (installGlobalCaptureHooks(), installed at
    // chat.js's own top level) wrap to also log a `performance.now()`
    // timestamp. Every real browser has `performance.now()` (part of the
    // standard High Resolution Time API); only this minimal sandbox
    // didn't, so this stub is completing the harness, not the code
    // (muxplex-fii).
    performance: { now: () => Date.now() },
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
      // A generic stub element for ANY id -- not null. chat.js's init()
      // (called at top-level load time, see the performance stub's
      // comment above) fails loud with a thrown Error if any of its
      // ~14 required elements are missing (by design: "the dangerous
      // tool must never be reachable without its gate"). That check
      // exists to catch a genuinely broken index.html, which is not
      // what this test is for -- it targets top-level binding
      // collisions only (see this file's module docstring) -- so
      // getElementById must return SOMETHING truthy here, the same way
      // a real index.html always provides these elements in production.
      getElementById: () => elementStub(),
      querySelector: () => null,
      querySelectorAll: () => [],
      createElement: elementStub,
      addEventListener() {},
      removeEventListener() {},
      body: elementStub(),
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
