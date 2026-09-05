// Clipboard image paste in the agent chat composer (muxplex-1i9).
//
// WHY THIS FILE IS SELF-CONTAINED: test_chat_panel.mjs already has a
// (larger) harness of the same shape, but it does not export it, and this
// work landed alongside several other lanes editing this repo. A new file
// with its own compact harness costs some duplication and buys a zero
// merge-conflict surface. The harness below is the same idea as that
// file's -- load the REAL chat.js into a node:vm context over a stub DOM
// and drive it with real DOM events -- trimmed to what these tests need.
//
// WHAT IS BEING PROTECTED. Pasting a screenshot into a chat box is the
// fastest bug report a person can file, and the failure mode that makes it
// worthless is silence: the paste appears to work, the message sends, and
// the model answers a question about an image it was never given. So every
// test here asserts on one of two things -- the attachment is VISIBLE
// before it sends and actually reaches the request body, or the refusal is
// VISIBLE and nothing is sent at all. There is deliberately no test
// asserting a quiet fallback, because a quiet fallback is the bug.

import vm from 'node:vm';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import assert from 'node:assert/strict';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const chatJsSource = fs.readFileSync(path.join(__dirname, '..', 'chat.js'), 'utf-8');

// ---------------------------------------------------------------------
// Stub DOM
// ---------------------------------------------------------------------

function makeClassList() {
  const classes = new Set();
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
  };
}

function createDomEnvironment() {
  const idRegistry = new Map();

  class StubNode {
    constructor(tag) {
      this.tagName = String(tag || 'div').toUpperCase();
      this._children = [];
      this._text = '';
      this._attrs = {};
      this._listeners = {};
      this.style = {};
      this.classList = makeClassList();
      this.parentNode = null;
      this.value = '';
      this.selectionStart = 0;
      this.selectionEnd = 0;
      this.scrollTop = 0;
      this.scrollHeight = 20;
      this.disabled = false;
      this._id = '';
    }
    get id() { return this._id; }
    set id(v) {
      if (this._id && idRegistry.get(this._id) === this) idRegistry.delete(this._id);
      this._id = String(v == null ? '' : v);
      if (this._id) idRegistry.set(this._id, this);
    }
    get textContent() {
      if (this._children.length === 0) return this._text;
      return this._children.map((c) => c.textContent).join('');
    }
    set textContent(v) {
      // Faithful to the real DOM, and load-bearing here: assigning
      // textContent replaces the children with ONE text node, it does not
      // set a magic string that later appendChild()s erase. chat.js writes
      // a user bubble's caption and then appends the attachment
      // thumbnails; a stub that stored `_text` separately would report the
      // caption gone the moment an <img> arrived, which is a lie about
      // what a browser does.
      const s = v == null ? '' : String(v);
      this._text = s;
      this._children = [];
      if (s !== '') {
        const t = new StubNode('#text');
        t._text = s;
        t.parentNode = this;
        this._children.push(t);
      }
    }
    appendChild(child) { this._children.push(child); child.parentNode = this; return child; }
    removeChild(child) {
      const i = this._children.indexOf(child);
      if (i !== -1) this._children.splice(i, 1);
      child.parentNode = null;
      return child;
    }
    insertBefore(child, ref) {
      const i = ref ? this._children.indexOf(ref) : -1;
      if (i === -1) this._children.push(child);
      else this._children.splice(i, 0, child);
      child.parentNode = this;
      return child;
    }
    setAttribute(k, v) { this._attrs[k] = String(v); }
    getAttribute(k) {
      return Object.prototype.hasOwnProperty.call(this._attrs, k) ? this._attrs[k] : null;
    }
    removeAttribute(k) { delete this._attrs[k]; }
    hasAttribute(k) { return Object.prototype.hasOwnProperty.call(this._attrs, k); }
    addEventListener(ev, fn) { (this._listeners[ev] = this._listeners[ev] || []).push(fn); }
    removeEventListener(ev, fn) {
      if (!this._listeners[ev]) return;
      this._listeners[ev] = this._listeners[ev].filter((f) => f !== fn);
    }
    _fire(ev, evObj) { (this._listeners[ev] || []).slice().forEach((fn) => fn(evObj || {})); }
    click() { this._fire('click', {}); }
    focus() {}
    /** Walk the whole subtree -- the tests need to find generated chips and
     * <img> nodes chat.js created, which no flat registry can see. */
    _descendants() {
      const out = [];
      const walk = (n) => { n._children.forEach((c) => { out.push(c); walk(c); }); };
      walk(this);
      return out;
    }
    querySelector() { return null; }
    querySelectorAll() { return []; }
  }

  function createTextNode(text) {
    const n = new StubNode('#text');
    n.textContent = text;
    return n;
  }

  function makeDialog() {
    const el = new StubNode('dialog');
    el.open = false;
    el.showModal = function () { this.open = true; };
    el.close = function () { this.open = false; this._fire('close', {}); };
    return el;
  }

  const bodyEl = new StubNode('body');

  return {
    StubNode,
    makeDialog,
    idRegistry,
    document: {
      readyState: 'complete',
      title: 'muxplex test',
      visibilityState: 'visible',
      body: bodyEl,
      getElementById: (id) => idRegistry.get(id) || null,
      createElement: (tag) => new StubNode(tag),
      createTextNode,
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener: () => {},
      removeEventListener: () => {},
    },
    window: {
      innerWidth: 1024,
      innerHeight: 768,
      addEventListener: () => {},
      removeEventListener: () => {},
      matchMedia: () => ({ matches: false, addListener() {}, removeListener() {} }),
    },
  };
}

