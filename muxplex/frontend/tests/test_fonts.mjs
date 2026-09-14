import { createRequire } from 'node:module';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);

function loadFonts() {
  const modulePath = join(__dirname, '..', 'fonts.js');
  delete require.cache[require.resolve(modulePath)];
  const added = [];
  const requested = [];
  let loads = 0;
  class MockFontFace {
    constructor(family, source) { this.family = family; this.source = source; }
    load() { loads++; return Promise.resolve(this); }
  }
  globalThis.FontFace = MockFontFace;
  globalThis.document = {
    fonts: {
      add(face) { added.push(face); },
      load(descriptor) { requested.push(descriptor); return Promise.resolve([]); },
    },
  };
  globalThis.window = {};
  require(modulePath);
  return { fonts: globalThis.window.muxplexFonts, added, requested, get loads() { return loads; } };
}

test('font catalog defaults invalid values to FiraCode and System never creates a font request', async () => {
  const env = loadFonts();
  assert.equal(env.fonts.normalize('unknown'), 'FiraCode');
  assert.equal(env.fonts.normalize({}), 'FiraCode');
  assert.equal(env.fonts.normalize('System'), 'System');
  await env.fonts.ensureLoaded('System');
  assert.equal(env.loads, 0);
  assert.equal(env.added.length, 0);
});

test('optional font loads once, uses its verified family, and is memoized', async () => {
  const env = loadFonts();
  const first = env.fonts.ensureLoaded('FiraCode');
  const second = env.fonts.ensureLoaded('FiraCode');
  assert.strictEqual(first, second, 'concurrent selection must share one FontFace request');
  await first;
  await env.fonts.ensureLoaded('FiraCode');
  assert.equal(env.loads, 1);
  assert.equal(env.added.length, 1);
  assert.equal(env.added[0].family, 'FiraCode Nerd Font Mono');
  assert.match(env.requested[0], /FiraCode Nerd Font Mono/);
});