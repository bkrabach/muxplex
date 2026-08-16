// Tests for the "Agent Terminal Input" settings UI (input_enabled /
// input_allowed_sessions) -- the generic list-editor component
// (_buildListEditorRow/_serializeListEditor), the input_enabled toggle's
// PATCH payload, and the one-click "Enable typing for this fleet" button's
// PATCH payload (_enableFederationTypingPatch/_enableFederationTypingForFleet).
//
// SECURITY CONTEXT (why there is no credential-type test here): PATCH
// /api/settings already fences input_enabled/input_allowed_sessions to a
// real operator credential server-side (settings.py's
// OPERATOR_SETTABLE_LOCAL_KEYS + main.py's _bearer_only_caller() --
// covered by muxplex/tests/test_api.py and test_settings.py). This file
// covers only the client-side logic that decides WHAT payload to send and
// how the list editor serializes -- not the server-side fence itself.

// --- localStorage stub -- must be set before importing app.js ---
globalThis.localStorage = {
  getItem: () => null,
  setItem: () => {},
  removeItem: () => {},
};

// --- Minimal fake DOM -- a small element graph supporting exactly what
// _buildListEditorRow/_serializeListEditor/_enableFederationTypingForFleet
// touch: classList, attributes, appendChild, querySelector(All), and an
// innerHTML setter that clears children (matching app.js's own
// `container.innerHTML = ''` reset-before-repopulate idiom). ---

function makeClassList(initial) {
  const classes = new Set(initial || []);
  return {
    add(...cs) { for (const c of cs) classes.add(c); },
    remove(...cs) { for (const c of cs) classes.delete(c); },
    toggle(c, force) {
      if (force === undefined) {
        if (classes.has(c)) { classes.delete(c); return false; }
        classes.add(c); return true;
      }
      if (force) classes.add(c); else classes.delete(c);
      return !!force;
    },
    contains(c) { return classes.has(c); },
    _set: classes,
  };
}

function elementMatches(el, selector) {
  // Matches on `el.className` (a plain string property, exactly how
  // _buildListEditorRow/_buildRemoteInstanceRow set it: `row.className =
  // '...'`), NOT `el.classList` -- a real DOM keeps those two in sync
  // automatically; this stub doesn't bother, so querySelector must read
  // the same property app.js actually writes.
  const classNames = (el.className || '').split(/\s+/).filter(Boolean);
  return selector.split(',').map((s) => s.trim()).some((sel) => {
    if (sel.startsWith('.')) return classNames.includes(sel.slice(1));
    if (sel.startsWith('#')) return el._id === sel.slice(1);
    return false;
  });
}

function collectMatches(node, selector, out) {
  for (const child of node._children) {
    if (elementMatches(child, selector)) out.push(child);
    collectMatches(child, selector, out);
  }
  return out;
}

function makeElement() {
  const attrs = {};
  const listeners = {};
  const el = {
    type: '',
    value: '',
    placeholder: '',
    textContent: '',
    _children: [],
    _parent: null,
    classList: makeClassList([]),
    setAttribute(k, v) { attrs[k] = String(v); },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(attrs, k) ? attrs[k] : null; },
    removeAttribute(k) { delete attrs[k]; },
    hasAttribute(k) { return Object.prototype.hasOwnProperty.call(attrs, k); },
    addEventListener(ev, fn) { (listeners[ev] = listeners[ev] || []).push(fn); },
    removeEventListener() {},
    appendChild(child) { child._parent = el; el._children.push(child); return child; },
    querySelector(sel) { const m = collectMatches(el, sel, []); return m.length ? m[0] : null; },
    querySelectorAll(sel) { return collectMatches(el, sel, []); },
    set innerHTML(_v) { el._children = []; },
    get innerHTML() { return el._children.length ? '(non-empty)' : ''; },
  };
  return el;
}

globalThis.document = {
  createElement: () => makeElement(),
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: () => {},
  removeEventListener: () => {},
};

globalThis.window = {
  addEventListener: () => {},
  location: { href: '' },
  innerWidth: 1024,
};

Object.defineProperty(globalThis, 'navigator', {
  value: { userAgent: 'test-agent' },
  writable: true,
  configurable: true,
});

import { createRequire } from 'node:module';
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const require = createRequire(import.meta.url);
const app = require(join(__dirname, '..', 'app.js'));