// Kept in lockstep with chat.js's init() by
// `test_required_ids_still_match_chat_js` at the bottom of this file.
const REQUIRED_IDS = [
  'chat-confirm-dialog',
  'chat-panel', 'chat-messages', 'chat-input', 'chat-send-btn', 'chat-new-btn',
  'chat-open-btn', 'chat-export-btn',
  'chat-panel-header', 'chat-composer', 'chat-byline',
  'chat-gate', 'chat-gate-text', 'chat-gate-settings-btn',
  'chat-confirm-backdrop',
  'chat-confirm-session', 'chat-confirm-text', 'chat-confirm-keys',
  'chat-confirm-cancel-btn', 'chat-confirm-send-btn',
];
// #chat-attachments is the attachment strip. It is OPTIONAL on purpose:
// an older index.html without it must still boot a working text-only
// panel rather than throwing, and `test_paste_without_the_strip_...`
// below pins that degradation to "paste does nothing special", never
// "paste silently eats the image".
const OPTIONAL_IDS = ['chat-live', 'chat-key-hint', 'chat-export-link', 'chat-attachments'];

/** A FileReader stub good enough for readAsDataURL: resolves on a
 * microtask with `file._dataUrl`. */
function makeFileReaderStub() {
  return class FileReaderStub {
    constructor() { this.result = null; this.onload = null; this.onerror = null; }
    readAsDataURL(file) {
      Promise.resolve().then(() => {
        if (file && file._failRead) {
          if (this.onerror) this.onerror({});
          return;
        }
        this.result = (file && file._dataUrl) || 'data:application/octet-stream;base64,';
        if (this.onload) this.onload({ target: this });
      });
    }
  };
}

function loadChatPanel({ fetchImpl, withAttachmentStrip = true } = {}) {
  const env = createDomEnvironment();
  const els = {};
  const ids = REQUIRED_IDS.concat(
    OPTIONAL_IDS.filter((id) => withAttachmentStrip || id !== 'chat-attachments')
  );
  ids.forEach((id) => {
    const el = new env.StubNode(id === 'chat-input' ? 'textarea' : 'div');
    el.id = id;
    els[id] = el;
  });
  els['chat-confirm-dialog'] = env.makeDialog();
  els['chat-confirm-dialog'].id = 'chat-confirm-dialog';

  const store = {};
  const fetchCalls = [];
  const blobParts = [];
  const sandbox = {
    console: { error() {}, warn() {}, log() {} },
    window: env.window,
    document: env.document,
    localStorage: {
      getItem: (k) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
    },
    fetch: async (url, opts) => { fetchCalls.push({ url, opts }); return fetchImpl(url, opts); },
    navigator: { userAgent: 'test-agent' },
    location: { href: 'http://localhost/test' },
    performance: { now: () => Date.now() },
    Blob: class {
      constructor(parts, opts) {
        this.parts = parts;
        this.type = opts && opts.type;
        blobParts.push((parts || []).join(''));
      }
    },
    URL: { createObjectURL: () => 'blob:fake-url', revokeObjectURL: () => {} },
    FileReader: makeFileReaderStub(),
    setTimeout,
    clearTimeout,
    TextEncoder,
    TextDecoder,
  };
  const context = vm.createContext(sandbox);
  vm.runInContext(chatJsSource, context, { filename: 'chat.js' });
  return { els, document: context.document, window: context.window, fetchCalls, blobParts };
}

