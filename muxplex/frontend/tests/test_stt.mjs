// Tests for dictation (app.js's _stt* functions): on-device preferred,
// cloud by explicit opt-in.
//
// This is a client-only feature: a mic button that is un-hidden when
// SpeechRecognition.available() reports 'available' or 'downloadable' for
// EITHER on-device ({processLocally: true}, tried first) or cloud
// ({processLocally: false}, tried only if on-device isn't there) for the
// user's language. Cloud dictation is never started without a recorded,
// explicit per-device opt-in (#compose-cloud-consent) naming where the
// audio goes; on-device dictation still starts instantly, unchanged. See
// app.js's "On-device dictation (STT)" section banner for the full design
// rationale, including why this replaced an earlier on-device-only gate.
//
// IMPORTANT SCOPE NOTE: there is no real microphone and no Chromium 139+ to
// drive in this test environment. These tests prove:
//   - the availability-gating logic, including the on-device-first/
//     cloud-second cascade and every possible SpeechRecognition.available()
//     outcome (including the API not existing at all)
//   - the button's visual/ARIA state machine (idle/listening/downloading),
//     including the persistent cloud indicator and mode-specific titles
//   - the cloud-consent gate: shown on first cloud use, never shown for
//     on-device, persisted per-device, and never bypassed
//   - that the resolved mode is fixed at init and never silently
//     re-decided or upgraded mid-session
//   - the transcript-insertion algorithm, against synthetic
//     SpeechRecognitionEvent-shaped objects (interim preview replaced in
//     place, final text committed and insertion point advanced)
//   - every error-handling path (no silent failures)
//   - that dictation is never auto-restarted from onend/onerror (the
//     rate-limit trap named in the task)
//   - phrase-biasing terms are sourced from real, already-loaded session/
//     device data, never a hardcoded list
//
// They do NOT and cannot prove that real speech becomes real text -- that
// needs a real device and is out of this suite's reach.

// --- localStorage stub (compose bar's own init reads it) ---
let _localStorageStore = {};
globalThis.localStorage = {
  getItem(key) { return Object.prototype.hasOwnProperty.call(_localStorageStore, key) ? _localStorageStore[key] : null; },
  setItem(key, value) { _localStorageStore[key] = String(value); },
  removeItem(key) { delete _localStorageStore[key]; },
};

// --- DOM stub ---
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

function makeStubElement(initialClasses) {
  const attrs = {};
  const listeners = {};
  return {
    style: {},
    value: '',
    disabled: false,
    textContent: '',
    scrollHeight: 20,
    selectionStart: 0,
    classList: makeClassList(initialClasses),
    setAttribute(k, v) { attrs[k] = String(v); },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(attrs, k) ? attrs[k] : null; },
    removeAttribute(k) { delete attrs[k]; },
    hasAttribute(k) { return Object.prototype.hasOwnProperty.call(attrs, k); },
    addEventListener(ev, fn) { (listeners[ev] = listeners[ev] || []).push(fn); },
    _fire(ev, evObj) { (listeners[ev] || []).forEach((fn) => fn(evObj)); },
    setSelectionRange(start) { this.selectionStart = start; },
    focus() { this._focused = true; },
    _focused: false,
  };
}

const elements = {
  'compose-bar': makeStubElement(['hidden']),
  'compose-notice': makeStubElement(['hidden']),
  'compose-error': makeStubElement(['hidden']),
  'compose-input': makeStubElement([]),
  'compose-send-btn': makeStubElement([]),
  'compose-queue-btn': makeStubElement([]),
  'compose-toggle-btn': makeStubElement([]),
  'compose-mic-btn': makeStubElement(['hidden']),
  'compose-cloud-consent': makeStubElement(['hidden']),
  'compose-cloud-consent-allow-btn': makeStubElement([]),
  'compose-cloud-consent-cancel-btn': makeStubElement([]),
};

globalThis.document = {
  getElementById: (id) => Object.prototype.hasOwnProperty.call(elements, id) ? elements[id] : null,
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: () => makeStubElement([]),
  addEventListener: () => {},
  removeEventListener: () => {},
};

let _refitCallCount = 0;
globalThis.window = {
  addEventListener: () => {},
  location: { href: '' },
  innerWidth: 1024,
  _refitTerminal: () => { _refitCallCount++; },
};

globalThis.Notification = {
  permission: 'default',
  requestPermission: async () => 'default',
};

Object.defineProperty(globalThis, 'navigator', {
  value: { userAgent: 'test-agent', language: 'en-US' },
  writable: true,
  configurable: true,
});

// Stubs for functions app.js's top-level code references from other files.
globalThis.renderGrid = () => {};
globalThis.handleBellTransitions = () => {};
globalThis.openSession = () => {};
globalThis.updatePillBell = () => {};

import { createRequire } from 'node:module';
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import fs from 'node:fs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const require = createRequire(import.meta.url);
const app = require(join(__dirname, '..', 'app.js'));
const appSource = fs.readFileSync(join(__dirname, '..', 'app.js'), 'utf-8');

