'use strict';

/**
 * browser-apis.js — Provide browser constructor stubs that VMP expects.
 *
 * Philosophy: VMP scripts do `new Event(...)`, `new MutationObserver(cb)`,
 * `canvas.getContext('2d')` etc. If these aren't constructors, VMP crashes
 * — not from "detection" but from missing basic browser APIs.
 *
 * These are NOT detection surfaces. They're functional stubs:
 * - Each constructor can be `new`'d without crashing
 * - Returns objects with minimal expected interface
 * - All toString → [native code]
 *
 * Only provide what jsdom DOESN'T already provide. Check before overriding.
 */

// Store currentScript refs outside the vm context (not detectable via 'in' operator)
const _scriptElementRefs = new WeakMap();
// Track injected script elements for getElementsByTagName filter
const _injectedScripts = new WeakSet();

function installBrowserApis(ctx, markNativeHandle, fingerprint) {
  const { markNative } = markNativeHandle;
  const fp = fingerprint || {};

  // ─── Expose Web APIs from Node.js that vm context doesn't inherit ───
  installNodeWebApis(ctx, markNative);

  // ─── navigator.plugins (PluginArray with Chrome PDF plugins) ───
  installPlugins(ctx, markNative);

  // ─── Canvas getContext stub ───
  installCanvasStub(ctx, markNative, fp);

  // ─── Constructor stubs for APIs jsdom doesn't provide ───
  installConstructorStubs(ctx, markNative, fp);

  // ─── navigator sub-objects ───
  installNavigatorApis(ctx, markNative, fp);

  // ─── document.currentScript ───
  installCurrentScript(ctx, markNative);
}

// ═══════════════════════════════════════════════════════════════════
// Node.js Web APIs — these are Web-standard but vm context doesn't inherit them
// ═══════════════════════════════════════════════════════════════════
function installNodeWebApis(ctx, markNative) {
  // Web APIs that Node.js 18+ provides globally but vm contexts don't inherit
  const webApis = [
    'TextEncoder', 'TextDecoder',
    'ReadableStream', 'WritableStream', 'TransformStream',
    'ByteLengthQueuingStrategy', 'CountQueuingStrategy',
    'CompressionStream', 'DecompressionStream',
    'Response', 'Request', 'Headers',
    'FormData', 'File',
    'Blob',
    'URLSearchParams',
    'DOMException',
    'AbortController', 'AbortSignal',
    'Event', 'EventTarget', 'CustomEvent',
    'MessageChannel', 'MessagePort',
    'BroadcastChannel',
    'structuredClone',
    'atob', 'btoa',
    'queueMicrotask',
    'performance',
    'crypto',
  ];

  for (const name of webApis) {
    if (ctx[name] === undefined || ctx[name] === null) {
      // Try to get from Node.js global scope
      const val = globalThis[name];
      if (val !== undefined) {
        try {
          Object.defineProperty(ctx, name, {
            value: val,
            writable: true,
            enumerable: true,
            configurable: true,
          });
          // markNative on constructors
          if (typeof val === 'function') {
            markNative(val, name);
          }
        } catch (e) { /* non-configurable — skip */ }
      }
    }
  }

  // Ensure crypto.subtle is available (Node.js webcrypto)
  if (ctx.crypto && !ctx.crypto.subtle) {
    try {
      const nodeCrypto = require('crypto');
      if (nodeCrypto.webcrypto && nodeCrypto.webcrypto.subtle) {
        Object.defineProperty(ctx.crypto, 'subtle', {
          value: nodeCrypto.webcrypto.subtle,
          writable: true,
          enumerable: true,
          configurable: true,
        });
      }
    } catch (e) {}
  }
  if (!ctx.crypto) {
    try {
      const nodeCrypto = require('crypto');
      if (nodeCrypto.webcrypto) {
        ctx.crypto = nodeCrypto.webcrypto;
      }
    } catch (e) {}
  }

  // ImageData — not in Node.js but commonly expected
  if (!ctx.ImageData) {
    ctx.ImageData = markNative(function ImageData(sw, sh) {
      if (arguments.length < 2) throw new TypeError("Failed to construct 'ImageData': 2 arguments required");
      const w = typeof sw === 'number' ? sw : sw.length / 4;
      const h = sh || 1;
      this.width = w;
      this.height = h;
      this.data = typeof sw === 'number' ? new Uint8ClampedArray(w * h * 4) : new Uint8ClampedArray(sw);
    }, 'ImageData');
  }

  // DOMMatrix / DOMPoint / DOMRect — geometry APIs
  // Chrome: all constructor params are optional, so .length === 0
  // Chrome: ReadOnly !== Mutable (separate constructors)
  // Chrome: DOMMatrix.prototype inherits from DOMMatrixReadOnly.prototype
  // Chrome: properties are GETTERS on prototype (not own data on instances)
  const ctxObjProto = (ctx.Object || Object).prototype;
  if (!ctx.DOMMatrix) {
    // --- DOMMatrixReadOnly ---
    const matrixStore = new WeakMap();
    ctx.DOMMatrixReadOnly = markNative(function DOMMatrixReadOnly(init) {
      matrixStore.set(this, {
        a: 1, b: 0, c: 0, d: 1, e: 0, f: 0,
        m11: 1, m12: 0, m13: 0, m14: 0,
        m21: 0, m22: 1, m23: 0, m24: 0,
        m31: 0, m32: 0, m33: 1, m34: 0,
        m41: 0, m42: 0, m43: 0, m44: 1,
        is2D: true, isIdentity: true,
      });
    }, 'DOMMatrixReadOnly', 0);
    ctx.DOMMatrixReadOnly.prototype = Object.create(ctxObjProto);
    Object.defineProperty(ctx.DOMMatrixReadOnly.prototype, Symbol.toStringTag, { value: 'DOMMatrixReadOnly', configurable: true });
    Object.defineProperty(ctx.DOMMatrixReadOnly.prototype, 'constructor', { value: ctx.DOMMatrixReadOnly, writable: true, configurable: true });
    // ReadOnly getters on prototype
    const matrixROProps = ['a','b','c','d','e','f','m11','m12','m13','m14','m21','m22','m23','m24','m31','m32','m33','m34','m41','m42','m43','m44','is2D','isIdentity'];
    for (const prop of matrixROProps) {
      Object.defineProperty(ctx.DOMMatrixReadOnly.prototype, prop, {
        get: markNative(function() {
          const s = matrixStore.get(this);
          return s ? s[prop] : undefined;
        }, `get ${prop}`),
        enumerable: true, configurable: true,
      });
    }

    // --- DOMMatrix (mutable — adds setters) ---
    ctx.DOMMatrix = markNative(function DOMMatrix(init) {
      matrixStore.set(this, {
        a: 1, b: 0, c: 0, d: 1, e: 0, f: 0,
        m11: 1, m12: 0, m13: 0, m14: 0,
        m21: 0, m22: 1, m23: 0, m24: 0,
        m31: 0, m32: 0, m33: 1, m34: 0,
        m41: 0, m42: 0, m43: 0, m44: 1,
        is2D: true, isIdentity: true,
      });
    }, 'DOMMatrix', 0);
    ctx.DOMMatrix.prototype = Object.create(ctx.DOMMatrixReadOnly.prototype);
    Object.defineProperty(ctx.DOMMatrix.prototype, Symbol.toStringTag, { value: 'DOMMatrix', configurable: true });
    Object.defineProperty(ctx.DOMMatrix.prototype, 'constructor', { value: ctx.DOMMatrix, writable: true, configurable: true });
    // Mutable: getter + setter on DOMMatrix.prototype (overrides ReadOnly getters)
    const matrixMutableProps = ['a','b','c','d','e','f','m11','m12','m13','m14','m21','m22','m23','m24','m31','m32','m33','m34','m41','m42','m43','m44'];
    for (const prop of matrixMutableProps) {
      Object.defineProperty(ctx.DOMMatrix.prototype, prop, {
        get: markNative(function() {
          const s = matrixStore.get(this);
          return s ? s[prop] : undefined;
        }, `get ${prop}`),
        set: markNative(function(v) {
          const s = matrixStore.get(this);
          if (s) s[prop] = v;
        }, `set ${prop}`),
        enumerable: true, configurable: true,
      });
    }
  }
  if (!ctx.DOMPoint) {
    // --- DOMPointReadOnly ---
    const pointStore = new WeakMap();
    ctx.DOMPointReadOnly = markNative(function DOMPointReadOnly(x, y, z, w) {
      pointStore.set(this, { x: x || 0, y: y || 0, z: z || 0, w: w !== undefined ? w : 1 });
    }, 'DOMPointReadOnly', 0);
    ctx.DOMPointReadOnly.prototype = Object.create(ctxObjProto);
    Object.defineProperty(ctx.DOMPointReadOnly.prototype, Symbol.toStringTag, { value: 'DOMPointReadOnly', configurable: true });
    Object.defineProperty(ctx.DOMPointReadOnly.prototype, 'constructor', { value: ctx.DOMPointReadOnly, writable: true, configurable: true });
    for (const prop of ['x','y','z','w']) {
      Object.defineProperty(ctx.DOMPointReadOnly.prototype, prop, {
        get: markNative(function() {
          const s = pointStore.get(this);
          return s ? s[prop] : undefined;
        }, `get ${prop}`),
        enumerable: true, configurable: true,
      });
    }

    // --- DOMPoint (mutable) ---
    ctx.DOMPoint = markNative(function DOMPoint(x, y, z, w) {
      pointStore.set(this, { x: x || 0, y: y || 0, z: z || 0, w: w !== undefined ? w : 1 });
    }, 'DOMPoint', 0);
    ctx.DOMPoint.prototype = Object.create(ctx.DOMPointReadOnly.prototype);
    Object.defineProperty(ctx.DOMPoint.prototype, Symbol.toStringTag, { value: 'DOMPoint', configurable: true });
    Object.defineProperty(ctx.DOMPoint.prototype, 'constructor', { value: ctx.DOMPoint, writable: true, configurable: true });
    for (const prop of ['x','y','z','w']) {
      Object.defineProperty(ctx.DOMPoint.prototype, prop, {
        get: markNative(function() {
          const s = pointStore.get(this);
          return s ? s[prop] : undefined;
        }, `get ${prop}`),
        set: markNative(function(v) {
          const s = pointStore.get(this);
          if (s) s[prop] = v;
        }, `set ${prop}`),
        enumerable: true, configurable: true,
      });
    }
  }
  if (!ctx.DOMRect) {
    // --- DOMRectReadOnly ---
    const rectStore = new WeakMap();
    ctx.DOMRectReadOnly = markNative(function DOMRectReadOnly(x, y, width, height) {
      rectStore.set(this, { x: x || 0, y: y || 0, width: width || 0, height: height || 0 });
    }, 'DOMRectReadOnly', 0);
    ctx.DOMRectReadOnly.prototype = Object.create(ctxObjProto);
    Object.defineProperty(ctx.DOMRectReadOnly.prototype, Symbol.toStringTag, { value: 'DOMRectReadOnly', configurable: true });
    Object.defineProperty(ctx.DOMRectReadOnly.prototype, 'constructor', { value: ctx.DOMRectReadOnly, writable: true, configurable: true });
    // ReadOnly getters: x, y, width, height, top, right, bottom, left
    for (const prop of ['x','y','width','height']) {
      Object.defineProperty(ctx.DOMRectReadOnly.prototype, prop, {
        get: markNative(function() {
          const s = rectStore.get(this);
          return s ? s[prop] : 0;
        }, `get ${prop}`),
        enumerable: true, configurable: true,
      });
    }
    // Computed properties (derived from x/y/width/height)
    Object.defineProperty(ctx.DOMRectReadOnly.prototype, 'top', {
      get: markNative(function() { const s = rectStore.get(this); return s ? Math.min(s.y, s.y + s.height) : 0; }, 'get top'),
      enumerable: true, configurable: true,
    });
    Object.defineProperty(ctx.DOMRectReadOnly.prototype, 'left', {
      get: markNative(function() { const s = rectStore.get(this); return s ? Math.min(s.x, s.x + s.width) : 0; }, 'get left'),
      enumerable: true, configurable: true,
    });
    Object.defineProperty(ctx.DOMRectReadOnly.prototype, 'bottom', {
      get: markNative(function() { const s = rectStore.get(this); return s ? Math.max(s.y, s.y + s.height) : 0; }, 'get bottom'),
      enumerable: true, configurable: true,
    });
    Object.defineProperty(ctx.DOMRectReadOnly.prototype, 'right', {
      get: markNative(function() { const s = rectStore.get(this); return s ? Math.max(s.x, s.x + s.width) : 0; }, 'get right'),
      enumerable: true, configurable: true,
    });
    Object.defineProperty(ctx.DOMRectReadOnly.prototype, 'toJSON', {
      value: markNative(function toJSON() {
        const s = rectStore.get(this);
        if (!s) return { x: 0, y: 0, width: 0, height: 0, top: 0, right: 0, bottom: 0, left: 0 };
        return { x: s.x, y: s.y, width: s.width, height: s.height, top: Math.min(s.y, s.y + s.height), right: Math.max(s.x, s.x + s.width), bottom: Math.max(s.y, s.y + s.height), left: Math.min(s.x, s.x + s.width) };
      }, 'toJSON'),
      writable: true, enumerable: false, configurable: true,
    });

    // --- DOMRect (mutable) ---
    ctx.DOMRect = markNative(function DOMRect(x, y, width, height) {
      rectStore.set(this, { x: x || 0, y: y || 0, width: width || 0, height: height || 0 });
    }, 'DOMRect', 0);
    ctx.DOMRect.prototype = Object.create(ctx.DOMRectReadOnly.prototype);
    Object.defineProperty(ctx.DOMRect.prototype, Symbol.toStringTag, { value: 'DOMRect', configurable: true });
    Object.defineProperty(ctx.DOMRect.prototype, 'constructor', { value: ctx.DOMRect, writable: true, configurable: true });
    for (const prop of ['x','y','width','height']) {
      Object.defineProperty(ctx.DOMRect.prototype, prop, {
        get: markNative(function() {
          const s = rectStore.get(this);
          return s ? s[prop] : 0;
        }, `get ${prop}`),
        set: markNative(function(v) {
          const s = rectStore.get(this);
          if (s) s[prop] = v;
        }, `set ${prop}`),
        enumerable: true, configurable: true,
      });
    }
  }

  // Path2D — methods on prototype (Chrome behavior)
  if (!ctx.Path2D) {
    const Path2D = markNative(function Path2D() {
      if (!(this instanceof Path2D) && !new.target) {
        throw new TypeError("Failed to construct 'Path2D': Please use the 'new' operator");
      }
    }, 'Path2D');
    Path2D.prototype = Object.create((ctx.Object || Object).prototype);
    Object.defineProperty(Path2D.prototype, Symbol.toStringTag, { value: 'Path2D', configurable: true });
    Object.defineProperty(Path2D.prototype, 'constructor', { value: Path2D, writable: true, configurable: true });
    const path2dMethods = ['addPath','closePath','moveTo','lineTo','arc','arcTo','rect','ellipse','bezierCurveTo','quadraticCurveTo'];
    for (const m of path2dMethods) {
      Object.defineProperty(Path2D.prototype, m, {
        value: markNative(function() {}, m),
        writable: true, enumerable: false, configurable: true,
      });
    }
    ctx.Path2D = Path2D;
  }
}