// ---------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------

const PNG_DATA_URL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==';

/** A clipboard image "file", shaped like the DataTransferItem.getAsFile()
 * result chat.js reads. */
function imageFile({ type = 'image/png', size = 2048, name = 'image.png', dataUrl = PNG_DATA_URL, failRead = false } = {}) {
  return { type, size, name, _dataUrl: dataUrl, _failRead: failRead };
}

/** A paste event carrying `files` as clipboard items, plus a
 * preventDefault spy. */
function pasteEvent(files, { textPlain = null } = {}) {
  const ev = {
    _prevented: false,
    preventDefault() { this._prevented = true; },
    clipboardData: {
      items: files.map((f) => ({ kind: 'file', type: f.type, getAsFile: () => f }))
        .concat(textPlain === null ? [] : [{ kind: 'string', type: 'text/plain', getAsFile: () => null }]),
      files,
      getData: () => (textPlain === null ? '' : textPlain),
    },
  };
  return ev;
}

function sseResponse(chunkObjs) {
  const raw = chunkObjs.map((c) => `data: ${JSON.stringify(c)}\n\n`).join('') + 'data: [DONE]\n\n';
  const bytes = new TextEncoder().encode(raw);
  let delivered = false;
  return {
    ok: true,
    status: 200,
    text: async () => raw,
    body: {
      getReader() {
        return {
          async read() {
            if (delivered) return { done: true, value: undefined };
            delivered = true;
            return { done: false, value: bytes };
          },
        };
      },
    },
  };
}

/** Records every /api/agent/chat/completions request body and answers with
 * a plain, tool-free turn. */
function makeCapturingFetch() {
  const requests = [];
  return {
    requests,
    fetchImpl: async (url, opts) => {
      if (url === '/api/agent/chat/completions') {
        requests.push(JSON.parse(opts.body));
        return sseResponse([
          { id: 'c1', choices: [{ delta: { content: 'ok' } }] },
          { id: 'c1', choices: [{ delta: {}, finish_reason: 'stop' }] },
        ]);
      }
      throw new Error('unexpected fetch url in test: ' + url);
    },
  };
}

async function waitUntil(fn, { timeout = 2000, interval = 5, label = 'condition' } = {}) {
  const start = Date.now();
  for (;;) {
    if (fn()) return;
    if (Date.now() - start > timeout) throw new Error(`waitUntil: timed out waiting for: ${label}`);
    await new Promise((r) => setTimeout(r, interval));
  }
}

/** Paste `files` into the composer and wait for the strip to settle. */
async function paste(panel, files, opts) {
  const ev = pasteEvent(files, opts);
  panel.els['chat-input']._fire('paste', ev);
  // FileReader resolves on a microtask; give the strip a turn to render.
  await new Promise((r) => setTimeout(r, 5));
  return ev;
}

function stripText(panel) {
  return panel.els['chat-attachments'].textContent;
}

function transcriptImages(panel) {
  return panel.els['chat-messages']._descendants().filter((n) => n.tagName === 'IMG');
}

function lastUserMessage(request) {
  const users = request.messages.filter((m) => m.role === 'user');
  return users[users.length - 1];
}

// ---------------------------------------------------------------------
// GROUP 1 -- a pasted image is VISIBLE before it is sent
// ---------------------------------------------------------------------

test('1i9: pasting an image attaches it and shows it in the composer', async () => {
  const { fetchImpl } = makeCapturingFetch();
  const panel = loadChatPanel({ fetchImpl });

  const ev = await paste(panel, [imageFile({ size: 2048, name: 'shot.png' })]);

  assert.ok(ev._prevented, 'an image paste must preventDefault -- otherwise the browser also drops a filename into the textarea');
  assert.ok(!panel.els['chat-attachments'].classList.contains('hidden'), 'the attachment strip must become visible');
  const text = stripText(panel);
  assert.match(text, /shot\.png/, `the chip must name the file: ${text}`);
  assert.match(text, /KB|MB|bytes/i, `the chip must state the size so an oversize paste is obvious: ${text}`);
});