function resetDom() {
  elements['compose-bar'] = makeStubElement(['hidden']);
  elements['compose-notice'] = makeStubElement(['hidden']);
  elements['compose-error'] = makeStubElement(['hidden']);
  elements['compose-input'] = makeStubElement([]);
  elements['compose-send-btn'] = makeStubElement([]);
  elements['compose-queue-btn'] = makeStubElement([]);
  elements['compose-toggle-btn'] = makeStubElement([]);
  elements['compose-mic-btn'] = makeStubElement(['hidden']);
  elements['compose-cloud-consent'] = makeStubElement(['hidden']);
  elements['compose-cloud-consent-allow-btn'] = makeStubElement([]);
  elements['compose-cloud-consent-cancel-btn'] = makeStubElement([]);
  _refitCallCount = 0;
}

beforeEach(() => {
  resetDom();
  _localStorageStore = {};
  globalThis.window.innerWidth = 1024;
  delete globalThis.window.SpeechRecognition;
  delete globalThis.window.webkitSpeechRecognition;
  delete globalThis.window.SpeechRecognitionPhrase;
  app._setViewingSession(null);
  app._setCurrentSessions([]);
  app._setServerSettings({ input_enabled: true });
  app._setSttStatus(null);
  app._setSttMode(null);
  app._sttSetState('idle');
  app._setSttRecognition(null);
  app._setSttInsertState(0, 0);
  app._composeClearDraft();
});

/** Helper: a fake SpeechRecognition.available() that returns a fixed status
 * for on-device calls and a (possibly different) fixed status for cloud
 * calls, recording every call's args in order. */
function fakeAvailable(onDeviceStatus, cloudStatus, calls) {
  return async (args) => {
    (calls || (calls = [])).push(args);
    return args.processLocally ? onDeviceStatus : cloudStatus;
  };
}

// --- Availability gating: the button must not exist unless something is provably there ---

test('no SpeechRecognition at all -> availability resolves null', async () => {
  const result = await app._sttCheckAvailability();
  assert.strictEqual(result, null);
});

test('SpeechRecognition exists but has no .available() (pre-139 build) -> null', async () => {
  globalThis.window.SpeechRecognition = function() {};
  const result = await app._sttCheckAvailability();
  assert.strictEqual(result, null);
});

test("on-device available() resolves 'available' -> returns status+lang+mode:'ondevice'", async () => {
  globalThis.window.SpeechRecognition = function() {};
  globalThis.window.SpeechRecognition.available = fakeAvailable('available', 'available');
  const result = await app._sttCheckAvailability();
  assert.deepEqual(result, { status: 'available', lang: 'en-US', mode: 'ondevice' });
});

test("on-device available() resolves 'downloadable' -> returns status+lang+mode:'ondevice'", async () => {
  globalThis.window.SpeechRecognition = function() {};
  globalThis.window.SpeechRecognition.available = fakeAvailable('downloadable', 'available');
  const result = await app._sttCheckAvailability();
  assert.deepEqual(result, { status: 'downloadable', lang: 'en-US', mode: 'ondevice' });
});

test("both on-device and cloud 'unavailable' -> null (button never shown)", async () => {
  globalThis.window.SpeechRecognition = function() {};
  globalThis.window.SpeechRecognition.available = fakeAvailable('unavailable', 'unavailable');
  const result = await app._sttCheckAvailability();
  assert.strictEqual(result, null);
});

test("both on-device and cloud 'downloading' -> null (this spike does not poll a 3rd state)", async () => {
  globalThis.window.SpeechRecognition = function() {};
  globalThis.window.SpeechRecognition.available = fakeAvailable('downloading', 'downloading');
  const result = await app._sttCheckAvailability();
  assert.strictEqual(result, null);
});

test('on-device available() is checked FIRST, passed processLocally: true and the resolved language', async () => {
  globalThis.window.SpeechRecognition = function() {};
  let capturedArgs = null;
  globalThis.window.SpeechRecognition.available = async (args) => { capturedArgs = args; return 'available'; };
  await app._sttCheckAvailability();
  assert.strictEqual(capturedArgs.processLocally, true);
  assert.deepEqual(capturedArgs.langs, ['en-US']);
});

test('available() throwing is treated as capability absent, never propagates', async () => {
  globalThis.window.SpeechRecognition = function() {};
  globalThis.window.SpeechRecognition.available = async () => { throw new Error('boom'); };
  const result = await app._sttCheckAvailability();
  assert.strictEqual(result, null);
});

test('_sttInit() with no SpeechRecognition leaves the button hidden', async () => {
  await app._sttInit();
  assert.strictEqual(elements['compose-mic-btn'].classList.contains('hidden'), true);
  assert.strictEqual(app._getSttStatus(), null);
  assert.strictEqual(app._getSttMode(), null);
});

test("_sttInit() with on-device 'available' un-hides the button and sets mode 'ondevice'", async () => {
  globalThis.window.SpeechRecognition = function() {};
  globalThis.window.SpeechRecognition.available = fakeAvailable('available', 'available');
  await app._sttInit();
  assert.strictEqual(elements['compose-mic-btn'].classList.contains('hidden'), false);
  assert.strictEqual(app._getSttStatus(), 'available');
  assert.strictEqual(app._getSttMode(), 'ondevice');
});

