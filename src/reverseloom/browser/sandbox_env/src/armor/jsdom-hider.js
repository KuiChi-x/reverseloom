'use strict';

const { _injectedScripts } = require('./browser-apis');

/**
 * jsdom-hider.js — Hide jsdom detection signatures.
 *
 * Techniques learned from sdenv (Object.js + window.js):
 *
 * 1. Object.getOwnPropertySymbols(window) → []
 * 2. Object.getOwnPropertyNames(window) — filter _ prefixed jsdom internals
 * 3. Object.keys(window) — filter _ prefixed jsdom internals
 * 4. getComputedStyle return value filtering
 * 5. Delete jsdom-only globals (XPathException, etc.)
 * 6. Bulk markNative on window/document/navigator functions
 * 7. Hide window._ properties from 'in' operator and direct access
 */

// jsdom internal properties that leak on window (must hide)
const JSDOM_WINDOW_INTERNALS = new Set([
  '_top', '_parent', '_length', '_globalObject', '_globalProxy',
  '_registeredHandlers', '_eventHandlers', '_resourceLoader',
  '_document', '_origin', '_sessionHistory', '_virtualConsole',
  '_runScripts', '_frameElement', '_pretendToBeVisual',
  '_storageQuota', '_commonForOrigin', '_currentOriginData',
  '_localStorage', '_sessionStorage', '_selection',
  '_customElementRegistry', 'loadTextSync',
]);

// jsdom-only globals that Chrome doesn't have
const JSDOM_ONLY_GLOBALS = [
  'XPathException',
  'loadTextSync',
];