test('1i9: the attached image is shown as a thumbnail, not just named', async () => {
  const { fetchImpl } = makeCapturingFetch();
  const panel = loadChatPanel({ fetchImpl });
  await paste(panel, [imageFile()]);

  const imgs = panel.els['chat-attachments']._descendants().filter((n) => n.tagName === 'IMG');
  assert.strictEqual(imgs.length, 1, 'expected exactly one preview thumbnail in the strip');
  assert.strictEqual(imgs[0].getAttribute('src'), PNG_DATA_URL, 'the thumbnail must show the pasted image itself');
});

test('1i9: an attachment can be removed before sending', async () => {
  const { fetchImpl, requests } = makeCapturingFetch();
  const panel = loadChatPanel({ fetchImpl });
  await paste(panel, [imageFile({ name: 'oops.png' })]);
  assert.match(stripText(panel), /oops\.png/);

  const removeBtn = panel.els['chat-attachments']._descendants()
    .find((n) => n.getAttribute('data-attachment-remove') !== null);
  assert.ok(removeBtn, 'every chip must carry a remove control');
  removeBtn._fire('click', {});

  assert.doesNotMatch(stripText(panel), /oops\.png/, 'removing a chip must drop the attachment');
  assert.ok(panel.els['chat-attachments'].classList.contains('hidden'), 'the strip must hide again when empty');

  panel.els['chat-input'].value = 'never mind';
  panel.els['chat-send-btn']._fire('click');
  await waitUntil(() => panel.els['chat-send-btn'].disabled === false, { label: 'turn to finish' });
  assert.strictEqual(typeof lastUserMessage(requests[0]).content, 'string',
    'a removed attachment must not still travel in the request');
});

// ---------------------------------------------------------------------
// GROUP 2 -- refusals are LOUD, and nothing is sent
// ---------------------------------------------------------------------

test('1i9: an oversize image is refused with a stated limit, not silently dropped', async () => {
  const { fetchImpl } = makeCapturingFetch();
  const panel = loadChatPanel({ fetchImpl });

  await paste(panel, [imageFile({ size: 50 * 1024 * 1024, name: 'huge.png' })]);

  const shown = panel.els['chat-composer'].textContent + ' ' + stripText(panel);
  assert.match(shown, /too large|exceeds|limit/i, `an oversize paste must say so: ${shown}`);
  assert.match(shown, /MB/i, `the refusal must state the actual limit: ${shown}`);
  const imgs = panel.els['chat-attachments']._descendants().filter((n) => n.tagName === 'IMG');
  assert.strictEqual(imgs.length, 0, 'a refused image must not be attached anyway');
});

test('1i9: an unsupported image type is refused by name', async () => {
  const { fetchImpl } = makeCapturingFetch();
  const panel = loadChatPanel({ fetchImpl });

  await paste(panel, [imageFile({ type: 'image/tiff', name: 'scan.tiff' })]);

  const shown = panel.els['chat-composer'].textContent + ' ' + stripText(panel);
  assert.match(shown, /image\/tiff/, `the refusal must name the rejected type: ${shown}`);
  assert.match(shown, /png/i, `the refusal must name what IS accepted: ${shown}`);
});

test('1i9: pasting more images than the limit refuses the extras out loud', async () => {
  const { fetchImpl } = makeCapturingFetch();
  const panel = loadChatPanel({ fetchImpl });

  await paste(panel, [
    imageFile({ name: 'a.png' }), imageFile({ name: 'b.png' }),
    imageFile({ name: 'c.png' }), imageFile({ name: 'd.png' }),
    imageFile({ name: 'e.png' }),
  ]);

  const shown = panel.els['chat-composer'].textContent + ' ' + stripText(panel);
  assert.match(shown, /most|limit|maximum/i, `hitting the count cap must be stated: ${shown}`);
  const imgs = panel.els['chat-attachments']._descendants().filter((n) => n.tagName === 'IMG');
  assert.ok(imgs.length >= 1 && imgs.length <= 4, `expected the cap to be enforced, attached ${imgs.length}`);
});

test('1i9: a clipboard read failure is reported, never swallowed', async () => {
  const { fetchImpl } = makeCapturingFetch();
  const panel = loadChatPanel({ fetchImpl });

  await paste(panel, [imageFile({ name: 'broken.png', failRead: true })]);

  const shown = panel.els['chat-composer'].textContent + ' ' + stripText(panel);
  assert.match(shown, /could not|failed|unable/i, `a failed read must surface: ${shown}`);
});