test("_sttInit() with on-device unavailable but cloud 'available' un-hides the button and sets mode 'cloud'", async () => {
  globalThis.window.SpeechRecognition = function() {};
  globalThis.window.SpeechRecognition.available = fakeAvailable('unavailable', 'available');
  await app._sttInit();
  assert.strictEqual(elements['compose-mic-btn'].classList.contains('hidden'), false);
  assert.strictEqual(app._getSttStatus(), 'available');
  assert.strictEqual(app._getSttMode(), 'cloud');
});

test('_sttInit() checks cloud with processLocally: false, only after on-device is confirmed unavailable', async () => {
  globalThis.window.SpeechRecognition = function() {};
  const calls = [];
  globalThis.window.SpeechRecognition.available = fakeAvailable('unavailable', 'available', calls);
  await app._sttInit();
  assert.strictEqual(calls.length, 2);
  assert.strictEqual(calls[0].processLocally, true);
  assert.strictEqual(calls[1].processLocally, false);
});

test('_sttInit() never checks cloud at all once on-device already resolved available (prefer on-device)', async () => {
  globalThis.window.SpeechRecognition = function() {};
  const calls = [];
  globalThis.window.SpeechRecognition.available = fakeAvailable('available', 'available', calls);
  await app._sttInit();
  assert.strictEqual(calls.length, 1, 'cloud availability must never be probed once on-device already won');
  assert.strictEqual(calls[0].processLocally, true);
});

// --- Button render state machine ---

test('idle state, on-device mode: not listening, not downloading, no cloud badge, plain "Dictate" title', () => {
  app._setSttStatus('available');
  app._setSttMode('ondevice');
  app._sttSetState('idle');
  const btn = elements['compose-mic-btn'];
  assert.strictEqual(btn.classList.contains('compose-bar__mic--listening'), false);
  assert.strictEqual(btn.classList.contains('compose-bar__mic--downloading'), false);
  assert.strictEqual(btn.classList.contains('compose-bar__mic--cloud'), false);
  assert.strictEqual(btn.getAttribute('aria-pressed'), 'false');
  assert.match(btn.title, /Dictate/);
  assert.doesNotMatch(btn.title, /cloud/i);
});

test('idle state, cloud mode: persistent --cloud badge class and title mentions cloud, even before any click', () => {
  app._setSttStatus('available');
  app._setSttMode('cloud');
  app._sttSetState('idle');
  const btn = elements['compose-mic-btn'];
  assert.strictEqual(btn.classList.contains('compose-bar__mic--cloud'), true);
  assert.match(btn.title, /cloud/i);
});

test('listening state, on-device mode: pressed, pulsing class, "Stop dictation" title (no cloud wording)', () => {
  app._setSttStatus('available');
  app._setSttMode('ondevice');
  app._sttSetState('listening');
  const btn = elements['compose-mic-btn'];
  assert.strictEqual(btn.classList.contains('compose-bar__mic--listening'), true);
  assert.strictEqual(btn.classList.contains('compose-bar__mic--cloud'), false);
  assert.strictEqual(btn.getAttribute('aria-pressed'), 'true');
  assert.match(btn.title, /Stop dictation/);
  assert.doesNotMatch(btn.title, /cloud/i);
});

test('listening state, cloud mode: pressed, pulsing AND cloud classes both present, title says audio is leaving the device', () => {
  app._setSttStatus('available');
  app._setSttMode('cloud');
  app._sttSetState('listening');
  const btn = elements['compose-mic-btn'];
  assert.strictEqual(btn.classList.contains('compose-bar__mic--listening'), true);
  assert.strictEqual(btn.classList.contains('compose-bar__mic--cloud'), true);
  assert.strictEqual(btn.getAttribute('aria-pressed'), 'true');
  assert.match(btn.title, /leaving this device/i);
});

test('downloading state: disabled, spinner class, "Downloading" title', () => {
  app._setSttStatus('downloadable');
  app._setSttMode('ondevice');
  app._sttSetState('downloading');
  const btn = elements['compose-mic-btn'];
  assert.strictEqual(btn.classList.contains('compose-bar__mic--downloading'), true);
  assert.strictEqual(btn.disabled, true);
  assert.match(btn.title, /Downloading/);
});

test('button disabled when input_enabled is not true, even if listening', () => {
  app._setServerSettings({ input_enabled: false });
  app._setSttStatus('available');
  app._sttRenderButton();
  assert.strictEqual(elements['compose-mic-btn'].disabled, true);
});

test('_composeRenderEnabledState() keeps the mic button in sync', async () => {
  app._setSttStatus('available');
  app._setServerSettings({ input_enabled: true });
  app._composeRenderEnabledState();
  assert.strictEqual(elements['compose-mic-btn'].disabled, false);
  app._setServerSettings({ input_enabled: false });
  app._composeRenderEnabledState();
  assert.strictEqual(elements['compose-mic-btn'].disabled, true);
});

// --- Transcript insertion algorithm ---
//
// NOTE ON CONTRACT: _sttApplyTranscript() now takes the FULL `results`
// array (the same shape as a SpeechRecognitionEvent's `.results`), not a
// single (transcript, isFinal) pair. Every call rebuilds the entire
// session's dictated region from that array and REPLACES whatever was
// there before at the fixed `_sttInsertPos` anchor -- see app.js's
// _sttApplyTranscript()/_sttHandleResult() docstrings for the full
// rationale (this replaced an incremental commit-and-advance design that
// broke on Android Chrome's cumulative result delivery).