// ═══════════════════════════════════════════════════════════════════
// navigator.plugins — Chrome 120+ has 5 PDF/internal plugins
// ═══════════════════════════════════════════════════════════════════
function installPlugins(ctx, markNative) {
  const nav = ctx.navigator;
  if (!nav) return;

  // Create constructors FIRST so instances can use their prototypes
  const win = ctx;
  if (!win.MimeType) {
    win.MimeType = markNative(function MimeType() { throw new TypeError('Illegal constructor'); }, 'MimeType');
    win.MimeType.prototype = Object.create(ctx.Object.prototype);
    Object.defineProperty(win.MimeType.prototype, Symbol.toStringTag, { value: 'MimeType', configurable: true });
    Object.defineProperty(win.MimeType.prototype, 'constructor', { value: win.MimeType, writable: true, configurable: true });
  }
  if (!win.Plugin) {
    win.Plugin = markNative(function Plugin() { throw new TypeError('Illegal constructor'); }, 'Plugin');
    win.Plugin.prototype = Object.create(ctx.Object.prototype);
    Object.defineProperty(win.Plugin.prototype, Symbol.toStringTag, { value: 'Plugin', configurable: true });
    Object.defineProperty(win.Plugin.prototype, 'constructor', { value: win.Plugin, writable: true, configurable: true });
  }
  if (!win.PluginArray) {
    win.PluginArray = markNative(function PluginArray() { throw new TypeError('Illegal constructor'); }, 'PluginArray');
    win.PluginArray.prototype = Object.create(ctx.Object.prototype);
    Object.defineProperty(win.PluginArray.prototype, Symbol.toStringTag, { value: 'PluginArray', configurable: true });
    Object.defineProperty(win.PluginArray.prototype, 'constructor', { value: win.PluginArray, writable: true, configurable: true });
  }
  if (!win.MimeTypeArray) {
    win.MimeTypeArray = markNative(function MimeTypeArray() { throw new TypeError('Illegal constructor'); }, 'MimeTypeArray');
    win.MimeTypeArray.prototype = Object.create(ctx.Object.prototype);
    Object.defineProperty(win.MimeTypeArray.prototype, Symbol.toStringTag, { value: 'MimeTypeArray', configurable: true });
    Object.defineProperty(win.MimeTypeArray.prototype, 'constructor', { value: win.MimeTypeArray, writable: true, configurable: true });
  }

  // Plugin data matching Chrome 120+
  const pluginData = [
    {
      name: 'PDF Viewer', filename: 'internal-pdf-viewer',
      description: 'Portable Document Format',
      mimeTypes: [{ type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' }],
    },
    {
      name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer',
      description: 'Portable Document Format',
      mimeTypes: [{ type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' }],
    },
    {
      name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer',
      description: 'Portable Document Format',
      mimeTypes: [{ type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' }],
    },
    {
      name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer',
      description: 'Portable Document Format',
      mimeTypes: [{ type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' }],
    },
    {
      name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer',
      description: 'Portable Document Format',
      mimeTypes: [{ type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' }],
    },
  ];

  // Build MimeType objects
  function createMimeType(data, plugin) {
    const mt = Object.create(win.MimeType.prototype);
    Object.defineProperties(mt, {
      type: { get: markNative(function() { return data.type; }, 'get type'), enumerable: true, configurable: true },
      suffixes: { get: markNative(function() { return data.suffixes; }, 'get suffixes'), enumerable: true, configurable: true },
      description: { get: markNative(function() { return data.description; }, 'get description'), enumerable: true, configurable: true },
      enabledPlugin: { get: markNative(function() { return plugin; }, 'get enabledPlugin'), enumerable: true, configurable: true },
    });
    return mt;
  }

  // Build Plugin objects
  function createPlugin(data) {
    const plugin = Object.create(win.Plugin.prototype);
    const mimeTypes = data.mimeTypes.map(m => createMimeType(m, plugin));

    Object.defineProperties(plugin, {
      name: { get: markNative(function() { return data.name; }, 'get name'), enumerable: true, configurable: true },
      filename: { get: markNative(function() { return data.filename; }, 'get filename'), enumerable: true, configurable: true },
      description: { get: markNative(function() { return data.description; }, 'get description'), enumerable: true, configurable: true },
      length: { get: markNative(function() { return mimeTypes.length; }, 'get length'), enumerable: true, configurable: true },
    });

    // Indexed access
    for (let i = 0; i < mimeTypes.length; i++) {
      Object.defineProperty(plugin, i, { value: mimeTypes[i], enumerable: true, configurable: true });
    }

    plugin.item = markNative(function item(index) { return mimeTypes[index] || null; }, 'item');
    plugin.namedItem = markNative(function namedItem(name) {
      return mimeTypes.find(m => m.type === name) || null;
    }, 'namedItem');
    plugin[Symbol.iterator] = markNative(function values() { let i = 0; const next = markNative(function next() { return i < mimeTypes.length ? { value: mimeTypes[i++], done: false } : { done: true, value: undefined }; }, 'next'); const iter = { next: next }; iter[Symbol.iterator] = markNative(function() { return this; }, '[Symbol.iterator]'); return iter; }, 'values');
    return plugin;
  }

  const plugins = pluginData.map(createPlugin);

  // Build PluginArray
  const pluginArray = Object.create(win.PluginArray.prototype);
  Object.defineProperty(pluginArray, 'length', {
    get: markNative(function() { return plugins.length; }, 'get length'),
    enumerable: true, configurable: true,
  });
  for (let i = 0; i < plugins.length; i++) {
    Object.defineProperty(pluginArray, i, { value: plugins[i], enumerable: true, configurable: true });
  }
  pluginArray.item = markNative(function item(index) { return plugins[index] || null; }, 'item');
  pluginArray.namedItem = markNative(function namedItem(name) {
    return plugins.find(p => p.name === name) || null;
  }, 'namedItem');
  pluginArray.refresh = markNative(function refresh() {}, 'refresh');
  pluginArray[Symbol.iterator] = markNative(function values() { let i = 0; const next = markNative(function next() { return i < plugins.length ? { value: plugins[i++], done: false } : { done: true, value: undefined }; }, 'next'); const iter = { next: next }; iter[Symbol.iterator] = markNative(function() { return this; }, '[Symbol.iterator]'); return iter; }, 'values');

  Object.defineProperty(nav, 'plugins', {
    get: markNative(function() {
      if (this !== nav) throw new TypeError('Illegal invocation');
      return pluginArray;
    }, 'get plugins'),
    enumerable: true, configurable: true,
  });

  // MimeTypeArray
  const allMimeTypes = plugins.flatMap((p, i) => {
    const mt = [];
    for (let j = 0; j < p.length; j++) mt.push(p[j]);
    return mt;
  });
  // Deduplicate by type
  const uniqueMimes = [];
  const seen = new Set();
  for (const m of allMimeTypes) {
    if (!seen.has(m.type)) { seen.add(m.type); uniqueMimes.push(m); }
  }

  const mimeTypeArray = Object.create(win.MimeTypeArray.prototype);
  Object.defineProperty(mimeTypeArray, 'length', {
    get: markNative(function() { return uniqueMimes.length; }, 'get length'),
    enumerable: true, configurable: true,
  });
  for (let i = 0; i < uniqueMimes.length; i++) {
    Object.defineProperty(mimeTypeArray, i, { value: uniqueMimes[i], enumerable: true, configurable: true });
  }
  mimeTypeArray.item = markNative(function item(index) { return uniqueMimes[index] || null; }, 'item');
  mimeTypeArray.namedItem = markNative(function namedItem(name) {
    return uniqueMimes.find(m => m.type === name) || null;
  }, 'namedItem');
  mimeTypeArray[Symbol.iterator] = markNative(function values() { let i = 0; const next = markNative(function next() { return i < uniqueMimes.length ? { value: uniqueMimes[i++], done: false } : { done: true, value: undefined }; }, 'next'); const iter = { next: next }; iter[Symbol.iterator] = markNative(function() { return this; }, '[Symbol.iterator]'); return iter; }, 'values');

  Object.defineProperty(nav, 'mimeTypes', {
    get: markNative(function() {
      if (this !== nav) throw new TypeError('Illegal invocation');
      return mimeTypeArray;
    }, 'get mimeTypes'),
    enumerable: true, configurable: true,
  });
}

// ═══════════════════════════════════════════════════════════════════
// Canvas getContext — return a CanvasRenderingContext2D stub
// ═══════════════════════════════════════════════════════════════════
function installCanvasStub(ctx, markNative, fp) {
  const win = ctx;
  const HTMLCanvasElement = win.HTMLCanvasElement;
  if (!HTMLCanvasElement) return;

  // ─── CanvasGradient / CanvasPattern / TextMetrics constructors ───
  // These must exist BEFORE prototype methods reference them
  const CanvasGradient = markNative(function CanvasGradient() {
    throw new TypeError('Illegal constructor');
  }, 'CanvasGradient');
  CanvasGradient.prototype = Object.create(ctx.Object.prototype);
  Object.defineProperty(CanvasGradient.prototype, Symbol.toStringTag, { value: 'CanvasGradient', configurable: true });
  Object.defineProperty(CanvasGradient.prototype, 'constructor', { value: CanvasGradient, writable: true, configurable: true });
  Object.defineProperty(CanvasGradient.prototype, 'addColorStop', {
    value: markNative(function addColorStop() {}, 'addColorStop'),
    writable: true, enumerable: false, configurable: true,
  });
  win.CanvasGradient = CanvasGradient;

  const CanvasPattern = markNative(function CanvasPattern() {
    throw new TypeError('Illegal constructor');
  }, 'CanvasPattern');
  CanvasPattern.prototype = Object.create(ctx.Object.prototype);
  Object.defineProperty(CanvasPattern.prototype, Symbol.toStringTag, { value: 'CanvasPattern', configurable: true });
  Object.defineProperty(CanvasPattern.prototype, 'constructor', { value: CanvasPattern, writable: true, configurable: true });
  Object.defineProperty(CanvasPattern.prototype, 'setTransform', {
    value: markNative(function setTransform() {}, 'setTransform'),
    writable: true, enumerable: false, configurable: true,
  });
  win.CanvasPattern = CanvasPattern;

  const TextMetrics = markNative(function TextMetrics() {
    throw new TypeError('Illegal constructor');
  }, 'TextMetrics');
  TextMetrics.prototype = Object.create(ctx.Object.prototype);
  Object.defineProperty(TextMetrics.prototype, Symbol.toStringTag, { value: 'TextMetrics', configurable: true });
  Object.defineProperty(TextMetrics.prototype, 'constructor', { value: TextMetrics, writable: true, configurable: true });
  const tmFields = ['width','actualBoundingBoxAscent','actualBoundingBoxDescent','actualBoundingBoxLeft','actualBoundingBoxRight','fontBoundingBoxAscent','fontBoundingBoxDescent'];
  for (const f of tmFields) {
    Object.defineProperty(TextMetrics.prototype, f, { value: 0, writable: true, enumerable: true, configurable: true });
  }
  win.TextMetrics = TextMetrics;

  // ─── CanvasRenderingContext2D constructor ───
  const CanvasRenderingContext2D = markNative(function CanvasRenderingContext2D() {
    throw new TypeError('Illegal constructor');
  }, 'CanvasRenderingContext2D');
  CanvasRenderingContext2D.prototype = Object.create(ctx.Object.prototype);
  Object.defineProperty(CanvasRenderingContext2D.prototype, Symbol.toStringTag, { value: 'CanvasRenderingContext2D', configurable: true });
  Object.defineProperty(CanvasRenderingContext2D.prototype, 'constructor', { value: CanvasRenderingContext2D, writable: true, configurable: true });

  // All methods on PROTOTYPE (Chrome: ctx.hasOwnProperty('fillRect') === false)
  const ctx2dProto = CanvasRenderingContext2D.prototype;
  const ctx2dNoopMethods = [
    'arc', 'arcTo', 'beginPath', 'bezierCurveTo', 'clearRect', 'clip',
    'closePath', 'drawImage', 'ellipse', 'fill', 'fillRect',
    'fillText', 'lineTo', 'moveTo', 'putImageData', 'quadraticCurveTo', 'rect',
    'resetTransform', 'restore', 'rotate', 'save', 'scale', 'setLineDash',
    'setTransform', 'stroke', 'strokeRect', 'strokeText', 'transform',
    'translate',
  ];
  for (const m of ctx2dNoopMethods) {
    Object.defineProperty(ctx2dProto, m, {
      value: markNative(function() {}, m),
      writable: true, enumerable: false, configurable: true,
    });
  }
  // Special methods that return values
  Object.defineProperty(ctx2dProto, 'measureText', {
    value: markNative(function measureText() {
      const tm = Object.create(TextMetrics.prototype);
      tm.width = 10; tm.actualBoundingBoxAscent = 8; tm.actualBoundingBoxDescent = 2;
      tm.actualBoundingBoxLeft = 0; tm.actualBoundingBoxRight = 10;
      tm.fontBoundingBoxAscent = 10; tm.fontBoundingBoxDescent = 3;
      return tm;
    }, 'measureText'),
    writable: true, enumerable: false, configurable: true,
  });
  Object.defineProperty(ctx2dProto, 'getImageData', {
    value: markNative(function getImageData(sx, sy, sw, sh) {
      const w = sw || 1, h = sh || 1;
      return new ctx.ImageData(w, h);
    }, 'getImageData'),
    writable: true, enumerable: false, configurable: true,
  });
  Object.defineProperty(ctx2dProto, 'createImageData', {
    value: markNative(function createImageData(w, h) {
      return new ctx.ImageData(w, h || w);
    }, 'createImageData'),
    writable: true, enumerable: false, configurable: true,
  });
  Object.defineProperty(ctx2dProto, 'createLinearGradient', {
    value: markNative(function createLinearGradient() {
      return Object.create(CanvasGradient.prototype);
    }, 'createLinearGradient'),
    writable: true, enumerable: false, configurable: true,
  });
  Object.defineProperty(ctx2dProto, 'createRadialGradient', {
    value: markNative(function createRadialGradient() {
      return Object.create(CanvasGradient.prototype);
    }, 'createRadialGradient'),
    writable: true, enumerable: false, configurable: true,
  });
  Object.defineProperty(ctx2dProto, 'createPattern', {
    value: markNative(function createPattern() {
      return Object.create(CanvasPattern.prototype);
    }, 'createPattern'),
    writable: true, enumerable: false, configurable: true,
  });
  Object.defineProperty(ctx2dProto, 'isPointInPath', {
    value: markNative(function isPointInPath() { return false; }, 'isPointInPath'),
    writable: true, enumerable: false, configurable: true,
  });
  Object.defineProperty(ctx2dProto, 'getLineDash', {
    value: markNative(function getLineDash() { return []; }, 'getLineDash'),
    writable: true, enumerable: false, configurable: true,
  });

  win.CanvasRenderingContext2D = CanvasRenderingContext2D;

  // ─── WebGLRenderingContext constructor ───
  const WebGLRenderingContext = markNative(function WebGLRenderingContext() {
    throw new TypeError('Illegal constructor');
  }, 'WebGLRenderingContext');
  WebGLRenderingContext.prototype = Object.create(ctx.Object.prototype);
  Object.defineProperty(WebGLRenderingContext.prototype, Symbol.toStringTag, { value: 'WebGLRenderingContext', configurable: true });
  Object.defineProperty(WebGLRenderingContext.prototype, 'constructor', { value: WebGLRenderingContext, writable: true, configurable: true });

  // WebGL methods on PROTOTYPE
  const glProto = WebGLRenderingContext.prototype;
  const glNoopMethods = [
    'bindBuffer', 'bufferData', 'shaderSource', 'compileShader', 'attachShader',
    'linkProgram', 'useProgram', 'enableVertexAttribArray', 'vertexAttribPointer',
    'uniform1f', 'uniform2f', 'uniform3f', 'uniform4f',
    'drawArrays', 'drawElements', 'clear', 'clearColor', 'clearDepth',
    'enable', 'disable', 'viewport', 'deleteShader',
  ];
  for (const m of glNoopMethods) {
    Object.defineProperty(glProto, m, {
      value: markNative(function() {}, m),
      writable: true, enumerable: false, configurable: true,
    });
  }
  Object.defineProperty(glProto, 'getParameter', {
    value: markNative(function getParameter(pname) {
      const params = {
        7938: 'WebGL 1.0', 7936: 'WebKit', 7937: 'WebKit WebGL',
        37445: fp.webgl_vendor || 'Google Inc. (NVIDIA)',
        37446: fp.webgl_renderer || 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1060, OpenGL 4.5)',
        3379: 16384, 34076: 16384, 34921: 16, 36347: 1024, 36348: 512, 34930: 16,
      };
      return params[pname] !== undefined ? params[pname] : null;
    }, 'getParameter'),
    writable: true, enumerable: false, configurable: true,
  });
  Object.defineProperty(glProto, 'getExtension', {
    value: markNative(function getExtension(ext) {
      if (ext === 'WEBGL_debug_renderer_info') {
        return { UNMASKED_VENDOR_WEBGL: 37445, UNMASKED_RENDERER_WEBGL: 37446 };
      }
      return null;
    }, 'getExtension'),
    writable: true, enumerable: false, configurable: true,
  });
  Object.defineProperty(glProto, 'getSupportedExtensions', {
    value: markNative(function getSupportedExtensions() {
      return ['WEBGL_debug_renderer_info', 'WEBGL_lose_context', 'OES_texture_float'];
    }, 'getSupportedExtensions'),
    writable: true, enumerable: false, configurable: true,
  });
  Object.defineProperty(glProto, 'getShaderPrecisionFormat', {
    value: markNative(function getShaderPrecisionFormat() {
      return { rangeMin: 127, rangeMax: 127, precision: 23 };
    }, 'getShaderPrecisionFormat'),
    writable: true, enumerable: false, configurable: true,
  });
  Object.defineProperty(glProto, 'createBuffer', {
    value: markNative(function createBuffer() { return {}; }, 'createBuffer'),
    writable: true, enumerable: false, configurable: true,
  });
  Object.defineProperty(glProto, 'createProgram', {
    value: markNative(function createProgram() { return {}; }, 'createProgram'),
    writable: true, enumerable: false, configurable: true,
  });
  Object.defineProperty(glProto, 'createShader', {
    value: markNative(function createShader() { return {}; }, 'createShader'),
    writable: true, enumerable: false, configurable: true,
  });
  Object.defineProperty(glProto, 'getProgramParameter', {
    value: markNative(function getProgramParameter() { return true; }, 'getProgramParameter'),
    writable: true, enumerable: false, configurable: true,
  });
  Object.defineProperty(glProto, 'getShaderParameter', {
    value: markNative(function getShaderParameter() { return true; }, 'getShaderParameter'),
    writable: true, enumerable: false, configurable: true,
  });
  Object.defineProperty(glProto, 'getShaderInfoLog', {
    value: markNative(function getShaderInfoLog() { return ''; }, 'getShaderInfoLog'),
    writable: true, enumerable: false, configurable: true,
  });
  Object.defineProperty(glProto, 'getProgramInfoLog', {
    value: markNative(function getProgramInfoLog() { return ''; }, 'getProgramInfoLog'),
    writable: true, enumerable: false, configurable: true,
  });
  Object.defineProperty(glProto, 'getAttribLocation', {
    value: markNative(function getAttribLocation() { return 0; }, 'getAttribLocation'),
    writable: true, enumerable: false, configurable: true,
  });
  Object.defineProperty(glProto, 'getUniformLocation', {
    value: markNative(function getUniformLocation() { return {}; }, 'getUniformLocation'),
    writable: true, enumerable: false, configurable: true,
  });

  // WebGL constants — NON-WRITABLE on prototype (Chrome behavior)
  const GL_CONSTANTS = {
    VERTEX_SHADER: 35633, FRAGMENT_SHADER: 35632, ARRAY_BUFFER: 34962,
    ELEMENT_ARRAY_BUFFER: 34963, STATIC_DRAW: 35044, DYNAMIC_DRAW: 35048,
    FLOAT: 5126, UNSIGNED_BYTE: 5121, UNSIGNED_SHORT: 5123,
    TRIANGLES: 4, TRIANGLE_STRIP: 5, TRIANGLE_FAN: 6, LINES: 1, LINE_STRIP: 3,
    POINTS: 0, COLOR_BUFFER_BIT: 16384, DEPTH_BUFFER_BIT: 256, STENCIL_BUFFER_BIT: 1024,
    DEPTH_TEST: 2929, BLEND: 3042, CULL_FACE: 2884, SCISSOR_TEST: 3089,
    TEXTURE_2D: 3553, TEXTURE0: 33984, RGBA: 6408, RGB: 6407,
    NEAREST: 9728, LINEAR: 9729, TEXTURE_MAG_FILTER: 10240, TEXTURE_MIN_FILTER: 10241,
    TEXTURE_WRAP_S: 10242, TEXTURE_WRAP_T: 10243, CLAMP_TO_EDGE: 33071,
    FRAMEBUFFER: 36160, RENDERBUFFER: 36161, COLOR_ATTACHMENT0: 36064,
    COMPILE_STATUS: 35713, LINK_STATUS: 35714,
    MAX_TEXTURE_SIZE: 3379, MAX_CUBE_MAP_TEXTURE_SIZE: 34076,
    MAX_VERTEX_ATTRIBS: 34921, MAX_VERTEX_UNIFORM_VECTORS: 36347,
    MAX_FRAGMENT_UNIFORM_VECTORS: 36348, MAX_TEXTURE_IMAGE_UNITS: 34930,
    UNMASKED_VENDOR_WEBGL: 37445, UNMASKED_RENDERER_WEBGL: 37446,
    VERSION: 7938, VENDOR: 7936, RENDERER: 7937,
  };
  for (const [k, v] of Object.entries(GL_CONSTANTS)) {
    Object.defineProperty(glProto, k, {
      value: v, writable: false, enumerable: true, configurable: false,
    });
  }
  // Also on the constructor itself (Chrome exposes both)
  for (const [k, v] of Object.entries(GL_CONSTANTS)) {
    Object.defineProperty(WebGLRenderingContext, k, {
      value: v, writable: false, enumerable: true, configurable: false,
    });
  }

  win.WebGLRenderingContext = WebGLRenderingContext;

  // ─── Instance creation functions ───
  const context2dProps = {
    fillStyle: '#000000', strokeStyle: '#000000',
    font: '10px sans-serif', textAlign: 'start', textBaseline: 'alphabetic',
    direction: 'ltr', globalAlpha: 1, globalCompositeOperation: 'source-over',
    lineCap: 'butt', lineJoin: 'miter', lineWidth: 1, miterLimit: 10,
    shadowBlur: 0, shadowColor: 'rgba(0, 0, 0, 0)', shadowOffsetX: 0, shadowOffsetY: 0,
    imageSmoothingEnabled: true, imageSmoothingQuality: 'low',
  };

  function createContext2D(canvas) {
    const ctxObj = Object.create(CanvasRenderingContext2D.prototype);
    // Per-instance DATA properties only (methods are on prototype)
    for (const [key, val] of Object.entries(context2dProps)) {
      ctxObj[key] = val;
    }
    Object.defineProperty(ctxObj, 'canvas', { value: canvas, writable: false, enumerable: true, configurable: true });
    return ctxObj;
  }

  function createWebGLContext(canvas) {
    const gl = Object.create(WebGLRenderingContext.prototype);
    // Per-instance properties only
    Object.defineProperty(gl, 'canvas', { value: canvas, writable: false, enumerable: true, configurable: true });
    Object.defineProperty(gl, 'drawingBufferWidth', { get: markNative(function() { return canvas.width || 300; }, 'get drawingBufferWidth'), enumerable: true, configurable: true });
    Object.defineProperty(gl, 'drawingBufferHeight', { get: markNative(function() { return canvas.height || 150; }, 'get drawingBufferHeight'), enumerable: true, configurable: true });
    return gl;
  }

  // Override getContext on HTMLCanvasElement.prototype
  // Chrome returns the SAME context on repeated calls for the same type.
  // Once a type is obtained, incompatible types return null.
  const canvasContexts = new WeakMap();
  const origGetContext = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = markNative(function getContext(contextType) {
    let map = canvasContexts.get(this);
    if (!map) { map = {}; canvasContexts.set(this, map); }
    // Once a context type is chosen, others return null (Chrome behavior)
    if (map._type && map._type !== contextType) return null;
    if (map[contextType]) return map[contextType];
    let ctx2;
    if (contextType === '2d') ctx2 = createContext2D(this);
    else if (contextType === 'webgl' || contextType === 'experimental-webgl') ctx2 = createWebGLContext(this);
    else if (contextType === 'webgl2') ctx2 = createWebGLContext(this);
    else return null;
    map[contextType] = ctx2;
    map._type = contextType;
    return ctx2;
  }, 'getContext');

  // toDataURL / toBlob stubs
  if (fp.canvas_data_url || !HTMLCanvasElement.prototype.toDataURL || HTMLCanvasElement.prototype.toDataURL.toString().includes('Not implemented')) {
    HTMLCanvasElement.prototype.toDataURL = markNative(function toDataURL() {
      return fp.canvas_data_url || 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAASwAAAAyCAYAAACbRJVIAAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZcwAADsMAAA7DAcdvqGQAAAaSSURBVHhe7ZpNaBNBFMdn0lSbIEoPrUVLPZSgVg8e6kGkoKBYDz0UFBGkB8WDB0FQBEE8iCB4FEQQREG8iCAiCJ4EDwXPCh4Kiig9+FE/WrW1TZvs+GazTTabzWY3O9OdmvmXP7uZmc1m357M3HmSQAghhBBCCCGEEEII';
    }, 'toDataURL');
  }
  // toBlob must return non-empty Blob (size=0 is detectable)
  HTMLCanvasElement.prototype.toBlob = markNative(function toBlob(callback, type) {
    if (callback) {
      const dataUrl = this.toDataURL(type || 'image/png');
      const parts = dataUrl.split(',');
      const b64 = parts[1] || '';
      const binary = atob(b64);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      setTimeout(function() { callback(new Blob([bytes], { type: type || 'image/png' })); }, 0);
    }
  }, 'toBlob');
}

// ═══════════════════════════════════════════════════════════════════
// Constructor stubs — all the things VMP does `new X()` on
// ═══════════════════════════════════════════════════════════════════
function installConstructorStubs(ctx, markNative, fp) {
  const win = ctx;
  const ctxObjectProto = (ctx.Object || Object).prototype;
  fp = fp || {};

  // Helper: create an "Illegal constructor" that still functions as a type check
  function illegalCtor(name) {
    const ctor = markNative(function() {
      throw new TypeError('Illegal constructor');
    }, name);
    ctor.prototype = Object.create(ctxObjectProto);
    Object.defineProperty(ctor.prototype, Symbol.toStringTag, { value: name, configurable: true });
    Object.defineProperty(ctor.prototype, 'constructor', { value: ctor, writable: true, configurable: true });
    return ctor;
  }

  /**
   * Create a constructor with methods on PROTOTYPE (matches Chrome).
   * Chrome: `new MutationObserver(cb).hasOwnProperty('observe')` → false
   *         `'observe' in MutationObserver.prototype` → true
   *
   * @param {string} name - Constructor name
   * @param {object} protoMethods - { methodName: function } placed on prototype
   * @param {function|null} init - Instance initializer (for per-instance DATA only)
   * @param {number} length - Constructor.length
   */
  function workingCtor(name, protoMethods, init, length) {
    const ctor = markNative(function(...args) {
      if (!(this instanceof ctor) && !new.target) {
        throw new TypeError(`Failed to construct '${name}': Please use the 'new' operator`);
      }
      if (init) init.call(this, ...args);
    }, name, length !== undefined ? length : 0);
    ctor.prototype = Object.create(ctxObjectProto);
    Object.defineProperty(ctor.prototype, Symbol.toStringTag, { value: name, configurable: true });
    Object.defineProperty(ctor.prototype, 'constructor', { value: ctor, writable: true, configurable: true });
    // Methods on prototype — non-enumerable (matches Chrome)
    if (protoMethods) {
      for (const [mName, fn] of Object.entries(protoMethods)) {
        Object.defineProperty(ctor.prototype, mName, {
          value: markNative(fn, mName),
          writable: true, enumerable: false, configurable: true,
        });
      }
    }
    return ctor;
  }

  // Only install if jsdom doesn't already provide it
  function installIfMissing(name, ctor) {
    if (typeof win[name] === 'undefined' || win[name] === undefined) {
      win[name] = ctor;
    } else {
      // jsdom provides it — just markNative
      markNative(win[name], name);
    }
  }

  // ─── Observers (commonly constructed by VMP) ───
  installIfMissing('MutationObserver', workingCtor('MutationObserver', {
    observe: function observe() {},
    disconnect: function disconnect() {},
    takeRecords: function takeRecords() { return []; },
  }, null, 1));

  installIfMissing('IntersectionObserver', workingCtor('IntersectionObserver', {
    observe: function observe() {},
    unobserve: function unobserve() {},
    disconnect: function disconnect() {},
    takeRecords: function takeRecords() { return []; },
  }, function(cb, opts) {
    this.root = null;
    this.rootMargin = '0px 0px 0px 0px';
    this.thresholds = [0];
  }, 1));

  installIfMissing('ResizeObserver', workingCtor('ResizeObserver', {
    observe: function observe() {},
    unobserve: function unobserve() {},
    disconnect: function disconnect() {},
  }, null, 1));

  installIfMissing('PerformanceObserver', workingCtor('PerformanceObserver', {
    observe: function observe() {},
    disconnect: function disconnect() {},
    takeRecords: function takeRecords() { return []; },
  }, null, 1));

  // ─── WebSocket ───
  installIfMissing('WebSocket', workingCtor('WebSocket', {
    send: function send() {},
    close: function close() { this.readyState = 3; },
    addEventListener: function addEventListener() {},
    removeEventListener: function removeEventListener() {},
    dispatchEvent: function dispatchEvent() { return true; },
  }, function(url) {
    this.url = url;
    this.readyState = 0;
    this.bufferedAmount = 0;
    this.extensions = '';
    this.protocol = '';
    this.binaryType = 'blob';
    this.onopen = null;
    this.onclose = null;
    this.onerror = null;
    this.onmessage = null;
  }, 1));
  // WebSocket constants — NON-WRITABLE (Chrome behavior, same as WebGL constants)
  if (win.WebSocket) {
    const wsConstants = { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 };
    for (const [name, value] of Object.entries(wsConstants)) {
      try {
        Object.defineProperty(win.WebSocket, name, {
          value, writable: false, enumerable: true, configurable: false,
        });
        Object.defineProperty(win.WebSocket.prototype, name, {
          value, writable: false, enumerable: true, configurable: false,
        });
      } catch (e) {}
    }
  }

  // ─── Worker ───
  installIfMissing('Worker', workingCtor('Worker', {
    postMessage: function postMessage() {},
    terminate: function terminate() {},
    addEventListener: function addEventListener() {},
    removeEventListener: function removeEventListener() {},
    dispatchEvent: function dispatchEvent() { return true; },
  }, function(url) {
    this.onmessage = null;
    this.onerror = null;
  }, 1));

  // ─── SharedWorker ───
  installIfMissing('SharedWorker', workingCtor('SharedWorker', {
    // SharedWorker itself has no methods; .port has them
  }, function(url) {
    this.port = Object.create(ctxObjectProto);
    this.port.onmessage = null;
    this.port.postMessage = markNative(function postMessage() {}, 'postMessage');
    this.port.start = markNative(function start() {}, 'start');
    this.port.close = markNative(function close() {}, 'close');
    this.port.addEventListener = markNative(function addEventListener() {}, 'addEventListener');
    this.port.removeEventListener = markNative(function removeEventListener() {}, 'removeEventListener');
  }, 1));

  // ─── MessageChannel / BroadcastChannel ───
  installIfMissing('MessageChannel', workingCtor('MessageChannel', null, function() {
    function makePort() {
      const p = Object.create(ctxObjectProto);
      p.onmessage = null;
      p.postMessage = markNative(function postMessage() {}, 'postMessage');
      p.start = markNative(function start() {}, 'start');
      p.close = markNative(function close() {}, 'close');
      p.addEventListener = markNative(function addEventListener() {}, 'addEventListener');
      p.removeEventListener = markNative(function removeEventListener() {}, 'removeEventListener');
      return p;
    }
    this.port1 = makePort();
    this.port2 = makePort();
  }, 0));

  installIfMissing('BroadcastChannel', workingCtor('BroadcastChannel', {
    postMessage: function postMessage() {},
    close: function close() {},
    addEventListener: function addEventListener() {},
    removeEventListener: function removeEventListener() {},
    dispatchEvent: function dispatchEvent() { return true; },
  }, function(name) {
    this.name = name;
    this.onmessage = null;
    this.onmessageerror = null;
  }, 1));

  // ─── Image constructor ───
  installIfMissing('Image', markNative(function Image(width, height) {
    // In jsdom, Image should be HTMLImageElement — if jsdom has it, use it
    if (win.document && win.document.createElement) {
      const img = win.document.createElement('img');
      if (width !== undefined) img.width = width;
      if (height !== undefined) img.height = height;
      return img;
    }
    this.src = '';
    this.width = width || 0;
    this.height = height || 0;
    this.onload = null;
    this.onerror = null;
  }, 'Image', 0));

  // ─── OffscreenCanvas ───
  installIfMissing('OffscreenCanvas', workingCtor('OffscreenCanvas', {
    getContext: function getContext(type) {
      if (type === '2d') return { fillRect() {}, clearRect() {}, getImageData() { return new ctx.ImageData(1, 1); } };
      return null;
    },
    convertToBlob: function convertToBlob() { return Promise.resolve(new win.Blob([])); },
    transferToImageBitmap: function transferToImageBitmap() { return {}; },
  }, function(width, height) {
    this.width = width || 0;
    this.height = height || 0;
  }, 2));

  // ─── AbortController / AbortSignal ───
  if (!win.AbortController) {
    const AbortSignal = illegalCtor('AbortSignal');
    win.AbortSignal = AbortSignal;

    installIfMissing('AbortController', workingCtor('AbortController', null, function() {
      this.signal = Object.create(ctx.Object.prototype);
      this.signal.aborted = false;
      this.signal.reason = undefined;
      this.signal.onabort = null;
      this.signal.addEventListener = markNative(function addEventListener() {}, 'addEventListener');
      this.signal.removeEventListener = markNative(function removeEventListener() {}, 'removeEventListener');
      Object.defineProperty(this.signal, Symbol.toStringTag, { value: 'AbortSignal', configurable: true });
      this.abort = markNative(function abort() { this.signal.aborted = true; }.bind(this), 'abort');
    }));
  }

  // ─── CSS ───
  if (!win.CSS) {
    win.CSS = Object.create(ctx.Object.prototype);
    // CSS.supports: return true for well-known supported properties/features
    const SUPPORTED_CSS = new Set([
      'display:block', 'display:flex', 'display:grid', 'display:inline',
      'color:red', 'color:blue', 'color:inherit', 'position:relative',
      'position:absolute', 'position:fixed', 'position:sticky',
      'transform:translate(0)', 'opacity:0.5', 'transition:all',
      'animation:none', 'flex-direction:row', 'grid-template-columns:1fr',
      'gap:1px', 'overflow:hidden', 'z-index:1', 'pointer-events:none',
      'user-select:none', 'backdrop-filter:blur(1px)',
    ]);
    win.CSS.supports = markNative(function supports(prop, value) {
      if (arguments.length === 1) {
        // Conditional form: CSS.supports("(display: flex)")
        return true; // Chrome supports most modern CSS
      }
      // Two-argument form: CSS.supports("display", "flex")
      const key = (prop + ':' + value).toLowerCase().replace(/\s/g, '');
      if (SUPPORTED_CSS.has(key)) return true;
      // Default: return true for standard properties (Chrome supports most CSS)
      if (typeof prop === 'string' && /^[a-z-]+$/.test(prop)) return true;
      return false;
    }, 'supports');
    win.CSS.escape = markNative(function escape(str) {
      // Proper CSS.escape: escape special chars per CSSOM spec
      if (!str) return '';
      let result = '';
      for (let i = 0; i < str.length; i++) {
        const ch = str.charCodeAt(i);
        if (ch === 0) { result += '\uFFFD'; continue; }
        if ((ch >= 1 && ch <= 31) || ch === 127 ||
            (i === 0 && ch >= 48 && ch <= 57) ||
            (i === 1 && ch >= 48 && ch <= 57 && str.charCodeAt(0) === 45)) {
          result += '\\' + ch.toString(16) + ' ';
        } else if (i === 0 && ch === 45 && str.length === 1) {
          result += '\\' + str[i];
        } else if (ch >= 128 || ch === 45 || ch === 95 ||
                   (ch >= 48 && ch <= 57) || (ch >= 65 && ch <= 90) || (ch >= 97 && ch <= 122)) {
          result += str[i];
        } else {
          result += '\\' + str[i];
        }
      }
      return result;
    }, 'escape');
    Object.defineProperty(win.CSS, Symbol.toStringTag, { value: 'CSS', configurable: true });
  }

  // ─── PointerEvent / InputEvent ───
  installIfMissing('PointerEvent', workingCtor('PointerEvent', null, function(type, opts) {
    this.type = type;
    this.pointerId = (opts && opts.pointerId) || 0;
    this.pointerType = (opts && opts.pointerType) || '';
    this.bubbles = (opts && opts.bubbles) || false;
    this.cancelable = (opts && opts.cancelable) || false;
  }, 1));

  installIfMissing('InputEvent', workingCtor('InputEvent', null, function(type, opts) {
    this.type = type;
    this.inputType = (opts && opts.inputType) || '';
    this.data = (opts && opts.data) || null;
    this.bubbles = (opts && opts.bubbles) || false;
  }, 1));

  // ─── Notification ───
  installIfMissing('Notification', workingCtor('Notification', {
    close: function close() {},
  }, function(title, opts) {
    this.title = title;
    this.body = (opts && opts.body) || '';
    this.icon = (opts && opts.icon) || '';
  }, 1));
  if (win.Notification) {
    win.Notification.permission = 'default';
    win.Notification.requestPermission = markNative(function requestPermission() {
      return Promise.resolve('default');
    }, 'requestPermission');
  }

  // ─── RTCPeerConnection ───
  installIfMissing('RTCPeerConnection', workingCtor('RTCPeerConnection', {
    createDataChannel: function createDataChannel() { return {}; },
    createOffer: function createOffer() { return Promise.resolve({}); },
    createAnswer: function createAnswer() { return Promise.resolve({}); },
    setLocalDescription: function setLocalDescription() { return Promise.resolve(); },
    setRemoteDescription: function setRemoteDescription() { return Promise.resolve(); },
    close: function close() {},
    addEventListener: function addEventListener() {},
    removeEventListener: function removeEventListener() {},
    dispatchEvent: function dispatchEvent() { return true; },
  }, function() {
    this.iceConnectionState = 'new';
    this.connectionState = 'new';
    this.signalingState = 'stable';
  }, 0));
  installIfMissing('webkitRTCPeerConnection', win.RTCPeerConnection);

  // ─── SpeechSynthesis ───
  if (!win.speechSynthesis) {
    // Create SpeechSynthesis constructor (Illegal constructor — Chrome exposes it)
    const SpeechSynthesis = markNative(function SpeechSynthesis() {
      throw new TypeError('Illegal constructor');
    }, 'SpeechSynthesis');
    SpeechSynthesis.prototype = Object.create(ctx.EventTarget ? ctx.EventTarget.prototype : ctx.Object.prototype);
    Object.defineProperty(SpeechSynthesis.prototype, Symbol.toStringTag, { value: 'SpeechSynthesis', configurable: true });
    Object.defineProperty(SpeechSynthesis.prototype, 'constructor', { value: SpeechSynthesis, writable: true, configurable: true });

    // Methods on prototype (Chrome: speechSynthesis.hasOwnProperty('speak') === false)
    // Derive voice list from fingerprint platform/languages
    const primaryLang = (fp.languages && fp.languages[0]) || 'en-US';
    const voicesByLang = {
      'zh-CN': [
        { default: true, lang: 'zh-CN', localService: true, name: 'Microsoft Huihui - Chinese (Simplified)', voiceURI: 'Microsoft Huihui - Chinese (Simplified)' },
        { default: false, lang: 'en-US', localService: true, name: 'Microsoft David - English (United States)', voiceURI: 'Microsoft David - English (United States)' },
      ],
      'en-US': [
        { default: true, lang: 'en-US', localService: true, name: 'Microsoft David - English (United States)', voiceURI: 'Microsoft David - English (United States)' },
        { default: false, lang: 'en-US', localService: true, name: 'Microsoft Zira - English (United States)', voiceURI: 'Microsoft Zira - English (United States)' },
      ],
      'en-GB': [
        { default: true, lang: 'en-GB', localService: true, name: 'Microsoft Hazel - English (Great Britain)', voiceURI: 'Microsoft Hazel - English (Great Britain)' },
        { default: false, lang: 'en-US', localService: true, name: 'Microsoft David - English (United States)', voiceURI: 'Microsoft David - English (United States)' },
      ],
    };
    const voices = voicesByLang[primaryLang] || voicesByLang['en-US'];

    Object.defineProperty(SpeechSynthesis.prototype, 'speak', {
      value: markNative(function speak() {}, 'speak'),
      writable: true, enumerable: false, configurable: true,
    });
    Object.defineProperty(SpeechSynthesis.prototype, 'cancel', {
      value: markNative(function cancel() {}, 'cancel'),
      writable: true, enumerable: false, configurable: true,
    });
    Object.defineProperty(SpeechSynthesis.prototype, 'pause', {
      value: markNative(function pause() {}, 'pause'),
      writable: true, enumerable: false, configurable: true,
    });
    Object.defineProperty(SpeechSynthesis.prototype, 'resume', {
      value: markNative(function resume() {}, 'resume'),
      writable: true, enumerable: false, configurable: true,
    });
    Object.defineProperty(SpeechSynthesis.prototype, 'getVoices', {
      value: markNative(function getVoices() { return voices; }, 'getVoices'),
      writable: true, enumerable: false, configurable: true,
    });

    // Create instance with prototype chain
    const speechSynthesis = Object.create(SpeechSynthesis.prototype);
    // Per-instance data properties only
    speechSynthesis.speaking = false;
    speechSynthesis.pending = false;
    speechSynthesis.paused = false;

    win.speechSynthesis = speechSynthesis;
    win.SpeechSynthesis = SpeechSynthesis;
  }
  installIfMissing('SpeechSynthesisUtterance', workingCtor('SpeechSynthesisUtterance', null, function(text) {
    this.text = text || '';
    this.lang = '';
    this.voice = null;
    this.volume = 1;
    this.rate = 1;
    this.pitch = 1;
  }, 0));

  // ─── matchMedia improvement ───
  {
    const origMatchMedia = win.matchMedia;
    // Chrome-true media queries on desktop
    const DESKTOP_TRUE_QUERIES = new Set([
      '(any-pointer:fine)', '(any-pointer)', '(any-hover:hover)', '(any-hover)',
      '(color-gamut:srgb)', '(color-gamut:p3)', '(color-gamut)',
      '(pointer:fine)', '(pointer)', '(hover:hover)', '(hover)',
      '(color)', '(min-color:1)',
    ]);
    win.matchMedia = markNative(function matchMedia(query) {
      if (origMatchMedia) {
        try {
          const result = origMatchMedia.call(win, query);
          if (result) return result;
        } catch (e) {}
      }
      // Fallback: return a MediaQueryList-like object with correct matches
      const normalizedQuery = (query || '').replace(/\s/g, '');
      return {
        matches: DESKTOP_TRUE_QUERIES.has(normalizedQuery),
        media: (query || '').replace(/:\s*/g, ': '),
        onchange: null,
        addEventListener: markNative(function addEventListener() {}, 'addEventListener'),
        removeEventListener: markNative(function removeEventListener() {}, 'removeEventListener'),
        addListener: markNative(function addListener() {}, 'addListener'),
        removeListener: markNative(function removeListener() {}, 'removeListener'),
      };
    }, 'matchMedia');
  }

  // ─── getComputedStyle enhancement ───
  // jsdom only returns ~15 CSS properties. Chrome returns 340+.
  // Wrap to provide all standard properties with sensible defaults.
  {
    const origGetComputedStyle = win.getComputedStyle;
    // Chrome 120+ CSS properties (subset that covers common fingerprinting checks)
    const CSS_DEFAULTS = {
      'display': 'block', 'position': 'static', 'top': 'auto', 'right': 'auto',
      'bottom': 'auto', 'left': 'auto', 'float': 'none', 'clear': 'none',
      'width': 'auto', 'height': 'auto', 'min-width': '0px', 'min-height': '0px',
      'max-width': 'none', 'max-height': 'none', 'margin-top': '0px',
      'margin-right': '0px', 'margin-bottom': '0px', 'margin-left': '0px',
      'padding-top': '0px', 'padding-right': '0px', 'padding-bottom': '0px',
      'padding-left': '0px', 'border-top-width': '0px', 'border-right-width': '0px',
      'border-bottom-width': '0px', 'border-left-width': '0px',
      'border-top-style': 'none', 'border-right-style': 'none',
      'border-bottom-style': 'none', 'border-left-style': 'none',
      'border-top-color': 'rgb(0, 0, 0)', 'border-right-color': 'rgb(0, 0, 0)',
      'border-bottom-color': 'rgb(0, 0, 0)', 'border-left-color': 'rgb(0, 0, 0)',
      'overflow': 'visible', 'overflow-x': 'visible', 'overflow-y': 'visible',
      'visibility': 'visible', 'opacity': '1', 'z-index': 'auto',
      'font-family': 'Times', 'font-size': '16px', 'font-weight': '400',
      'font-style': 'normal', 'line-height': 'normal', 'text-align': 'start',
      'text-decoration': 'none solid rgb(0, 0, 0)', 'text-transform': 'none',
      'white-space': 'normal', 'word-spacing': '0px', 'letter-spacing': 'normal',
      'color': 'rgb(0, 0, 0)', 'background-color': 'rgba(0, 0, 0, 0)',
      'background-image': 'none', 'background-repeat': 'repeat',
      'background-position': '0% 0%', 'background-size': 'auto',
      'cursor': 'auto', 'box-sizing': 'content-box',
      'transform': 'none', 'transition': 'all 0s ease 0s', 'animation': 'none 0s ease 0s 1 normal none running',
      'flex-direction': 'row', 'flex-wrap': 'nowrap', 'justify-content': 'normal',
      'align-items': 'normal', 'align-content': 'normal', 'order': '0',
      'flex-grow': '0', 'flex-shrink': '1', 'flex-basis': 'auto',
      'grid-template-columns': 'none', 'grid-template-rows': 'none',
      'gap': 'normal', 'outline': 'rgb(0, 0, 0) none 0px',
      'outline-width': '0px', 'outline-style': 'none', 'outline-color': 'rgb(0, 0, 0)',
      'list-style-type': 'disc', 'table-layout': 'auto', 'border-collapse': 'separate',
      'border-spacing': '0px 0px', 'vertical-align': 'baseline',
      'text-indent': '0px', 'word-break': 'normal', 'overflow-wrap': 'normal',
      'clip': 'auto', 'contain': 'none', 'will-change': 'auto',
      'pointer-events': 'auto', 'user-select': 'auto', 'resize': 'none',
      'appearance': 'none', 'filter': 'none', 'backdrop-filter': 'none',
      'mix-blend-mode': 'normal', 'isolation': 'auto', 'object-fit': 'fill',
      'object-position': '50% 50%', 'image-rendering': 'auto',
      'column-count': 'auto', 'column-width': 'auto', 'column-gap': 'normal',
      'orphans': '2', 'widows': '2', 'page-break-before': 'auto',
      'page-break-after': 'auto', 'page-break-inside': 'auto',
      'break-before': 'auto', 'break-after': 'auto', 'break-inside': 'auto',
      '-webkit-font-smoothing': 'auto', '-webkit-text-size-adjust': 'auto',
      'accent-color': 'auto', 'aspect-ratio': 'auto', 'content-visibility': 'visible',
    };
    const CSS_KEYS = Object.keys(CSS_DEFAULTS);

    // Cache: Chrome returns the same CSSStyleDeclaration per element
    const computedStyleCache = new WeakMap();

    win.getComputedStyle = markNative(function getComputedStyle(el, pseudo) {
      if (!pseudo && computedStyleCache.has(el)) return computedStyleCache.get(el);
      const orig = origGetComputedStyle ? origGetComputedStyle.call(win, el, pseudo) : null;
      // Build merged style: jsdom values override defaults
      const style = Object.create(ctx.Object.prototype);
      const merged = { ...CSS_DEFAULTS };
      if (orig) {
        for (let i = 0; i < orig.length; i++) {
          const prop = orig[i];
          const val = orig.getPropertyValue(prop);
          if (val) merged[prop] = val;
        }
      }
      const allKeys = Object.keys(merged);
      // Indexed access + length
      Object.defineProperty(style, 'length', { value: allKeys.length, writable: false, enumerable: true, configurable: true });
      for (let i = 0; i < allKeys.length; i++) {
        Object.defineProperty(style, i, { value: allKeys[i], enumerable: true, configurable: true });
      }
      // getPropertyValue / item
      style.getPropertyValue = markNative(function getPropertyValue(prop) {
        return merged[prop] || '';
      }, 'getPropertyValue');
      style.item = markNative(function item(index) {
        return allKeys[index] || '';
      }, 'item');
      style.getPropertyPriority = markNative(function getPropertyPriority() { return ''; }, 'getPropertyPriority');
      // camelCase access (e.g., style.display, style.fontSize)
      for (const [prop, val] of Object.entries(merged)) {
        const camel = prop.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
        if (!(camel in style)) style[camel] = val;
        if (!(prop in style)) style[prop] = val;
      }
      Object.defineProperty(style, Symbol.toStringTag, { value: 'CSSStyleDeclaration', configurable: true });
      // Symbol.iterator for for...of
      style[Symbol.iterator] = markNative(function values() {
        let i = 0;
        const next = markNative(function next() { return i < allKeys.length ? { value: allKeys[i++], done: false } : { done: true, value: undefined }; }, 'next');
        const iter = { next: next };
        iter[Symbol.iterator] = markNative(function() { return this; }, '[Symbol.iterator]');
        return iter;
      }, 'values');
      if (!pseudo) computedStyleCache.set(el, style);
      return style;
    }, 'getComputedStyle');
  }

  // ─── Element layout dimensions ───
  // jsdom has no layout engine — offsetWidth/Height return 0.
  // Real browsers return computed dimensions. Stub with style-based inference.
  {
    const HTMLElement = win.HTMLElement;
    if (HTMLElement && HTMLElement.prototype) {
      const proto = HTMLElement.prototype;

      // Parse numeric px value from style string (e.g., "100px" → 100)
      function parsePx(val) {
        if (!val) return 0;
        const n = parseFloat(val);
        return isNaN(n) ? 0 : n;
      }

      // Compute approximate element dimensions from inline/computed style
      function getElementDims(el) {
        const style = el.style || {};
        let w = parsePx(style.width);
        let h = parsePx(style.height);
        // Default body/html dimensions based on viewport
        const tag = (el.tagName || '').toLowerCase();
        if (tag === 'body' || tag === 'html') {
          w = w || (win.innerWidth || 1920);
          h = h || (win.innerHeight || 1080);
        }
        return { w, h };
      }

      // offsetWidth/Height — include padding+border (approximate with just width/height)
      const owDesc = Object.getOwnPropertyDescriptor(proto, 'offsetWidth');
      Object.defineProperty(proto, 'offsetWidth', {
        get: markNative(function() { return getElementDims(this).w; }, 'get offsetWidth'),
        enumerable: true, configurable: true,
      });
      Object.defineProperty(proto, 'offsetHeight', {
        get: markNative(function() { return getElementDims(this).h; }, 'get offsetHeight'),
        enumerable: true, configurable: true,
      });

      Object.defineProperty(proto, 'clientWidth', {
        get: markNative(function() { return getElementDims(this).w; }, 'get clientWidth'),
        enumerable: true, configurable: true,
      });
      Object.defineProperty(proto, 'clientHeight', {
        get: markNative(function() { return getElementDims(this).h; }, 'get clientHeight'),
        enumerable: true, configurable: true,
      });

      Object.defineProperty(proto, 'scrollWidth', {
        get: markNative(function() { return getElementDims(this).w; }, 'get scrollWidth'),
        enumerable: true, configurable: true,
      });
      Object.defineProperty(proto, 'scrollHeight', {
        get: markNative(function() { return getElementDims(this).h; }, 'get scrollHeight'),
        enumerable: true, configurable: true,
      });

      // getBoundingClientRect — return DOMRect instance (instanceof DOMRect must be true)
      const origGetBCR = proto.getBoundingClientRect;
      proto.getBoundingClientRect = markNative(function getBoundingClientRect() {
        const dims = getElementDims(this);
        const x = this.offsetLeft || 0;
        const y = this.offsetTop || 0;
        // Use DOMRect constructor if available (jsdom provides it), else fallback
        if (ctx.DOMRect) {
          try { return new ctx.DOMRect(x, y, dims.w, dims.h); } catch (e) {}
        }
        return { x, y, width: dims.w, height: dims.h, top: y, left: x, right: x + dims.w, bottom: y + dims.h };
      }, 'getBoundingClientRect');
    }
  }


  // ─── performance.getEntriesByType and related ───
  const perf = win.performance;
  if (perf) {
    if (!perf.getEntriesByType) {
      perf.getEntriesByType = markNative(function getEntriesByType() { return []; }, 'getEntriesByType');
    }
    if (!perf.getEntriesByName) {
      perf.getEntriesByName = markNative(function getEntriesByName() { return []; }, 'getEntriesByName');
    }
    if (!perf.getEntries) {
      perf.getEntries = markNative(function getEntries() { return []; }, 'getEntries');
    }
    if (!perf.mark) {
      perf.mark = markNative(function mark() {}, 'mark');
    }
    if (!perf.measure) {
      perf.measure = markNative(function measure() {}, 'measure');
    }
    if (!perf.clearMarks) {
      perf.clearMarks = markNative(function clearMarks() {}, 'clearMarks');
    }
    if (!perf.clearMeasures) {
      perf.clearMeasures = markNative(function clearMeasures() {}, 'clearMeasures');
    }
    markNative(perf.now, 'now');

    // Quantize performance.now() to 100μs (Chrome cross-origin isolation default)
    const origPerfNow = perf.now.bind(perf);
    perf.now = markNative(function now() {
      return Math.round(origPerfNow() * 10) / 10;
    }, 'now');

    // performance.memory — Chrome-only, non-standard but commonly fingerprinted
    // Chrome returns the SAME MemoryInfo object (same reference), values drift on read
    if (!perf.memory) {
      let usedBase = 20000000 + Math.floor(Math.random() * 15000000);
      let totalBase = usedBase + 5000000 + Math.floor(Math.random() * 10000000);
      const memoryObj = Object.create(ctx.Object.prototype);
      Object.defineProperty(memoryObj, Symbol.toStringTag, { value: 'MemoryInfo', configurable: true });
      Object.defineProperties(memoryObj, {
        jsHeapSizeLimit: { get: markNative(function() { return 4294705152; }, 'get jsHeapSizeLimit'), enumerable: true, configurable: true },
        totalJSHeapSize: { get: markNative(function() { totalBase += Math.floor(Math.random() * 10000); return totalBase; }, 'get totalJSHeapSize'), enumerable: true, configurable: true },
        usedJSHeapSize: { get: markNative(function() { usedBase += Math.floor(Math.random() * 50000) - 20000; return usedBase; }, 'get usedJSHeapSize'), enumerable: true, configurable: true },
      });
      Object.defineProperty(perf, 'memory', {
        get: markNative(function() { return memoryObj; }, 'get memory'),
        enumerable: true, configurable: true,
      });
    }

    // performance.eventCounts — Chrome EventCounts (Map-like)
    if (!perf.eventCounts) {
      const eventCounts = Object.create(ctx.Object.prototype);
      Object.defineProperty(eventCounts, Symbol.toStringTag, { value: 'EventCounts', configurable: true });
      eventCounts.size = 0;
      eventCounts.entries = markNative(function entries() { return [][Symbol.iterator](); }, 'entries');
      eventCounts.keys = markNative(function keys() { return [][Symbol.iterator](); }, 'keys');
      eventCounts.values = markNative(function values() { return [][Symbol.iterator](); }, 'values');
      eventCounts.get = markNative(function get() { return 0; }, 'get');
      eventCounts.has = markNative(function has() { return false; }, 'has');
      eventCounts.forEach = markNative(function forEach() {}, 'forEach');
      eventCounts[Symbol.iterator] = markNative(function() { return [][Symbol.iterator](); }, '[Symbol.iterator]');
      Object.defineProperty(perf, 'eventCounts', {
        get: markNative(function() { return eventCounts; }, 'get eventCounts'),
        enumerable: true, configurable: true,
      });
    }
  }

  // ─── IndexedDB stub ───
  if (!win.indexedDB) {
    const idbFactory = Object.create(ctx.Object.prototype);
    idbFactory.open = markNative(function open(name, version) {
      const request = Object.create(ctx.Object.prototype);
      request.result = null;
      request.error = null;
      request.readyState = 'pending';
      request.onsuccess = null;
      request.onerror = null;
      request.onupgradeneeded = null;
      setTimeout(() => { if (request.onsuccess) request.onsuccess({ target: request }); }, 0);
      return request;
    }, 'open');
    idbFactory.deleteDatabase = markNative(function deleteDatabase() {
      return { onsuccess: null, onerror: null };
    }, 'deleteDatabase');
    Object.defineProperty(idbFactory, Symbol.toStringTag, { value: 'IDBFactory', configurable: true });
    Object.defineProperty(win, 'indexedDB', {
      get: markNative(function() { return idbFactory; }, 'get indexedDB'),
      enumerable: true, configurable: true,
    });
  }

  // ─── navigator.getBattery() — common fingerprint API ───
  if (ctx.navigator && !ctx.navigator.getBattery) {
    ctx.navigator.getBattery = markNative(function getBattery() {
      return Promise.resolve({
        charging: true,
        chargingTime: 0,
        dischargingTime: Infinity,
        level: 1,
        onchargingchange: null,
        onchargingtimechange: null,
        ondischargingtimechange: null,
        onlevelchange: null,
        addEventListener: markNative(function addEventListener() {}, 'addEventListener'),
        removeEventListener: markNative(function removeEventListener() {}, 'removeEventListener'),
      });
    }, 'getBattery');
  }

  // ─── URL.createObjectURL / revokeObjectURL ───
  if (win.URL) {
    if (!win.URL.createObjectURL) {
      win.URL.createObjectURL = markNative(function createObjectURL() {
        // Chrome uses UUID v4 format: xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx
        const hex = () => Math.floor(Math.random() * 16).toString(16);
        const s = (n) => { let r = ''; for (let i = 0; i < n; i++) r += hex(); return r; };
        const uuid = s(8) + '-' + s(4) + '-4' + s(3) + '-' + (8 + Math.floor(Math.random() * 4)).toString(16) + s(3) + '-' + s(12);
        return `blob:${win.location.origin}/${uuid}`;
      }, 'createObjectURL');
    }
    if (!win.URL.revokeObjectURL) {
      win.URL.revokeObjectURL = markNative(function revokeObjectURL() {}, 'revokeObjectURL');
    }
  }

  // ─── Chrome-specific constants and webkit APIs ───
  if (win.TEMPORARY === undefined) win.TEMPORARY = 0;
  if (win.PERSISTENT === undefined) win.PERSISTENT = 1;

  if (!win.webkitRequestFileSystem) {
    win.webkitRequestFileSystem = markNative(function webkitRequestFileSystem(type, size, successCallback, errorCallback) {
      if (typeof successCallback === 'function') {
        setTimeout(() => successCallback({ root: { fullPath: '/', isDirectory: true, isFile: false } }), 0);
      }
    }, 'webkitRequestFileSystem', 3);
  }
  if (!win.webkitResolveLocalFileSystemURL) {
    win.webkitResolveLocalFileSystemURL = markNative(function webkitResolveLocalFileSystemURL() {}, 'webkitResolveLocalFileSystemURL', 2);
  }

  // ─── DeviceMotionEvent / DeviceOrientationEvent ───
  installIfMissing('DeviceMotionEvent', workingCtor('DeviceMotionEvent', null, function(type, opts) {
    this.type = type || 'devicemotion';
    this.acceleration = null;
    this.accelerationIncludingGravity = null;
    this.rotationRate = null;
    this.interval = 0;
  }, 1));
  installIfMissing('DeviceOrientationEvent', workingCtor('DeviceOrientationEvent', null, function(type, opts) {
    this.type = type || 'deviceorientation';
    this.alpha = null;
    this.beta = null;
    this.gamma = null;
    this.absolute = false;
  }, 1));

  // ─── MediaStreamTrack ───
  installIfMissing('MediaStreamTrack', illegalCtor('MediaStreamTrack'));

  // ─── Debugger neutralization ───
  // Old approach: strip `debugger` at source level before eval.
  // Do NOT proxy Function or replace eval — those change prototype chains
  // (eval.constructor !== Function) which is a trivial detection vector.
  // Debugger stripping is handled in engine.js at the source level instead.
}

// ═══════════════════════════════════════════════════════════════════
// Navigator sub-objects (mediaDevices, credentials, storage, etc.)
// ═══════════════════════════════════════════════════════════════════
function installNavigatorApis(ctx, markNative, fp) {
  const nav = ctx.navigator;
  if (!nav) return;
  fp = fp || {};

  // navigator.mediaDevices
  if (!nav.mediaDevices) {
    const mediaDevices = Object.create(ctx.Object.prototype);
    mediaDevices.enumerateDevices = markNative(function enumerateDevices() { return Promise.resolve([]); }, 'enumerateDevices');
    mediaDevices.getUserMedia = markNative(function getUserMedia() { return Promise.reject(new Error('NotAllowedError')); }, 'getUserMedia');
    mediaDevices.getSupportedConstraints = markNative(function getSupportedConstraints() { return {}; }, 'getSupportedConstraints');
    Object.defineProperty(mediaDevices, Symbol.toStringTag, { value: 'MediaDevices', configurable: true });
    Object.defineProperty(nav, 'mediaDevices', {
      get: markNative(function() { return mediaDevices; }, 'get mediaDevices'),
      enumerable: true, configurable: true,
    });
  }

  // navigator.serviceWorker
  if (!nav.serviceWorker) {
    const sw = Object.create(ctx.Object.prototype);
    sw.controller = null;
    sw.ready = Promise.resolve({ active: null });
    sw.register = markNative(function register() { return Promise.resolve({}); }, 'register');
    sw.getRegistrations = markNative(function getRegistrations() { return Promise.resolve([]); }, 'getRegistrations');
    sw.addEventListener = markNative(function addEventListener() {}, 'addEventListener');
    sw.removeEventListener = markNative(function removeEventListener() {}, 'removeEventListener');
    Object.defineProperty(sw, Symbol.toStringTag, { value: 'ServiceWorkerContainer', configurable: true });
    Object.defineProperty(nav, 'serviceWorker', {
      get: markNative(function() { return sw; }, 'get serviceWorker'),
      enumerable: true, configurable: true,
    });
  }

  // navigator.bluetooth
  if (!nav.bluetooth) {
    const bt = Object.create(ctx.Object.prototype);
    bt.getAvailability = markNative(function getAvailability() { return Promise.resolve(false); }, 'getAvailability');
    bt.requestDevice = markNative(function requestDevice() { return Promise.reject(new Error('NotFoundError')); }, 'requestDevice');
    Object.defineProperty(bt, Symbol.toStringTag, { value: 'Bluetooth', configurable: true });
    Object.defineProperty(nav, 'bluetooth', {
      get: markNative(function() { return bt; }, 'get bluetooth'),
      enumerable: true, configurable: true,
    });
  }

  // navigator.credentials
  if (!nav.credentials) {
    const cred = Object.create(ctx.Object.prototype);
    cred.get = markNative(function get() { return Promise.resolve(null); }, 'get');
    cred.store = markNative(function store() { return Promise.resolve(); }, 'store');
    cred.create = markNative(function create() { return Promise.resolve(null); }, 'create');
    cred.preventSilentAccess = markNative(function preventSilentAccess() { return Promise.resolve(); }, 'preventSilentAccess');
    Object.defineProperty(cred, Symbol.toStringTag, { value: 'CredentialsContainer', configurable: true });
    Object.defineProperty(nav, 'credentials', {
      get: markNative(function() { return cred; }, 'get credentials'),
      enumerable: true, configurable: true,
    });
  }

  // navigator.storage
  if (!nav.storage) {
    const storage = Object.create(ctx.Object.prototype);
    storage.estimate = markNative(function estimate() { return Promise.resolve({ quota: 1073741824, usage: 0 }); }, 'estimate');
    storage.persist = markNative(function persist() { return Promise.resolve(false); }, 'persist');
    storage.persisted = markNative(function persisted() { return Promise.resolve(false); }, 'persisted');
    Object.defineProperty(storage, Symbol.toStringTag, { value: 'StorageManager', configurable: true });
    Object.defineProperty(nav, 'storage', {
      get: markNative(function() { return storage; }, 'get storage'),
      enumerable: true, configurable: true,
    });
  }

  // navigator.permissions
  if (!nav.permissions) {
    const permissions = Object.create(ctx.Object.prototype);
    permissions.query = markNative(function query(desc) {
      return Promise.resolve({ state: 'prompt', name: desc && desc.name, onchange: null });
    }, 'query');
    Object.defineProperty(permissions, Symbol.toStringTag, { value: 'Permissions', configurable: true });
    Object.defineProperty(nav, 'permissions', {
      get: markNative(function() { return permissions; }, 'get permissions'),
      enumerable: true, configurable: true,
    });
  }

  // navigator.clipboard — Chrome provides Clipboard instance
  if (!nav.clipboard) {
    const clipboard = Object.create(ctx.EventTarget ? ctx.EventTarget.prototype : ctx.Object.prototype);
    Object.defineProperty(clipboard, Symbol.toStringTag, { value: 'Clipboard', configurable: true });
    clipboard.read = markNative(function read() { return Promise.resolve([]); }, 'read');
    clipboard.readText = markNative(function readText() { return Promise.resolve(''); }, 'readText');
    clipboard.write = markNative(function write() { return Promise.resolve(); }, 'write');
    clipboard.writeText = markNative(function writeText() { return Promise.resolve(); }, 'writeText');
    Object.defineProperty(nav, 'clipboard', {
      get: markNative(function() { return clipboard; }, 'get clipboard'),
      enumerable: true, configurable: true,
    });
  }

  // navigator.keyboard — Chrome Keyboard API
  if (!nav.keyboard) {
    const keyboard = Object.create(ctx.Object.prototype);
    Object.defineProperty(keyboard, Symbol.toStringTag, { value: 'Keyboard', configurable: true });
    keyboard.getLayoutMap = markNative(function getLayoutMap() {
      const layoutMap = Object.create(ctx.Object.prototype);
      Object.defineProperty(layoutMap, Symbol.toStringTag, { value: 'KeyboardLayoutMap', configurable: true });
      layoutMap.size = 0;
      layoutMap.entries = markNative(function entries() { return [][Symbol.iterator](); }, 'entries');
      layoutMap.keys = markNative(function keys() { return [][Symbol.iterator](); }, 'keys');
      layoutMap.values = markNative(function values() { return [][Symbol.iterator](); }, 'values');
      layoutMap.get = markNative(function get() { return undefined; }, 'get');
      layoutMap.has = markNative(function has() { return false; }, 'has');
      layoutMap.forEach = markNative(function forEach() {}, 'forEach');
      return Promise.resolve(layoutMap);
    }, 'getLayoutMap');
    keyboard.lock = markNative(function lock() { return Promise.resolve(); }, 'lock');
    keyboard.unlock = markNative(function unlock() {}, 'unlock');
    Object.defineProperty(nav, 'keyboard', {
      get: markNative(function() { return keyboard; }, 'get keyboard'),
      enumerable: true, configurable: true,
    });
  }

  // navigator.userActivation — Chrome UserActivation API
  if (!nav.userActivation) {
    const userActivation = Object.create(ctx.Object.prototype);
    Object.defineProperty(userActivation, Symbol.toStringTag, { value: 'UserActivation', configurable: true });
    Object.defineProperty(userActivation, 'hasBeenActive', {
      get: markNative(function() { return true; }, 'get hasBeenActive'),
      enumerable: true, configurable: true,
    });
    Object.defineProperty(userActivation, 'isActive', {
      get: markNative(function() { return false; }, 'get isActive'),
      enumerable: true, configurable: true,
    });
    Object.defineProperty(nav, 'userActivation', {
      get: markNative(function() { return userActivation; }, 'get userActivation'),
      enumerable: true, configurable: true,
    });
  }

  // navigator.userAgentData — derive version from fingerprint UA string
  if (!nav.userAgentData) {
    // Extract Chrome version from UA string (e.g. "Chrome/146.0.0.0" → "146")
    let chromeVersion = '120';
    const uaStr = fp.user_agent || '';
    const vMatch = uaStr.match(/Chrome\/(\d+)/);
    if (vMatch) chromeVersion = vMatch[1];

    const CtxObject = ctx.Object || Object;
    const CtxArray = ctx.Array || Array;
    const uaData = Object.create(CtxObject.prototype);
    // Brand objects must live in vm context realm (instanceof Object must be true)
    function ctxBrand(brand, version) {
      const obj = CtxObject.create(CtxObject.prototype);
      obj.brand = brand;
      obj.version = version;
      return CtxObject.freeze(obj);
    }
    const brandsArr = new CtxArray(3);
    brandsArr[0] = ctxBrand('Not_A Brand', '8');
    brandsArr[1] = ctxBrand('Chromium', chromeVersion);
    brandsArr[2] = ctxBrand('Google Chrome', chromeVersion);
    uaData.brands = CtxObject.freeze(brandsArr);
    uaData.mobile = false;
    uaData.platform = fp.platform === 'MacIntel' ? 'macOS' : 'Windows';
    uaData.getHighEntropyValues = markNative(function getHighEntropyValues() {
      return Promise.resolve({
        brands: uaData.brands,
        mobile: false,
        platform: uaData.platform,
        platformVersion: '15.0.0',
        architecture: 'x86',
        model: '',
        uaFullVersion: chromeVersion + '.0.0.0',
      });
    }, 'getHighEntropyValues');
    uaData.toJSON = markNative(function toJSON() {
      return { brands: uaData.brands, mobile: uaData.mobile, platform: uaData.platform };
    }, 'toJSON');
    Object.defineProperty(uaData, Symbol.toStringTag, { value: 'NavigatorUAData', configurable: true });
    Object.defineProperty(nav, 'userAgentData', {
      get: markNative(function() { return uaData; }, 'get userAgentData'),
      enumerable: true, configurable: true,
    });
  }
}

// ═══════════════════════════════════════════════════════════════════
// document.currentScript — VMP checks this to find its own script URL
// ═══════════════════════════════════════════════════════════════════
function installCurrentScript(ctx, markNative) {
  const doc = ctx.document;
  if (!doc) return;

  // Create a script element to act as currentScript
  const scriptEl = doc.createElement('script');
  scriptEl.type = 'text/javascript';
  // Mark in WeakSet (invisible to target, no expando property)
  _injectedScripts.add(scriptEl);

  // Append to head so getElementsByTagName('script') finds it
  const head = doc.querySelector('head') || doc.documentElement;
  if (head) head.appendChild(scriptEl);

  // Make it currentScript (getter-only, no setter — matches Chrome)
  Object.defineProperty(doc, 'currentScript', {
    get: markNative(function() { return scriptEl; }, 'get currentScript'),
    enumerable: true,
    configurable: true,
  });

  // Store ref outside vm context (invisible to target script)
  _scriptElementRefs.set(ctx, scriptEl);
}

function getScriptElement(ctx) {
  return _scriptElementRefs.get(ctx) || null;
}

module.exports = { installBrowserApis, getScriptElement, _injectedScripts };