// ---------------------------------------------------------------------
// GROUP 3 -- the image actually reaches the request
// ---------------------------------------------------------------------

test('1i9: a sent message carries the image as an OpenAI image_url content block', async () => {
  const { fetchImpl, requests } = makeCapturingFetch();
  const panel = loadChatPanel({ fetchImpl });

  await paste(panel, [imageFile()]);
  panel.els['chat-input'].value = 'what is wrong here?';
  panel.els['chat-send-btn']._fire('click');
  await waitUntil(() => panel.els['chat-send-btn'].disabled === false, { label: 'turn to finish' });

  const msg = lastUserMessage(requests[0]);
  assert.ok(Array.isArray(msg.content), `a message with an attachment must use content blocks, got: ${typeof msg.content}`);
  const textPart = msg.content.find((p) => p.type === 'text');
  const imgPart = msg.content.find((p) => p.type === 'image_url');
  assert.ok(textPart && textPart.text === 'what is wrong here?', 'the typed text must still travel');
  assert.ok(imgPart, 'the pasted image must travel as an image_url block');
  assert.strictEqual(imgPart.image_url.url, PNG_DATA_URL, 'the image data itself must reach the request');
});

test('1i9: an image with no caption still sends (the screenshot IS the message)', async () => {
  const { fetchImpl, requests } = makeCapturingFetch();
  const panel = loadChatPanel({ fetchImpl });

  await paste(panel, [imageFile()]);
  panel.els['chat-input'].value = '';
  panel.els['chat-send-btn']._fire('click');
  await waitUntil(() => requests.length === 1, { label: 'a request to be made' });

  const msg = lastUserMessage(requests[0]);
  assert.ok(Array.isArray(msg.content));
  assert.ok(msg.content.some((p) => p.type === 'image_url'), 'the image must send even with an empty composer');
});

test('1i9: an empty composer with NO attachment still sends nothing', async () => {
  const { fetchImpl, requests } = makeCapturingFetch();
  const panel = loadChatPanel({ fetchImpl });

  panel.els['chat-input'].value = '   ';
  panel.els['chat-send-btn']._fire('click');
  await new Promise((r) => setTimeout(r, 20));
  assert.strictEqual(requests.length, 0, 'an empty send must remain a no-op');
});

test('1i9: the transcript shows what was actually sent', async () => {
  const { fetchImpl } = makeCapturingFetch();
  const panel = loadChatPanel({ fetchImpl });

  await paste(panel, [imageFile()]);
  panel.els['chat-input'].value = 'look';
  panel.els['chat-send-btn']._fire('click');
  await waitUntil(() => panel.els['chat-send-btn'].disabled === false, { label: 'turn to finish' });

  const imgs = transcriptImages(panel);
  assert.strictEqual(imgs.length, 1, 'the user bubble must show the image that was sent');
  assert.strictEqual(imgs[0].getAttribute('src'), PNG_DATA_URL);
  assert.match(panel.els['chat-messages'].textContent, /look/, 'the caption must be in the transcript too');
});

test('1i9: the composer is cleared after a send, so the next message is not double-attached', async () => {
  const { fetchImpl, requests } = makeCapturingFetch();
  const panel = loadChatPanel({ fetchImpl });

  await paste(panel, [imageFile()]);
  panel.els['chat-input'].value = 'one';
  panel.els['chat-send-btn']._fire('click');
  await waitUntil(() => panel.els['chat-send-btn'].disabled === false, { label: 'first turn' });

  assert.ok(panel.els['chat-attachments'].classList.contains('hidden'), 'the strip must clear after sending');

  panel.els['chat-input'].value = 'two';
  panel.els['chat-send-btn']._fire('click');
  await waitUntil(() => requests.length === 2, { label: 'second turn' });

  const second = lastUserMessage(requests[1]);
  assert.strictEqual(second.content, 'two', 'the second message must not re-send the first image');
});

// ---------------------------------------------------------------------
// GROUP 4 -- retention: image bytes must not leak into the debug export
// ---------------------------------------------------------------------