test('final result is committed with a trailing space', () => {
  const input = elements['compose-input'];
  input.value = '';
  app._setSttInsertState(0, 0);
  app._sttApplyTranscript(input, [{ 0: { transcript: 'hello world' }, isFinal: true }]);
  assert.strictEqual(input.value, 'hello world ');
});

test('interim result is shown live but does not commit', () => {
  const input = elements['compose-input'];
  input.value = '';
  app._setSttInsertState(0, 0);
  app._sttApplyTranscript(input, [{ 0: { transcript: 'hel' }, isFinal: false }]);
  assert.strictEqual(input.value, 'hel');
  app._sttApplyTranscript(input, [{ 0: { transcript: 'hello' }, isFinal: false }]); // replaces the preview, not appends
  assert.strictEqual(input.value, 'hello');
});

test('a final result after interim updates replaces the preview exactly once', () => {
  const input = elements['compose-input'];
  input.value = '';
  app._setSttInsertState(0, 0);
  app._sttApplyTranscript(input, [{ 0: { transcript: 'hel' }, isFinal: false }]);
  app._sttApplyTranscript(input, [{ 0: { transcript: 'hello' }, isFinal: false }]);
  app._sttApplyTranscript(input, [{ 0: { transcript: 'hello' }, isFinal: true }]);
  assert.strictEqual(input.value, 'hello ');
});

test('a trailing interim result after finals is appended without committing (finals + live preview)', () => {
  const input = elements['compose-input'];
  input.value = '';
  app._setSttInsertState(0, 0);
  app._sttApplyTranscript(input, [{ 0: { transcript: 'hello' }, isFinal: true }]);
  app._sttApplyTranscript(input, [
    { 0: { transcript: 'hello' }, isFinal: true },
    { 0: { transcript: 'wor' }, isFinal: false },
  ]);
  assert.strictEqual(input.value, 'hello wor');
});

test('dictation inserts at the cursor position, not always at the end', () => {
  const input = elements['compose-input'];
  input.value = 'AAAABBBB';
  input.selectionStart = 4; // between the A's and B's
  app._setSttStatus('available');
  globalThis.window.SpeechRecognition = function() {
    this.start = () => {};
  };
  app._sttStart();
  app._sttHandleResult({ resultIndex: 0, results: [{ 0: { transcript: 'middle' }, isFinal: true }] });
  assert.strictEqual(input.value, 'AAAAmiddle BBBB');
});

test('spec-clean shape: disjoint incremental finals across sequential events accumulate correctly (results list is genuinely cumulative, as the spec requires)', () => {
  const input = elements['compose-input'];
  input.value = '';
  app._setSttInsertState(0, 0);
  // Event 1: engine has finalized "first" so far.
  app._sttHandleResult({ resultIndex: 0, results: [{ 0: { transcript: 'first' }, isFinal: true }] });
  assert.strictEqual(input.value, 'first ');
  // Event 2: "first" is still in the list (never re-sent/mutated), "second" is newly appended.
  app._sttHandleResult({
    resultIndex: 1,
    results: [
      { 0: { transcript: 'first' }, isFinal: true },
      { 0: { transcript: 'second' }, isFinal: true },
    ],
  });
  assert.strictEqual(input.value, 'first second ', 'never overwrite prior committed text');
});

test('regression (Android Chrome cloud dictation): cumulative growing final results end with the clean sentence, not a ladder of every intermediate state', () => {
  // The exact field bug: dictating "what needs to be worked on next" on
  // Android Chrome cloud dictation delivered ONE 'result' event per
  // intermediate utterance state, each marked isFinal:true, with the
  // SAME single result entry re-delivered holding a longer transcript
  // each time (not a growing list -- a single entry that keeps changing).
  const input = elements['compose-input'];
  input.value = '';
  app._setSttInsertState(0, 0);
  const steps = [
    'what',
    'what needs',
    'what needs to',
    'what needs to be',
    'what needs to be worked',
    'what needs to be worked on',
    'what needs to be worked on next',
  ];
  for (const s of steps) {
    app._sttHandleResult({ resultIndex: 0, results: [{ 0: { transcript: s }, isFinal: true }] });
  }
  assert.strictEqual(
    input.value,
    'what needs to be worked on next ',
    'must NOT be the ladder of every intermediate utterance state concatenated together'
  );
});

test('idempotency: replaying the identical result event twice does not change the value', () => {
  const input = elements['compose-input'];
  input.value = '';
  app._setSttInsertState(0, 0);
  const event = {
    resultIndex: 0,
    results: [
      { 0: { transcript: 'what needs to' }, isFinal: true },
      { 0: { transcript: 'be' }, isFinal: false },
    ],
  };
  app._sttHandleResult(event);
  const afterFirst = input.value;
  app._sttHandleResult(event); // exact same event object, re-delivered
  assert.strictEqual(input.value, afterFirst, 're-processing the same event must be a no-op');
});