// --- PATCH-capture fetch stub, same pattern as test_compose.mjs ---
let _patchedSettings = [];
function installSettingsFetchStub() {
  _patchedSettings = [];
  globalThis.fetch = async (_path, opts) => {
    if (opts && opts.method === 'PATCH' && opts.body) {
      let parsed = {};
      try { parsed = JSON.parse(opts.body); } catch (_) { /* not our patch */ }
      _patchedSettings.push(parsed);
      // Echo back the patched keys (minus the CAS field), same shape a
      // real PATCH /api/settings response takes -- the redacted settings
      // object, reflecting what was just written. Good enough to exercise
      // _enableFederationTypingForFleet()'s own body-driven state refresh
      // without standing up a real server.
      const responseBody = Object.assign({}, parsed);
      delete responseBody.expected_settings_updated_at;
      return { ok: true, json: async () => responseBody };
    }
    return { ok: true, json: async () => ({}) };
  };
}
function settled() {
  return new Promise((r) => setTimeout(r, 0));
}
const NEVER = Symbol('never-patched');
function lastPatched(key) {
  for (let i = _patchedSettings.length - 1; i >= 0; i--) {
    if (Object.prototype.hasOwnProperty.call(_patchedSettings[i], key)) {
      return _patchedSettings[i][key];
    }
  }
  return NEVER;
}

beforeEach(() => {
  installSettingsFetchStub();
  app._setServerSettings({});
});

// --- Exports sanity ---

test('app.js exports the new list-editor + Agent Terminal Input functions', () => {
  const expected = [
    '_buildListEditorRow',
    '_serializeListEditor',
    '_saveInputAllowedSessions',
    '_updateInputAllowedSessionsFieldVisibility',
    '_enableFederationTypingPatch',
    '_enableFederationTypingForFleet',
  ];
  for (const fn of expected) {
    assert.ok(fn in app, `app.js should export "${fn}"`);
    assert.strictEqual(typeof app[fn], 'function', `"${fn}" should be a function`);
  }
});

// --- List editor: serialize (rows -> array) ---

test('_serializeListEditor reads values from built rows in order', () => {
  const container = makeElement();
  container.appendChild(app._buildListEditorRow('*'));
  container.appendChild(app._buildListEditorRow('agent-*'));
  assert.deepStrictEqual(app._serializeListEditor(container), ['*', 'agent-*']);
});

test('_serializeListEditor on an empty container returns [] (never [""])', () => {
  const container = makeElement();
  assert.deepStrictEqual(app._serializeListEditor(container), []);
});

test('_serializeListEditor returns [] for a null container', () => {
  assert.deepStrictEqual(app._serializeListEditor(null), []);
});

test('_serializeListEditor trims whitespace around a value', () => {
  const container = makeElement();
  container.appendChild(app._buildListEditorRow('  agent-*  '));
  assert.deepStrictEqual(app._serializeListEditor(container), ['agent-*']);
});

test('_serializeListEditor drops blank/whitespace-only rows entirely (not as "")', () => {
  const container = makeElement();
  container.appendChild(app._buildListEditorRow('*'));
  container.appendChild(app._buildListEditorRow('   ')); // blank row (e.g. added then never filled in)
  container.appendChild(app._buildListEditorRow(''));    // empty row
  container.appendChild(app._buildListEditorRow('agent-*'));
  assert.deepStrictEqual(app._serializeListEditor(container), ['*', 'agent-*']);
});

test('_serializeListEditor with only blank rows returns [] (not [""])', () => {
  const container = makeElement();
  container.appendChild(app._buildListEditorRow(''));
  container.appendChild(app._buildListEditorRow('   '));
  assert.deepStrictEqual(app._serializeListEditor(container), []);
});

test('_buildListEditorRow defaults value to "" when omitted', () => {
  const row = app._buildListEditorRow();
  const container = makeElement();
  container.appendChild(row);
  assert.deepStrictEqual(app._serializeListEditor(container), []);
});

// --- input_enabled toggle -> PATCH payload ---
//
// The checkbox's change handler (bindStaticEventListeners()) calls
// patchServerSetting('input_enabled', enabled) directly -- this is that
// exact call, asserted against the wire body patchSettingsGuarded sends.

test('toggling input_enabled on sends {input_enabled: true}', async () => {
  await app.patchServerSetting('input_enabled', true);
  assert.strictEqual(lastPatched('input_enabled'), true);
});