// A pasted screenshot of a terminal routinely contains an API key, a
// hostname, or a password prompt. The debug export is a FILE that leaves
// the browser and gets pasted into issues and chat threads. Those two
// facts together are why the image bytes must never end up inside it:
// exporting a debug record should never be the thing that leaks the
// screenshot the user only meant to show the agent once.
test('1i9: the exported debug record never contains the raw image bytes', async () => {
  const { fetchImpl } = makeCapturingFetch();
  const panel = loadChatPanel({ fetchImpl });

  await paste(panel, [imageFile({ dataUrl: 'data:image/png;base64,SECRETSCREENSHOTBYTES' })]);
  panel.els['chat-input'].value = 'look';
  panel.els['chat-send-btn']._fire('click');
  await waitUntil(() => panel.els['chat-send-btn'].disabled === false, { label: 'turn to finish' });

  panel.els['chat-export-btn']._fire('click', {});
  await new Promise((r) => setTimeout(r, 30));

  const exported = panel.blobParts.join('\n');
  assert.ok(exported.length > 0, 'expected the export to have written something');
  assert.ok(!exported.includes('SECRETSCREENSHOTBYTES'),
    'the export must not embed the pasted image bytes');
  assert.match(exported, /image|attach/i,
    'the export must still SAY an image was attached -- redacted, not erased');
});

// ---------------------------------------------------------------------
// GROUP 5 -- everything that must NOT change
// ---------------------------------------------------------------------

test('1i9: pasting plain text is completely untouched', async () => {
  const { fetchImpl, requests } = makeCapturingFetch();
  const panel = loadChatPanel({ fetchImpl });

  const ev = pasteEvent([], { textPlain: 'hello from the clipboard' });
  panel.els['chat-input']._fire('paste', ev);
  await new Promise((r) => setTimeout(r, 5));

  assert.ok(!ev._prevented, 'a text paste must keep the textarea default -- never intercepted');
  assert.ok(panel.els['chat-attachments'].classList.contains('hidden'), 'a text paste must not open the strip');

  panel.els['chat-input'].value = 'typed';
  panel.els['chat-send-btn']._fire('click');
  await waitUntil(() => requests.length === 1, { label: 'a request' });
  assert.strictEqual(lastUserMessage(requests[0]).content, 'typed',
    'a text-only turn must still send a plain string, not content blocks');
});

test('1i9: a text-only conversation still sends plain-string content', async () => {
  const { fetchImpl, requests } = makeCapturingFetch();
  const panel = loadChatPanel({ fetchImpl });

  panel.els['chat-input'].value = 'what sessions do I have?';
  panel.els['chat-send-btn']._fire('click');
  await waitUntil(() => panel.els['chat-send-btn'].disabled === false, { label: 'turn to finish' });

  const msg = lastUserMessage(requests[0]);
  assert.strictEqual(msg.content, 'what sessions do I have?');
});

test('1i9: a new conversation drops any pending attachment', async () => {
  const { fetchImpl } = makeCapturingFetch();
  const panel = loadChatPanel({ fetchImpl });

  await paste(panel, [imageFile({ name: 'stale.png' })]);
  assert.match(stripText(panel), /stale\.png/);

  panel.els['chat-new-btn']._fire('click');
  assert.doesNotMatch(stripText(panel), /stale\.png/, 'a new conversation must not inherit a pending attachment');
  assert.ok(panel.els['chat-attachments'].classList.contains('hidden'));
});

test('1i9: paste without the attachment strip degrades to plain text, never a silent eat', async () => {
  const { fetchImpl } = makeCapturingFetch();
  const panel = loadChatPanel({ fetchImpl, withAttachmentStrip: false });

  const ev = await paste(panel, [imageFile()]);
  assert.ok(!ev._prevented,
    'with no strip to show it in, the paste must fall through to the browser default rather than being intercepted and dropped');
});

// ---------------------------------------------------------------------
// GROUP 6 -- contract guard
// ---------------------------------------------------------------------

test('1i9: REQUIRED_IDS above still matches chat.js init()', () => {
  const m = chatJsSource.match(/__missing\.push\("([^"]+)"\)/g) || [];
  const declared = m.map((s) => s.replace(/^__missing\.push\("/, '').replace(/"\)$/, ''));
  const missing = declared.filter((id) => !REQUIRED_IDS.includes(id));
  assert.deepStrictEqual(missing, [],
    `chat.js now hard-requires ids this harness does not supply: ${missing.join(', ')}`);
});