test('_sttHandleResult ignores event.resultIndex entirely -- always rebuilds from the full results array', () => {
  const input = elements['compose-input'];
  input.value = '';
  app._setSttInsertState(0, 0);
  app._sttHandleResult({
    resultIndex: 1, // a spec-compliant engine would set this to skip already-applied entries; the new algorithm must not depend on it
    results: [
      { 0: { transcript: 'first' }, isFinal: true },
      { 0: { transcript: 'second' }, isFinal: true },
    ],
  });
  assert.strictEqual(input.value, 'first second ');
});

test('stop then restart mid-dictation: the new SpeechRecognition session\'s fresh results list does not clobber previously committed text', () => {
  const input = elements['compose-input'];
  input.value = '';
  input.selectionStart = 0;
  app._setSttStatus('available');
  app._setSttMode('ondevice');

  let recognition1;
  globalThis.window.SpeechRecognition = function() { recognition1 = this; this.start = () => {}; this.stop = () => { this.onend(); }; };
  app._sttStart(); // session 1: anchor = 0
  app._sttHandleResult({ resultIndex: 0, results: [{ 0: { transcript: 'hello' }, isFinal: true }] });
  assert.strictEqual(input.value, 'hello ');
  app._sttStop(); // graceful stop -- fires onend synchronously per the stub above

  // Caret was left after "hello " by the prior session -- a fresh click
  // starts a brand-new SpeechRecognition instance whose `results` list
  // starts over from empty, exactly as a real browser's would.
  input.selectionStart = input.value.length;
  let recognition2;
  globalThis.window.SpeechRecognition = function() { recognition2 = this; this.start = () => {}; };
  app._sttStart(); // session 2: anchor advances to end of "hello "
  app._sttHandleResult({ resultIndex: 0, results: [{ 0: { transcript: 'world' }, isFinal: true }] });
  assert.strictEqual(input.value, 'hello world ', 'previously committed text from session 1 must survive session 2');
});

test('_sttHandleResult calls _refitTerminal (auto-grow)', () => {
  const input = elements['compose-input'];
  input.value = '';
  app._setSttInsertState(0, 0);
  _refitCallCount = 0;
  app._sttHandleResult({ resultIndex: 0, results: [{ 0: { transcript: 'hi' }, isFinal: true }] });
  assert.ok(_refitCallCount > 0);
});

// --- Phrase biasing: sourced from real data, never a hardcoded list ---

test('phrase source terms come from live session names and the device hostname', () => {
  app._setCurrentSessions([{ name: 'deploy-prod' }, { name: 'scratch' }, {}, { name: '' }]);
  app._setServerSettings({ device_name: 'my-macbook' });
  const terms = app._sttPhraseSourceTerms();
  assert.ok(terms.includes('deploy-prod'));
  assert.ok(terms.includes('scratch'));
  assert.ok(terms.includes('my-macbook'));
  assert.strictEqual(terms.length, 3); // empty/missing names skipped, no duplicates
});

test('phrase source terms de-duplicate and cap at STT_MAX_PHRASES', () => {
  const many = [];
  for (let i = 0; i < app.STT_MAX_PHRASES + 10; i++) many.push({ name: 'session-' + i });
  many.push({ name: 'session-0' }); // duplicate
  app._setCurrentSessions(many);
  const terms = app._sttPhraseSourceTerms();
  assert.strictEqual(terms.length, app.STT_MAX_PHRASES);
});

test('_sttBuildPhrases returns null when SpeechRecognitionPhrase is not exposed', () => {
  app._setCurrentSessions([{ name: 'x' }]);
  assert.strictEqual(app._sttBuildPhrases(), null);
});

test('_sttBuildPhrases returns null when there are no real terms to offer', () => {
  globalThis.window.SpeechRecognitionPhrase = function(phrase, boost) { this.phrase = phrase; this.boost = boost; };
  app._setCurrentSessions([]);
  app._setServerSettings({});
  assert.strictEqual(app._sttBuildPhrases(), null);
});

test('_sttBuildPhrases builds one SpeechRecognitionPhrase per sourced term with the boost constant', () => {
  globalThis.window.SpeechRecognitionPhrase = function(phrase, boost) { this.phrase = phrase; this.boost = boost; };
  app._setCurrentSessions([{ name: 'deploy-prod' }]);
  app._setServerSettings({ device_name: 'host1' });
  const phrases = app._sttBuildPhrases();
  assert.strictEqual(phrases.length, 2);
  assert.ok(phrases.every((p) => p.boost === app.STT_PHRASE_BOOST));
  assert.deepEqual(phrases.map((p) => p.phrase).sort(), ['deploy-prod', 'host1'].sort());
});

// --- Start/stop lifecycle ---

test('_sttStart sets processLocally=true for on-device mode', () => {
  app._setSttStatus('available');
  app._setSttMode('ondevice');
  let captured = null;
  globalThis.window.SpeechRecognition = function() { captured = this; this.start = () => {}; };
  app._sttStart();
  assert.strictEqual(captured.processLocally, true);
  assert.strictEqual(captured.continuous, true);
  assert.strictEqual(captured.interimResults, true);
});

test('_sttStart sets processLocally=false for cloud mode (never omitted/ambiguous)', () => {
  app._setSttStatus('available');
  app._setSttMode('cloud');
  let captured = null;
  globalThis.window.SpeechRecognition = function() { captured = this; this.start = () => {}; };
  app._sttStart();
  assert.strictEqual(captured.processLocally, false);
  assert.strictEqual(captured.continuous, true);
  assert.strictEqual(captured.interimResults, true);
});