test('toggling input_enabled off sends {input_enabled: false}', async () => {
  await app.patchServerSetting('input_enabled', false);
  assert.strictEqual(lastPatched('input_enabled'), false);
});

test('toggling input_enabled on updates the cached _serverSettings', async () => {
  app._setServerSettings({ input_enabled: false });
  await app.patchServerSetting('input_enabled', true);
  assert.strictEqual(app._getServerSettings().input_enabled, true);
});

// Wiring check (mirrors test_app.mjs's own source-text assertions for
// read-only/LOCAL_ONLY_KEYS fields): confirm the checkbox handler really
// calls patchServerSetting with the 'input_enabled' key, not a typo/renamed
// key that the payload tests above wouldn't otherwise catch.
test('bindStaticEventListeners wires #setting-input-enabled change to patchServerSetting("input_enabled", ...)', () => {
  const fs = require('node:fs');
  const source = fs.readFileSync(join(__dirname, '..', 'app.js'), 'utf-8');
  const idx = source.indexOf("$('setting-input-enabled'), 'change'");
  assert.ok(idx !== -1, '#setting-input-enabled must have a change handler bound');
  const handlerBody = source.slice(idx, idx + 400);
  assert.ok(
    handlerBody.includes("patchServerSetting('input_enabled'"),
    'the #setting-input-enabled change handler must call patchServerSetting(\'input_enabled\', ...)',
  );
});

// --- One-click "Enable typing for this fleet" -> PATCH payload ---

test('_enableFederationTypingPatch always turns input_enabled on', () => {
  const patch = app._enableFederationTypingPatch({});
  assert.strictEqual(patch.input_enabled, true);
});

test('_enableFederationTypingPatch widens an empty allow-list to ["*"]', () => {
  const patch = app._enableFederationTypingPatch({ input_allowed_sessions: [] });
  assert.deepStrictEqual(patch, { input_enabled: true, input_allowed_sessions: ['*'] });
});

test('_enableFederationTypingPatch widens a missing allow-list to ["*"]', () => {
  const patch = app._enableFederationTypingPatch({});
  assert.deepStrictEqual(patch, { input_enabled: true, input_allowed_sessions: ['*'] });
});

test('_enableFederationTypingPatch does NOT clobber a non-empty allow-list', () => {
  const patch = app._enableFederationTypingPatch({ input_allowed_sessions: ['agent-*'] });
  assert.deepStrictEqual(patch, { input_enabled: true });
  assert.ok(!('input_allowed_sessions' in patch), 'must not include input_allowed_sessions when already non-empty');
});

test('_enableFederationTypingPatch does NOT clobber a single-entry ["*"] allow-list either', () => {
  const patch = app._enableFederationTypingPatch({ input_allowed_sessions: ['*'] });
  assert.deepStrictEqual(patch, { input_enabled: true });
});

// --- _enableFederationTypingForFleet: end-to-end payload + state refresh ---

test('_enableFederationTypingForFleet sends input_enabled=true and input_allowed_sessions=["*"] when starting empty', async () => {
  app._setServerSettings({ input_enabled: false, input_allowed_sessions: [] });
  await app._enableFederationTypingForFleet();
  assert.strictEqual(lastPatched('input_enabled'), true);
  assert.deepStrictEqual(lastPatched('input_allowed_sessions'), ['*']);
});

test('_enableFederationTypingForFleet does not widen an already-narrowed allow-list', async () => {
  app._setServerSettings({ input_enabled: false, input_allowed_sessions: ['agent-*'] });
  await app._enableFederationTypingForFleet();
  assert.strictEqual(lastPatched('input_enabled'), true);
  assert.strictEqual(lastPatched('input_allowed_sessions'), NEVER, 'must not send input_allowed_sessions at all');
});

test('_enableFederationTypingForFleet updates cached _serverSettings.input_enabled', async () => {
  app._setServerSettings({ input_enabled: false, input_allowed_sessions: [] });
  await app._enableFederationTypingForFleet();
  assert.strictEqual(app._getServerSettings().input_enabled, true);
});

// --- _updateInputAllowedSessionsFieldVisibility: no-op-safe without DOM ---

test('_updateInputAllowedSessionsFieldVisibility does not throw when the field element is absent', () => {
  assert.doesNotThrow(() => app._updateInputAllowedSessionsFieldVisibility(true));
  assert.doesNotThrow(() => app._updateInputAllowedSessionsFieldVisibility(false));
});
