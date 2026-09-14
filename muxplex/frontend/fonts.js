// Optional terminal faces. Kept separate from app.js/terminal.js so the
// catalog, names, load de-duplication, and failure retry semantics have one
// small owner. This is a classic script: do not introduce top-level bindings
// that collide with the other frontend scripts.
;(function muxplexFontsModule() {
  'use strict';

  var SYSTEM_FAMILY = "'SF Mono', 'Fira Code', Consolas, monospace";
  var catalog = Object.freeze({
    System: Object.freeze({ label: 'System mono', cssFamily: SYSTEM_FAMILY }),
    FiraCode: Object.freeze({
      label: 'Fira Code Nerd Font Mono',
      family: 'FiraCode Nerd Font Mono',
      cssFamily: "'FiraCode Nerd Font Mono', " + SYSTEM_FAMILY,
      url: '/fonts/FiraCodeNerdFontMono-Regular.ttf',
    }),
    JetBrainsMono: Object.freeze({
      label: 'JetBrains Mono Nerd Font Mono',
      family: 'JetBrainsMono NFM',
      cssFamily: "'JetBrainsMono NFM', " + SYSTEM_FAMILY,
      url: '/fonts/JetBrainsMonoNerdFontMono-Regular.ttf',
    }),
  });
  var inflight = Object.create(null);
  var completed = Object.create(null);

  function normalize(value) {
    return typeof value === 'string' && catalog[value] ? value : 'FiraCode';
  }

  function cssFamily(value) {
    return catalog[normalize(value)].cssFamily;
  }

  function ensureLoaded(value) {
    var name = normalize(value);
    if (name === 'System') return Promise.resolve(catalog.System);
    if (completed[name]) return Promise.resolve(catalog[name]);
    if (inflight[name]) return inflight[name];
    if (typeof FontFace === 'undefined' || !document.fonts ||
        !document.fonts.add || !document.fonts.load) {
      return Promise.reject(new Error('This browser cannot load optional terminal fonts.'));
    }
    var entry = catalog[name];
    var face = new FontFace(entry.family, 'url(' + entry.url + ')', {
      style: 'normal',
      weight: '400',
    });
    inflight[name] = face.load().then(function(loadedFace) {
      document.fonts.add(loadedFace);
      return document.fonts.load('16px "' + entry.family + '"');
    }).then(function() {
      completed[name] = true;
      delete inflight[name];
      return entry;
    }, function(error) {
      delete inflight[name]; // A later deliberate selection may retry.
      throw error;
    });
    return inflight[name];
  }

  window.muxplexFonts = Object.freeze({
    catalog: catalog,
    normalize: normalize,
    cssFamily: cssFamily,
    ensureLoaded: ensureLoaded,
  });
}());