test('_sttStart transitions to listening and hides any prior error', () => {
  app._setSttStatus('available');
  app._setSttMode('ondevice');
  globalThis.window.SpeechRecognition = function() { this.start = () => {}; };
  app._composeShowError('stale error');
  app._sttStart();
  assert.strictEqual(app._getSttState(), 'listening');
  assert.strictEqual(elements['compose-error'].classList.contains('hidden'), true);
});

test('_sttStart does not start a second session while one is already running', () => {
  app._setSttStatus('available');
  app._setSttMode('ondevice');
  let constructCount = 0;
  globalThis.window.SpeechRecognition = function() { constructCount++; this.start = () => {}; };
  app._sttStart();
  app._sttStart();
  assert.strictEqual(constructCount, 1);
});

test('_sttStart surfaces a specific error if recognition.start() throws synchronously', () => {
  app._setSttStatus('available');
  app._setSttMode('ondevice');
  globalThis.window.SpeechRecognition = function() { this.start = () => { throw new Error('mic busy'); }; };
  app._sttStart();
  assert.strictEqual(app._getSttState(), 'idle');
  assert.match(elements['compose-error'].textContent, /Could not start dictation/);
  assert.match(elements['compose-error'].textContent, /mic busy/);
});

// --- Mode is fixed at init, never re-decided or silently upgraded mid-session ---

test('mode resolved to ondevice at init is used by _sttStart even if on-device later "goes away" -- no re-check, no fallback', () => {
  // Simulate _sttInit() having already resolved 'ondevice' earlier in the page load.
  app._setSttStatus('available');
  app._setSttMode('ondevice');
  // Now mutate availability as if the model were evicted after the check --
  // _sttStart() must NOT consult this again, so it can't matter either way.
  globalThis.window.SpeechRecognition = function() { this.start = () => {}; };
  globalThis.window.SpeechRecognition.available = async () => 'unavailable';
  let captured = null;
  const OriginalSR = globalThis.window.SpeechRecognition;
  globalThis.window.SpeechRecognition = function() { captured = this; this.start = () => {}; };
  globalThis.window.SpeechRecognition.available = OriginalSR.available;
  app._sttStart();
  assert.strictEqual(captured.processLocally, true, 'mode must stay ondevice -- never silently promoted to cloud mid-session');
});

test('_sttHandleClick never re-runs _sttCheckAvailability -- the resolved mode is trusted for the rest of the session', () => {
  app._setSttStatus('available');
  app._setSttMode('cloud');
  globalThis.localStorage.setItem(app.STT_CLOUD_CONSENT_STORAGE_KEY, 'granted');
  let availabilityCallCount = 0;
  globalThis.window.SpeechRecognition = function() { this.start = () => {}; };
  globalThis.window.SpeechRecognition.available = async () => { availabilityCallCount++; return 'available'; };
  app._sttHandleClick();
  assert.strictEqual(availabilityCallCount, 0, '_sttHandleClick must not re-probe availability; mode is decided once, by _sttInit()');
});

test('_sttStop calls .stop() (graceful), not .abort()', () => {
  let stopped = false;
  let aborted = false;
  app._setSttRecognition({ stop: () => { stopped = true; }, abort: () => { aborted = true; } });
  app._sttStop();
  assert.strictEqual(stopped, true);
  assert.strictEqual(aborted, false);
});

test('_sttStop is a no-op when nothing is running', () => {
  app._setSttRecognition(null);
  assert.doesNotThrow(() => app._sttStop());
});

test('_sttForceStop calls .abort() (not .stop()) and suppresses the end message', () => {
  let stopped = false;
  let aborted = false;
  app._setSttRecognition({ stop: () => { stopped = true; }, abort: () => { aborted = true; } });
  app._sttForceStop();
  assert.strictEqual(aborted, true);
  assert.strictEqual(stopped, false);
});

test('_composeClearDraft() force-stops any live dictation session', () => {
  let aborted = false;
  app._setSttRecognition({ stop: () => {}, abort: () => { aborted = true; } });
  app._composeClearDraft();
  assert.strictEqual(aborted, true);
});

// --- onend / onerror: no auto-restart, always an honest message ---

test('onend after an explicit user stop shows NO message', () => {
  app._setSttStatus('available');
  globalThis.window.SpeechRecognition = function() { this.start = () => {}; this.stop = () => { this.onend(); }; };
  app._sttStart();
  app._sttStop();
  assert.strictEqual(elements['compose-error'].classList.contains('hidden'), true);
  assert.strictEqual(app._getSttState(), 'idle');
});

test("onend with no prior error and no explicit stop (Chrome's ~60s silence timeout) shows an honest message", () => {
  app._setSttStatus('available');
  let recognition;
  globalThis.window.SpeechRecognition = function() { recognition = this; this.start = () => {}; };
  app._sttStart();
  recognition.onend(); // browser silently ended it -- never triggered by our own code
  assert.strictEqual(elements['compose-error'].classList.contains('hidden'), false);
  assert.match(elements['compose-error'].textContent, /minute of silence/);
  assert.strictEqual(app._getSttState(), 'idle');
});