function installJsdomHider(ctx, markNativeHandle) {
  const { markNative } = markNativeHandle;
  const win = ctx;

  // --- 0. Delete jsdom-only globals ---
  for (const name of JSDOM_ONLY_GLOBALS) {
    try { delete ctx[name]; } catch (e) {}
  }

  // --- 0a. Hide underscore-prefixed props from detection.
  // Cannot DELETE them (jsdom uses them internally), but we can:
  // 1. Make them non-enumerable (hides from for..in)
  // 2. The getOwnPropertyNames/getOwnPropertyDescriptor filters handle the rest
  // 3. Direct access `window._globalObject` remains a risk but deleting breaks jsdom.
  //    Tradeoff: jsdom functionality > hiding an obscure detection vector that
  //    requires knowing the exact property name to check.
  const allOwnProps = Object.getOwnPropertyNames(ctx);
  for (let i = 0; i < allOwnProps.length; i++) {
    const name = allOwnProps[i];
    if (name[0] !== '_') continue;
    try {
      const desc = Object.getOwnPropertyDescriptor(ctx, name);
      if (desc && desc.configurable && desc.enumerable) {
        Object.defineProperty(ctx, name, { ...desc, enumerable: false });
      }
    } catch (e) {}
  }

  // --- 0b. Fix window prototype chain (sdenv technique) ---
  // In Chrome: Object.getPrototypeOf(window) === Window.prototype → true
  // In jsdom vm context this may not hold. Fix it.
  if (ctx.Window && ctx.Window.prototype) {
    try { Object.setPrototypeOf(ctx, ctx.Window.prototype); } catch (e) {}
  }

  // --- 0b. Object.prototype.toString — guard against [object global] leak ---
  // In Node vm context, Object.prototype.toString.call(window) may return
  // "[object global]" instead of "[object Window]". Patch it.
  const origObjToString = Object.prototype.toString;
  const patchedObjToString = function toString() {
    const result = origObjToString.call(this);
    if (result === '[object global]') return '[object Window]';
    return result;
  };
  try {
    Object.defineProperty(ctx.Object.prototype, 'toString', {
      value: patchedObjToString,
      writable: true, configurable: true,
    });
    markNative(patchedObjToString, 'toString');
  } catch (e) {}

  // --- 1. Object.getOwnPropertySymbols filter ---
  const origGetOwnPropertySymbols = Object.getOwnPropertySymbols;
  const filteredGetOwnPropertySymbols = function getOwnPropertySymbols(obj) {
    if (obj && typeof obj === 'object') {
      try {
        if (obj === obj.window) return [];
      } catch (e) {}
      // Also filter hidden marker symbols from navigator/screen
      // (used for Illegal invocation checks, Proxy-transparent)
      try {
        if (obj === obj.window.navigator || obj === obj.window.screen) {
          return origGetOwnPropertySymbols(obj).filter(s => !String(s).startsWith('Symbol(__'));
        }
      } catch (e) {}
    }
    return origGetOwnPropertySymbols(obj);
  };
  Object.defineProperty(ctx.Object, 'getOwnPropertySymbols', {
    value: filteredGetOwnPropertySymbols,
    writable: true, enumerable: false, configurable: true,
  });
  markNative(filteredGetOwnPropertySymbols, 'getOwnPropertySymbols');

  // --- 2. Object.getOwnPropertyNames filter ---
  // Hide _ prefixed jsdom internals from the result
  const origGetOwnPropertyNames = Object.getOwnPropertyNames;
  const filteredGetOwnPropertyNames = function getOwnPropertyNames(obj) {
    let names;
    try { names = origGetOwnPropertyNames(obj); } catch (e) { return []; }
    if (obj && typeof obj === 'object') {
      try {
        if (obj === obj.window) {
          return names.filter(n => !n.startsWith('_') && !JSDOM_WINDOW_INTERNALS.has(n));
        }
      } catch (e) {}
    }
    return names;
  };
  Object.defineProperty(ctx.Object, 'getOwnPropertyNames', {
    value: filteredGetOwnPropertyNames,
    writable: true, enumerable: false, configurable: true,
  });
  markNative(filteredGetOwnPropertyNames, 'getOwnPropertyNames');

  // --- 3. Object.keys filter ---
  const origKeys = Object.keys;
  const filteredKeys = function keys(obj) {
    let k;
    try { k = origKeys(obj); } catch (e) { return []; }
    if (obj && typeof obj === 'object') {
      try {
        if (obj === obj.window) {
          return k.filter(n => !n.startsWith('_') && !JSDOM_WINDOW_INTERNALS.has(n));
        }
      } catch (e) {}
    }
    return k;
  };
  Object.defineProperty(ctx.Object, 'keys', {
    value: filteredKeys,
    writable: true, enumerable: false, configurable: true,
  });
  markNative(filteredKeys, 'keys');

  // --- 3b. Reflect.ownKeys filter ---
  // Must be consistent with getOwnPropertySymbols (returns []) and getOwnPropertyNames (filters _)
  const origReflectOwnKeys = Reflect.ownKeys;
  const filteredReflectOwnKeys = function ownKeys(obj) {
    const keys = origReflectOwnKeys(obj);
    if (obj && typeof obj === 'object') {
      try {
        if (obj === obj.window) {
          // Filter both: symbols (match getOwnPropertySymbols returning []) and _ strings
          return keys.filter(function(k) {
            if (typeof k === 'symbol') return false;
            return !k.startsWith('_') && !JSDOM_WINDOW_INTERNALS.has(k);
          });
        }
      } catch (e) {}
    }
    return keys;
  };
  Object.defineProperty(ctx.Reflect, 'ownKeys', {
    value: filteredReflectOwnKeys,
    writable: true, enumerable: false, configurable: true,
  });
  markNative(filteredReflectOwnKeys, 'ownKeys');

  // --- 3c. Object.getOwnPropertyDescriptor filter ---
  // Return undefined for underscore props on window/document/navigator (matches Chrome)
  const origGOPD = Object.getOwnPropertyDescriptor;
  const isWindowLike = (obj) => {
    try { return obj === obj.window; } catch (e) { return false; }
  };
  const filteredGOPD = function getOwnPropertyDescriptor(obj, prop) {
    if (typeof prop === 'string' && prop.startsWith('_') && obj && typeof obj === 'object' && isWindowLike(obj)) {
      return undefined;
    }
    return origGOPD(obj, prop);
  };
  Object.defineProperty(ctx.Object, 'getOwnPropertyDescriptor', {
    value: filteredGOPD,
    writable: true, enumerable: false, configurable: true,
  });
  markNative(filteredGOPD, 'getOwnPropertyDescriptor');

  // --- 3c2. Reflect.getOwnPropertyDescriptor filter ---
  // Must be consistent with Object.getOwnPropertyDescriptor
  if (ctx.Reflect) {
    const origReflectGOPD = Reflect.getOwnPropertyDescriptor;
    const filteredReflectGOPD = function getOwnPropertyDescriptor(obj, prop) {
      if (typeof prop === 'string' && prop.startsWith('_') && obj && typeof obj === 'object' && isWindowLike(obj)) {
        return undefined;
      }
      return origReflectGOPD(obj, prop);
    };
    Object.defineProperty(ctx.Reflect, 'getOwnPropertyDescriptor', {
      value: filteredReflectGOPD,
      writable: true, enumerable: false, configurable: true,
    });
    markNative(filteredReflectGOPD, 'getOwnPropertyDescriptor');
  }

  // --- 3d. Object.getOwnPropertyDescriptors filter ---
  const origGOPDs = Object.getOwnPropertyDescriptors;
  if (origGOPDs) {
    const filteredGOPDs = function getOwnPropertyDescriptors(obj) {
      const descs = origGOPDs(obj);
      if (obj && typeof obj === 'object' && isWindowLike(obj)) {
        // Build clean result: no underscore string keys, no symbol keys
        // (consistent with getOwnPropertyNames filtering _ and getOwnPropertySymbols returning [])
        const filtered = Object.create(null);
        const stringKeys = Object.getOwnPropertyNames(descs);
        for (let i = 0; i < stringKeys.length; i++) {
          const k = stringKeys[i];
          if (!k.startsWith('_') && !JSDOM_WINDOW_INTERNALS.has(k)) {
            filtered[k] = descs[k];
          }
        }
        // Symbol keys are intentionally NOT copied (matches getOwnPropertySymbols returning [])
        return filtered;
      }
      return descs;
    };
    Object.defineProperty(ctx.Object, 'getOwnPropertyDescriptors', {
      value: filteredGOPDs,
      writable: true, enumerable: false, configurable: true,
    });
    markNative(filteredGOPDs, 'getOwnPropertyDescriptors');
  }

  // --- 4. getComputedStyle filter ---
  // Only filter _ props from ownKeys enumeration (hide from Object.keys/getOwnPropertyNames)
  // Do NOT intercept get/has — that breaks cssstyle's internal _values access
  if (typeof win.getComputedStyle === 'function') {
    const origGetCS = win.getComputedStyle;
    const filteredGetCS = function getComputedStyle(el, pseudo) {
      const style = origGetCS.call(win, el, pseudo);
      if (!style) return style;
      return new Proxy(style, {
        ownKeys(target) {
          return Reflect.ownKeys(target).filter(k =>
            typeof k !== 'string' || !k.startsWith('_')
          );
        },
        getOwnPropertyDescriptor(target, prop) {
          if (typeof prop === 'string' && prop.startsWith('_')) {
            // Hide from Object.getOwnPropertyDescriptor enumeration
            // But still return it if it's non-configurable (Proxy invariant)
            const desc = Object.getOwnPropertyDescriptor(target, prop);
            if (desc && !desc.configurable) return desc;
            return undefined;
          }
          return Reflect.getOwnPropertyDescriptor(target, prop);
        },
      });
    };
    win.getComputedStyle = filteredGetCS;
    markNative(filteredGetCS, 'getComputedStyle', 1);
  }

  // --- 5. Window identity properties ---
  // Chrome has these, jsdom might not
  if (!('clientInformation' in win) || win.clientInformation !== win.navigator) {
    Object.defineProperty(win, 'clientInformation', {
      get: markNative(function() {
        if (this !== win) throw new TypeError('Illegal invocation');
        return win.navigator;
      }, 'get clientInformation'),
      enumerable: true, configurable: true,
    });
  }
  try {
    Object.defineProperty(win, 'closed', { value: false, writable: true, enumerable: true, configurable: true });
    Object.defineProperty(win, 'opener', { value: null, writable: true, enumerable: true, configurable: true });
    Object.defineProperty(win, 'isSecureContext', {
      get: markNative(function() {
        if (this !== win) throw new TypeError('Illegal invocation');
        try { return win.location.protocol === 'https:'; } catch (e) { return true; }
      }, 'get isSecureContext'),
      enumerable: true, configurable: true,
    });
  } catch (e) { /* some may be non-configurable */ }

  // --- 5a. Ensure window identity: window === window.self === window.top === window.parent === window.frames ---
  // jsdom's vm context may have these pointing to different objects (sdenv technique)
  const WIN_SELF_KEYS = ['self', 'frames', 'top', 'parent'];
  for (const key of WIN_SELF_KEYS) {
    try {
      Object.defineProperty(win, key, {
        get: markNative(function() {
          if (this !== win) throw new TypeError('Illegal invocation');
          return win;
        }, `get ${key}`),
        enumerable: true, configurable: true,
      });
    } catch (e) {}
  }

  // --- 6. Batch markNative on window functions (with correct .length) ---
  const windowFuncs = [
    ['alert', 0], ['confirm', 0], ['prompt', 0], ['open', 0],
    ['close', 0], ['print', 0], ['stop', 0],
    ['focus', 0], ['blur', 0], ['postMessage', 1],
    ['requestAnimationFrame', 1], ['cancelAnimationFrame', 1],
    ['requestIdleCallback', 1], ['cancelIdleCallback', 1],
    ['getComputedStyle', 1], ['getSelection', 0],
    ['matchMedia', 1], ['scroll', 0], ['scrollTo', 0], ['scrollBy', 0],
    ['moveTo', 2], ['moveBy', 2], ['resizeTo', 2], ['resizeBy', 2],
    ['atob', 1], ['btoa', 1], ['fetch', 1], ['find', 0],
    ['setTimeout', 1], ['setInterval', 1], ['clearTimeout', 0], ['clearInterval', 0],
    ['queueMicrotask', 1], ['structuredClone', 1], ['reportError', 1],
    ['addEventListener', 2], ['removeEventListener', 2], ['dispatchEvent', 1],
    ['createImageBitmap', 1], ['captureEvents', 0], ['releaseEvents', 0],
  ];
  for (const [name, len] of windowFuncs) {
    if (typeof win[name] === 'function') {
      markNative(win[name], name, len);
    }
  }

  // --- 7. Mark document methods ---
  const doc = win.document;
  if (doc) {
    const docFuncs = [
      ['createElement', 1], ['createElementNS', 2], ['createDocumentFragment', 0],
      ['createTextNode', 1], ['createComment', 1], ['createEvent', 1],
      ['createExpression', 1], ['createNSResolver', 1], ['createRange', 0],
      ['getElementById', 1], ['getElementsByTagName', 1], ['getElementsByClassName', 1],
      ['getElementsByName', 1], ['querySelector', 1], ['querySelectorAll', 1],
      ['addEventListener', 2], ['removeEventListener', 2], ['dispatchEvent', 1],
      ['hasFocus', 0], ['write', 0], ['writeln', 0], ['open', 0], ['close', 0],
      ['elementFromPoint', 2], ['elementsFromPoint', 2],
      ['evaluate', 5], ['adoptNode', 1], ['importNode', 1],
    ];
    for (const [name, len] of docFuncs) {
      if (typeof doc[name] === 'function') {
        markNative(doc[name], name, len);
      }
    }

    // --- 7a. Hide _sdDoc from document.createExpression results ---
    if (typeof doc.createExpression === 'function') {
      const origCreateExpr = doc.createExpression.bind(doc);
      const patchedCreateExpr = function createExpression(expression, resolver) {
        const expr = origCreateExpr(expression, resolver);
        if (!expr) return expr;
        return new Proxy(expr, {
          has(target, prop) {
            if (prop === '_sdDoc') return false;
            return prop in target;
          },
          get(target, prop, receiver) {
            if (prop === '_sdDoc') return undefined;
            const val = Reflect.get(target, prop, receiver);
            // Fix evaluate: coerce type to Number (Chrome behavior)
            if (prop === 'evaluate' && typeof val === 'function') {
              const evalFn = function evaluate(contextNode, type, result) {
                return val.call(target, contextNode, Number(type), result);
              };
              return markNative(evalFn, 'evaluate', 3);
            }
            return val;
          },
        });
      };
      doc.createExpression = patchedCreateExpr;
      markNative(patchedCreateExpr, 'createExpression', 1);
    }

    // --- 7b. Hide injected script elements from getElementsByTagName('script') ---
    const origGetByTagName = doc.getElementsByTagName.bind(doc);
    const patchedGetByTagName = function getElementsByTagName(tagName) {
      const result = origGetByTagName(tagName);
      if (tagName.toLowerCase() === 'script') {
        // Use Proxy over the original HTMLCollection to preserve instanceof/toString
        // but skip our injected element
        return new Proxy(result, {
          get(target, prop, receiver) {
            // Build filtered index mapping on demand
            const filtered = [];
            for (let i = 0; i < target.length; i++) {
              if (!_injectedScripts.has(target[i])) filtered.push(target[i]);
            }
            if (prop === 'length') return filtered.length;
            if (typeof prop === 'string' && /^\d+$/.test(prop)) {
              return filtered[Number(prop)] || undefined;
            }
            if (prop === 'item') return markNative(function item(i) { return filtered[i] || null; }, 'item', 1);
            if (prop === 'namedItem') return markNative(function namedItem(n) {
              return filtered.find(el => el.id === n || el.getAttribute('name') === n) || null;
            }, 'namedItem', 1);
            if (prop === Symbol.iterator) {
              return markNative(function values() { let idx = 0; const next = markNative(function next() { return idx < filtered.length ? { value: filtered[idx++], done: false } : { done: true, value: undefined }; }, 'next'); const iter = { next: next }; iter[Symbol.iterator] = markNative(function() { return this; }, '[Symbol.iterator]'); return iter; }, 'values');
            }
            return Reflect.get(target, prop, receiver);
          },
        });
      }
      return result;
    };
    doc.getElementsByTagName = patchedGetByTagName;
    markNative(patchedGetByTagName, 'getElementsByTagName', 1);
  }

  // --- 8. Mark navigator getters ---
  const nav = win.navigator;
  if (nav) {
    const navProto = Object.getPrototypeOf(nav);
    if (navProto) {
      const descs = Object.getOwnPropertyDescriptors(navProto);
      for (const [key, desc] of Object.entries(descs)) {
        if (desc.get) markNative(desc.get, `get ${key}`);
        if (desc.set) markNative(desc.set, `set ${key}`);
      }
    }
  }

  // --- 9. Mark console methods ---
  if (win.console) {
    const consoleMethods = [
      ['log', 0], ['warn', 0], ['error', 0], ['info', 0], ['debug', 0],
      ['trace', 0], ['dir', 0], ['dirxml', 0], ['table', 0],
      ['count', 0], ['countReset', 0],
      ['group', 0], ['groupCollapsed', 0], ['groupEnd', 0],
      ['time', 0], ['timeEnd', 0], ['timeLog', 0], ['timeStamp', 0],
      ['assert', 0], ['clear', 0], ['profile', 0], ['profileEnd', 0],
      ['context', 1], ['createTask', 0],
    ];
    for (const [name, len] of consoleMethods) {
      if (typeof win.console[name] === 'function') {
        markNative(win.console[name], name, len);
      }
    }
  }

  // --- 10. CSSStyleDeclaration — must throw "Illegal constructor" on new ---
  if (win.CSSStyleDeclaration && typeof win.CSSStyleDeclaration === 'function') {
    const OrigCSS = win.CSSStyleDeclaration;
    const PatchedCSS = function CSSStyleDeclaration() {
      throw new TypeError('Illegal constructor');
    };
    PatchedCSS.prototype = OrigCSS.prototype;
    Object.defineProperty(PatchedCSS.prototype, 'constructor', { value: PatchedCSS, writable: true, configurable: true });
    win.CSSStyleDeclaration = PatchedCSS;
    markNative(PatchedCSS, 'CSSStyleDeclaration', 0);
  }

  // --- 11. document.hidden / visibilityState ---
  // Anti-bots check these; jsdom may not expose them correctly
  const doc2 = win.document;
  if (doc2) {
    if (doc2.hidden === undefined || doc2.hidden !== false) {
      Object.defineProperty(doc2, 'hidden', {
        get: markNative(function() { return false; }, 'get hidden'),
        enumerable: true, configurable: true,
      });
    }
    if (!doc2.visibilityState || doc2.visibilityState !== 'visible') {
      Object.defineProperty(doc2, 'visibilityState', {
        get: markNative(function() { return 'visible'; }, 'get visibilityState'),
        enumerable: true, configurable: true,
      });
    }
  }

  // --- 12. performance.timing + performance.navigation ---
  // Deprecated but widely checked by anti-bot scripts (Akamai, PerimeterX, Cloudflare)
  const perf = win.performance;
  if (perf) {
    if (!perf.timing) {
      const navStart = Math.floor(perf.timeOrigin || Date.now() - 500);
      // Jitter function: adds 0~max ms of randomness to timing values
      const j = (max) => Math.floor(Math.random() * max);
      const dns = 2 + j(8);           // 2-10ms DNS
      const tcp = 10 + j(20);         // 10-30ms TCP
      const ssl = 5 + j(10);          // 5-15ms SSL (within TCP)
      const ttfb = 30 + j(80);        // 30-110ms TTFB
      const download = 8 + j(20);     // 8-28ms download
      const domParse = 80 + j(200);   // 80-280ms DOM parsing
      const domComplete = 150 + j(300); // 150-450ms DOM complete

      const fetchStart = navStart + 1 + j(2);
      const domainLookupStart = fetchStart + j(2);
      const domainLookupEnd = domainLookupStart + dns;
      const connectStart = domainLookupEnd;
      const connectEnd = connectStart + tcp;
      const secureConnectionStart = connectStart + ssl;
      const requestStart = connectEnd + j(2);
      const responseStart = requestStart + ttfb;
      const responseEnd = responseStart + download;
      const domLoading = responseEnd + 1 + j(3);
      const domInteractive = domLoading + domParse;
      const domContentLoadedStart = domInteractive + j(3);
      const domContentLoadedEnd = domContentLoadedStart + 1 + j(3);
      const domCompleteTs = domContentLoadedEnd + domComplete;
      const loadEventStart = domCompleteTs + j(2);
      const loadEventEnd = loadEventStart + 1 + j(3);

      // Create timing in the vm context realm so `instanceof Object` works
      const CtxObject = ctx.Object || Object;
      const timingObj = CtxObject.create(CtxObject.prototype);
      CtxObject.assign(timingObj, {
        navigationStart: navStart,
        unloadEventStart: 0, unloadEventEnd: 0,
        redirectStart: 0, redirectEnd: 0,
        fetchStart,
        domainLookupStart, domainLookupEnd,
        connectStart, connectEnd,
        secureConnectionStart,
        requestStart,
        responseStart, responseEnd,
        domLoading, domInteractive,
        domContentLoadedEventStart: domContentLoadedStart, domContentLoadedEventEnd: domContentLoadedEnd,
        domComplete: domCompleteTs,
        loadEventStart, loadEventEnd,
      });
      timingObj.toJSON = markNative(function toJSON() { return timingObj; }, 'toJSON');
      Object.defineProperty(timingObj, Symbol.toStringTag, { value: 'PerformanceTiming', configurable: true });
      Object.defineProperty(perf, 'timing', {
        get: markNative(function() { return timingObj; }, 'get timing'),
        enumerable: true, configurable: true,
      });
    }
    if (!perf.navigation) {
      const CtxObject = ctx.Object || Object;
      const navObj = CtxObject.create(CtxObject.prototype);
      navObj.type = 0;
      navObj.redirectCount = 0;
      navObj.toJSON = markNative(function toJSON() { return navObj; }, 'toJSON');
      Object.defineProperty(navObj, Symbol.toStringTag, { value: 'PerformanceNavigation', configurable: true });
      Object.defineProperty(perf, 'navigation', {
        get: markNative(function() { return navObj; }, 'get navigation'),
        enumerable: true, configurable: true,
      });
    }
  }

  // --- 13. console[Symbol.toStringTag] = 'console' ---
  // Chrome: Object.prototype.toString.call(console) === '[object console]'
  if (win.console) {
    try {
      Object.defineProperty(win.console, Symbol.toStringTag, {
        value: 'console', configurable: true,
      });
    } catch (e) {}
  }

  // --- 14. history.length ---
  // In a real browser, history.length >= 1 (1 for a fresh tab).
  if (win.history) {
    try {
      const origLength = win.history.length;
      if (origLength === 0 || origLength === undefined) {
        Object.defineProperty(win.history, 'length', {
          get: markNative(function() { return 1; }, 'get length'),
          enumerable: true, configurable: true,
        });
      }
    } catch (e) {}
  }

  // --- 15. window.crossOriginIsolated ---
  // Chrome always has this property (false unless COOP/COEP headers present)
  if (win.crossOriginIsolated === undefined) {
    Object.defineProperty(win, 'crossOriginIsolated', {
      get: markNative(function() {
        if (this !== win) throw new TypeError('Illegal invocation');
        return false;
      }, 'get crossOriginIsolated'),
      enumerable: true, configurable: true,
    });
  }

  // --- 16. window.origin → location.origin ---
  // Chrome: window.origin is a getter-only (no setter)
  try {
    Object.defineProperty(win, 'origin', {
      get: markNative(function() {
        if (this !== win) throw new TypeError('Illegal invocation');
        try { return win.location.origin; } catch (e) { return 'null'; }
      }, 'get origin'),
      enumerable: true, configurable: true,
    });
  } catch (e) {}

  // --- 17. window.name ---
  // In real Chrome, window.name defaults to "" (empty string). jsdom may have undefined.
  if (win.name === undefined) {
    try {
      Object.defineProperty(win, 'name', {
        value: '',
        writable: true, enumerable: true, configurable: true,
      });
    } catch (e) {}
  }

  // --- 18. document.domain ---
  // Chrome: document.domain getter returns location.hostname
  if (doc2) {
    try {
      Object.defineProperty(doc2, 'domain', {
        get: markNative(function() {
          try { return win.location.hostname; } catch (e) { return ''; }
        }, 'get domain'),
        set: markNative(function() {}, 'set domain'),
        enumerable: true, configurable: true,
      });
    } catch (e) {}
  }

  // --- 19. window.visualViewport ---
  // Chrome provides VisualViewport object with width/height/scale etc.
  // Reads from window.innerWidth/innerHeight dynamically (syncs with fingerprint overrides).
  if (!win.visualViewport) {
    const visualViewport = Object.create(ctx.EventTarget ? ctx.EventTarget.prototype : ctx.Object.prototype);
    Object.defineProperty(visualViewport, 'width', {
      get: markNative(function() { return win.innerWidth || 1920; }, 'get width'),
      enumerable: true, configurable: true,
    });
    Object.defineProperty(visualViewport, 'height', {
      get: markNative(function() { return win.innerHeight || 1080; }, 'get height'),
      enumerable: true, configurable: true,
    });
    const staticVpProps = { offsetLeft: 0, offsetTop: 0, pageLeft: 0, pageTop: 0, scale: 1 };
    for (const [key, value] of Object.entries(staticVpProps)) {
      Object.defineProperty(visualViewport, key, {
        get: markNative(function() { return value; }, `get ${key}`),
        enumerable: true, configurable: true,
      });
    }
    visualViewport.onresize = null;
    visualViewport.onscroll = null;
    visualViewport.addEventListener = markNative(function addEventListener(type, listener) {}, 'addEventListener', 2);
    visualViewport.removeEventListener = markNative(function removeEventListener(type, listener) {}, 'removeEventListener', 2);
    Object.defineProperty(visualViewport, Symbol.toStringTag, { value: 'VisualViewport', configurable: true });
    Object.defineProperty(win, 'visualViewport', {
      get: markNative(function() { return visualViewport; }, 'get visualViewport'),
      enumerable: true, configurable: true,
    });
  }

  // --- 20. location.ancestorOrigins ---
  // Chrome: DOMStringList with length 0 (for top-level frames)
  try {
    const loc = win.location;
    if (loc && !loc.ancestorOrigins) {
      const ancestorOrigins = Object.create(ctx.Object.prototype);
      Object.defineProperty(ancestorOrigins, 'length', {
        get: markNative(function() { return 0; }, 'get length'),
        enumerable: true, configurable: true,
      });
      ancestorOrigins.item = markNative(function item(index) { return null; }, 'item', 1);
      ancestorOrigins.contains = markNative(function contains(string) { return false; }, 'contains', 1);
      Object.defineProperty(ancestorOrigins, Symbol.toStringTag, { value: 'DOMStringList', configurable: true });
      Object.defineProperty(ancestorOrigins, Symbol.iterator, {
        value: markNative(function values() { const next = markNative(function next() { return { done: true, value: undefined }; }, 'next'); const iter = { next: next }; iter[Symbol.iterator] = markNative(function() { return this; }, '[Symbol.iterator]'); return iter; }, 'values'),
        configurable: true,
      });
      Object.defineProperty(loc, 'ancestorOrigins', {
        get: markNative(function() { return ancestorOrigins; }, 'get ancestorOrigins'),
        enumerable: true, configurable: true,
      });
    }
  } catch (e) {}

  // --- 21. requestIdleCallback / cancelIdleCallback ---
  // jsdom may not provide these. Chrome has them for idle scheduling.
  if (typeof win.requestIdleCallback !== 'function') {
    win.requestIdleCallback = markNative(function requestIdleCallback(callback, options) {
      const timeout = (options && options.timeout) || 50;
      return win.setTimeout(function() {
        const start = Date.now();
        callback({
          didTimeout: false,
          timeRemaining: markNative(function timeRemaining() { return Math.max(0, 50 - (Date.now() - start)); }, 'timeRemaining'),
        });
      }, 1);
    }, 'requestIdleCallback', 1);
  }
  if (typeof win.cancelIdleCallback !== 'function') {
    win.cancelIdleCallback = markNative(function cancelIdleCallback(id) {
      win.clearTimeout(id);
    }, 'cancelIdleCallback', 1);
  }

  // --- 22. document.exitFullscreen / fullscreenElement / fullscreenEnabled ---
  if (doc2) {
    if (typeof doc2.exitFullscreen !== 'function') {
      doc2.exitFullscreen = markNative(function exitFullscreen() {
        return Promise.resolve();
      }, 'exitFullscreen', 0);
    }
    if (doc2.fullscreenElement === undefined) {
      Object.defineProperty(doc2, 'fullscreenElement', {
        get: markNative(function() { return null; }, 'get fullscreenElement'),
        enumerable: true, configurable: true,
      });
    }
    if (doc2.fullscreenEnabled === undefined) {
      Object.defineProperty(doc2, 'fullscreenEnabled', {
        get: markNative(function() { return true; }, 'get fullscreenEnabled'),
        enumerable: true, configurable: true,
      });
    }
  }

  // --- 23. navigator.sendBeacon ---
  // Chrome defines sendBeacon on Navigator.prototype (not the instance).
  if (win.navigator && typeof win.navigator.sendBeacon !== 'function') {
    const navProto2 = Object.getPrototypeOf(win.navigator);
    const sendBeaconTarget = navProto2 || win.navigator;
    Object.defineProperty(sendBeaconTarget, 'sendBeacon', {
      value: markNative(function sendBeacon(url, data) {
        return true;
      }, 'sendBeacon', 2),
      writable: true,
      enumerable: true,
      configurable: true,
    });
  }

  // --- 24. document.elementFromPoint / elementsFromPoint ---
  // jsdom may have these but they often return null; ensure they exist
  if (doc2) {
    if (typeof doc2.elementFromPoint !== 'function') {
      doc2.elementFromPoint = markNative(function elementFromPoint(x, y) {
        return doc2.body || doc2.documentElement || null;
      }, 'elementFromPoint', 2);
    }
    if (typeof doc2.elementsFromPoint !== 'function') {
      doc2.elementsFromPoint = markNative(function elementsFromPoint(x, y) {
        const el = doc2.elementFromPoint(x, y);
        return el ? [el, doc2.body, doc2.documentElement].filter(Boolean) : [];
      }, 'elementsFromPoint', 2);
    }
  }

  // --- 25. Element.prototype.getBoundingClientRect returns DOMRect ---
  // jsdom's getBoundingClientRect may return a plain object; ensure it looks like DOMRect
  try {
    const Element = win.Element;
    if (Element && Element.prototype.getBoundingClientRect) {
      const origGetBCR = Element.prototype.getBoundingClientRect;
      Element.prototype.getBoundingClientRect = markNative(function getBoundingClientRect() {
        const rect = origGetBCR.call(this);
        // Ensure all standard DOMRect properties exist
        if (rect && rect.toJSON === undefined) {
          rect.toJSON = markNative(function toJSON() {
            return { x: rect.x || 0, y: rect.y || 0, width: rect.width || 0, height: rect.height || 0,
                     top: rect.top || 0, right: rect.right || 0, bottom: rect.bottom || 0, left: rect.left || 0 };
          }, 'toJSON');
        }
        return rect;
      }, 'getBoundingClientRect', 0);
    }
  } catch (e) {}

  // --- 27. window.navigation (Navigation API, Chrome 105+) ---
  // Scripts check presence of window.navigation as Chrome identity signal
  if (!win.navigation) {
    const Navigation = markNative(function Navigation() {
      throw new TypeError('Illegal constructor');
    }, 'Navigation');
    Navigation.prototype = Object.create(ctx.EventTarget ? ctx.EventTarget.prototype : ctx.Object.prototype);
    Object.defineProperty(Navigation.prototype, Symbol.toStringTag, { value: 'Navigation', configurable: true });
    Object.defineProperty(Navigation.prototype, 'constructor', { value: Navigation, writable: true, configurable: true });
    win.Navigation = Navigation;

    const navigation = { __proto__: Navigation.prototype };
    Object.defineProperties(navigation, {
      canGoBack: { get: markNative(function() { return false; }, 'get canGoBack'), enumerable: true, configurable: true },
      canGoForward: { get: markNative(function() { return false; }, 'get canGoForward'), enumerable: true, configurable: true },
      oncurrententrychange: { value: null, writable: true, enumerable: true, configurable: true },
      onnavigate: { value: null, writable: true, enumerable: true, configurable: true },
      onnavigateerror: { value: null, writable: true, enumerable: true, configurable: true },
      onnavigatesuccess: { value: null, writable: true, enumerable: true, configurable: true },
      transition: { value: null, writable: true, enumerable: true, configurable: true },
    });
    Object.defineProperty(win, 'navigation', {
      get: markNative(function() {
        if (this !== win) throw new TypeError('Illegal invocation');
        return navigation;
      }, 'get navigation'),
      enumerable: true, configurable: true,
    });
  }

  // --- 28. navigator.webkitPersistentStorage / webkitTemporaryStorage ---
  // REMOVED: Chrome 119 removed these APIs. Their presence on Chrome 120+ is a detection vector.
  // If target scripts access them, they'll get undefined (matches real Chrome 120+ behavior)
  // and the monitor will capture it in the todo list.

  // --- 29. Date.prototype methods markNative ---
  // 瑞数/Akamai check Date.prototype.getTime.toString() for [native code]
  const dateMethods = [
    'getTime', 'getFullYear', 'getMonth', 'getDate', 'getDay',
    'getHours', 'getMinutes', 'getSeconds', 'getMilliseconds',
    'getTimezoneOffset', 'toISOString', 'toJSON', 'toLocaleString',
    'toLocaleDateString', 'toLocaleTimeString', 'toString', 'valueOf',
  ];
  const DateProto = (win.Date || Date).prototype;
  for (const name of dateMethods) {
    if (typeof DateProto[name] === 'function') {
      markNative(DateProto[name], name);
    }
  }

  // --- 30. webkit-prefixed animation frame functions ---
  // Chrome still exposes these; absence is a signal
  if (typeof win.webkitRequestAnimationFrame !== 'function') {
    win.webkitRequestAnimationFrame = markNative(function webkitRequestAnimationFrame(cb) {
      return win.requestAnimationFrame(cb);
    }, 'webkitRequestAnimationFrame', 1);
  }
  if (typeof win.webkitCancelAnimationFrame !== 'function') {
    win.webkitCancelAnimationFrame = markNative(function webkitCancelAnimationFrame(id) {
      return win.cancelAnimationFrame(id);
    }, 'webkitCancelAnimationFrame', 1);
  }

  // --- 31. Modern Chrome API stubs (existence checks only) ---
  // Anti-bot scripts check `typeof window.X === 'function'` for these
  const CtxDOMException = win.DOMException || DOMException;
  const modernApis = [
    ['getScreenDetails', 0], ['queryLocalFonts', 0],
    ['showDirectoryPicker', 0], ['showOpenFilePicker', 0], ['showSaveFilePicker', 0],
  ];
  for (const [name, len] of modernApis) {
    if (typeof win[name] !== 'function') {
      win[name] = markNative(function() {
        return Promise.reject(new CtxDOMException('The request is not allowed', 'NotAllowedError'));
      }, name, len);
    }
  }

  // NOTE: .prototype stripping for native window functions moved to armor/index.js
  // (must run AFTER all armor steps that create/overwrite window functions)
}

module.exports = { installJsdomHider };