test('onerror followed by onend shows exactly ONE message, not two', () => {
  app._setSttStatus('available');
  let recognition;
  globalThis.window.SpeechRecognition = function() { recognition = this; this.start = () => {}; };
  app._sttStart();
  recognition.onerror({ error: 'no-speech' });
  const afterError = elements['compose-error'].textContent;
  recognition.onend();
  assert.strictEqual(elements['compose-error'].textContent, afterError, 'onend must not overwrite the specific error message');
  assert.match(afterError, /No speech detected/);
});

test('no-auto-restart: neither onerror nor onend ever calls recognition.start() again', () => {
  app._setSttStatus('available');
  let startCalls = 0;
  let recognition;
  globalThis.window.SpeechRecognition = function() { recognition = this; this.start = () => { startCalls++; }; };
  app._sttStart();
  assert.strictEqual(startCalls, 1);
  recognition.onerror({ error: 'no-speech' });
  recognition.onend();
  assert.strictEqual(startCalls, 1, 'dictation must never auto-restart from onend/onerror (rate-limit trap)');
  assert.strictEqual(app._getSttState(), 'idle');
});

test("error 'not-allowed' surfaces a mic-permission message", () => {
  app._sttHandleError({ error: 'not-allowed' });
  assert.match(elements['compose-error'].textContent, /Microphone access was blocked/);
});

test("error 'audio-capture' surfaces a no-microphone message", () => {
  app._sttHandleError({ error: 'audio-capture' });
  assert.match(elements['compose-error'].textContent, /No microphone was found/);
});

test("error 'language-not-supported' (on-device mode) disables the capability (re-closes the gate) and shows a message", () => {
  app._setSttStatus('available');
  app._setSttMode('ondevice');
  app._sttHandleError({ error: 'language-not-supported' });
  assert.strictEqual(app._getSttStatus(), null);
  assert.strictEqual(app._getSttMode(), null, 'mode must also clear -- must not silently retry as cloud');
  assert.match(elements['compose-error'].textContent, /no longer available/);
});

test("error 'language-not-supported' (cloud mode) re-closes the gate too -- never falls through to on-device", () => {
  app._setSttStatus('available');
  app._setSttMode('cloud');
  app._sttHandleError({ error: 'language-not-supported' });
  assert.strictEqual(app._getSttStatus(), null);
  assert.strictEqual(app._getSttMode(), null);
  assert.match(elements['compose-error'].textContent, /cloud speech-recognition service/);
});

test('unrecognized error code still shows a specific (non-empty) message -- no silent path', () => {
  app._sttHandleError({ error: 'some-future-error-code' });
  assert.match(elements['compose-error'].textContent, /some-future-error-code/);
});

// --- Click dispatch ---

test('click while idle, on-device mode, starts recognition immediately -- no prompt', () => {
  app._setSttStatus('available');
  app._setSttMode('ondevice');
  let started = false;
  globalThis.window.SpeechRecognition = function() { this.start = () => { started = true; }; };
  app._sttHandleClick();
  assert.strictEqual(started, true);
  assert.strictEqual(app._getSttConsentPending(), false, 'on-device must never show the cloud-consent gate');
});

test('click while listening stops recognition (mode does not matter)', () => {
  app._setSttStatus('available');
  app._setSttMode('cloud');
  let stopped = false;
  app._setSttRecognition({ stop: () => { stopped = true; }, abort: () => {} });
  app._sttSetState('listening');
  app._sttHandleClick();
  assert.strictEqual(stopped, true);
});

test('click while downloading is ignored (no double-install)', () => {
  app._setSttStatus('downloadable');
  app._setSttMode('ondevice');
  app._sttSetState('downloading');
  let installCalls = 0;
  globalThis.window.SpeechRecognition = function() {};
  globalThis.window.SpeechRecognition.install = async () => { installCalls++; return true; };
  app._sttHandleClick();
  assert.strictEqual(installCalls, 0);
});

test('click while status downloadable, on-device mode, triggers install then start with processLocally: true', async () => {
  app._setSttStatus('downloadable');
  app._setSttMode('ondevice');
  let installArgs = null;
  let started = false;
  globalThis.window.SpeechRecognition = function() { this.start = () => { started = true; }; };
  globalThis.window.SpeechRecognition.install = async (args) => { installArgs = args; return true; };
  await app._sttInstallThenStart();
  assert.strictEqual(installArgs.processLocally, true);
  assert.strictEqual(started, true);
  assert.strictEqual(app._getSttStatus(), 'available');
});

test('install() resolving false shows an inline error and returns to idle', async () => {
  app._setSttStatus('downloadable');
  app._setSttMode('ondevice');
  globalThis.window.SpeechRecognition = function() {};
  globalThis.window.SpeechRecognition.install = async () => false;
  await app._sttInstallThenStart();
  assert.strictEqual(app._getSttState(), 'idle');
  assert.match(elements['compose-error'].textContent, /could not be installed/);
});

test('install() rejecting shows an inline error and returns to idle -- no silent failure', async () => {
  app._setSttStatus('downloadable');
  app._setSttMode('ondevice');
  globalThis.window.SpeechRecognition = function() {};
  globalThis.window.SpeechRecognition.install = async () => { throw new Error('disk full'); };
  await app._sttInstallThenStart();
  assert.strictEqual(app._getSttState(), 'idle');
  assert.match(elements['compose-error'].textContent, /could not be installed/);
  assert.match(elements['compose-error'].textContent, /disk full/);
});

// --- Cloud-dictation opt-in gate ---

test('click while idle, cloud mode, first use on this device: shows the consent gate, does NOT start recognition', () => {
  app._setSttStatus('available');
  app._setSttMode('cloud');
  let started = false;
  globalThis.window.SpeechRecognition = function() { this.start = () => { started = true; }; };
  app._sttHandleClick();
  assert.strictEqual(started, false, 'cloud dictation must never start without recorded consent');
  assert.strictEqual(app._getSttConsentPending(), true);
  assert.strictEqual(elements['compose-cloud-consent'].classList.contains('hidden'), false);
});

test('clicking "Use cloud dictation" persists consent, hides the gate, and starts recognition with processLocally:false', () => {
  app._setSttStatus('available');
  app._setSttMode('cloud');
  let started = false;
  globalThis.window.SpeechRecognition = function() { this.start = () => { started = true; }; };
  app._sttHandleClick(); // shows the gate
  assert.strictEqual(app._getSttConsentPending(), true);
  app._sttCloudConsentAllow();
  assert.strictEqual(started, true);
  assert.strictEqual(app._getSttConsentPending(), false);
  assert.strictEqual(elements['compose-cloud-consent'].classList.contains('hidden'), true);
  assert.strictEqual(globalThis.localStorage.getItem(app.STT_CLOUD_CONSENT_STORAGE_KEY), 'granted');
});

test('clicking "Not now" hides the gate without starting recognition or persisting consent', () => {
  app._setSttStatus('available');
  app._setSttMode('cloud');
  let started = false;
  globalThis.window.SpeechRecognition = function() { this.start = () => { started = true; }; };
  app._sttHandleClick();
  app._sttCloudConsentCancel();
  assert.strictEqual(started, false);
  assert.strictEqual(app._getSttConsentPending(), false);
  assert.strictEqual(elements['compose-cloud-consent'].classList.contains('hidden'), true);
  assert.strictEqual(app._sttCloudConsentGranted(), false);
});

test('once consent is granted on this device, a later click starts cloud dictation immediately -- no repeat prompt', () => {
  app._setSttStatus('available');
  app._setSttMode('cloud');
  globalThis.localStorage.setItem(app.STT_CLOUD_CONSENT_STORAGE_KEY, 'granted');
  let started = false;
  globalThis.window.SpeechRecognition = function() { this.start = () => { started = true; }; };
  app._sttHandleClick();
  assert.strictEqual(started, true);
  assert.strictEqual(app._getSttConsentPending(), false, 'must not show the gate again once already granted');
});

test('consent is per-device: localStorage blocked/empty means the gate is shown even if a previous test in this run granted it (isolated via beforeEach)', () => {
  // beforeEach resets _localStorageStore -- this asserts that reset actually
  // takes effect, i.e. consent does not leak across sessions/devices.
  assert.strictEqual(app._sttCloudConsentGranted(), false);
});

test('_sttHandleClick ignores a second click while the consent gate is already showing (belt-and-suspenders)', () => {
  app._setSttStatus('available');
  app._setSttMode('cloud');
  let constructCount = 0;
  globalThis.window.SpeechRecognition = function() { constructCount++; this.start = () => {}; };
  app._sttHandleClick(); // shows the gate
  app._sttHandleClick(); // should be a no-op, not a second gate-show or start
  assert.strictEqual(constructCount, 0);
  assert.strictEqual(app._getSttConsentPending(), true);
});

test('_composeClearDraft() hides a pending cloud-consent gate on session switch', () => {
  app._setSttStatus('available');
  app._setSttMode('cloud');
  app._sttShowCloudConsent();
  assert.strictEqual(app._getSttConsentPending(), true);
  app._composeClearDraft();
  assert.strictEqual(app._getSttConsentPending(), false);
  assert.strictEqual(elements['compose-cloud-consent'].classList.contains('hidden'), true);
});

// --- No silent swallow across the STT functions (mirrors _composeSend's own guard) ---

test('no empty catch blocks anywhere in the STT section of app.js', () => {
  const startIdx = appSource.indexOf('On-device dictation (STT)');
  assert.notStrictEqual(startIdx, -1, 'could not locate the STT section banner in app.js');
  const endIdx = appSource.indexOf('Follow-up queue', startIdx);
  assert.notStrictEqual(endIdx, -1, 'could not locate the end of the STT section');
  const section = appSource.slice(startIdx, endIdx);
  assert.doesNotMatch(section, /catch\s*\([^)]*\)\s*\{\s*\}/, 'empty catch block found in the STT section');
});

test('every SpeechRecognitionErrorEvent code path renders a non-empty message', () => {
  const codes = ['no-speech', 'not-allowed', 'service-not-allowed', 'audio-capture', 'language-not-supported', 'aborted', 'network', 'something-else'];
  for (const code of codes) {
    elements['compose-error'] = makeStubElement(['hidden']);
    app._sttHandleError({ error: code });
    assert.ok(elements['compose-error'].textContent.length > 0, `error code '${code}' produced an empty message`);
  }
});
