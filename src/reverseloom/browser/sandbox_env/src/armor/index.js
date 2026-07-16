'use strict';

/**
 * armor/index.js — Installs all anti-detection armor in correct order.
 *
 * Order matters:
 *   1. mark-native (toString defense — everything else depends on it)
 *   2. jsdom-hider (hide jsdom internals — before other patches add functions)
 *   3. node-hider (hide Node.js globals)
 *   4. chrome-overlay (add Chrome identity)
 *   5. browser-apis (constructors + stubs VMP expects to `new`)
 *   6. fingerprint (override with user values — must be last to win)
 */

const { installMarkNative } = require('./mark-native');
const { installJsdomHider } = require('./jsdom-hider');
const { installNodeHider } = require('./node-hider');
const { installChromeOverlay } = require('./chrome-overlay');
const { installBrowserApis } = require('./browser-apis');
const { applyFingerprint } = require('./fingerprint');

/**
 * Install complete anti-detection armor on a jsdom VM context.
 * @param {object} ctx - vm context from dom.getInternalVMContext()
 * @param {object} options - { fingerprint }
 * @returns {object} - { markNativeHandle }
 */
function installArmor(ctx, options) {
  const { fingerprint } = options || {};

  // 1. toString defense — must be first
  const markNativeHandle = installMarkNative(ctx);

  // 2. Hide jsdom detection signatures (Symbols, _props, function toString)
  installJsdomHider(ctx, markNativeHandle);

  // 3. Hide Node.js traces
  installNodeHider(ctx);

  // 4. Chrome overlay (navigator, chrome, screen, window dims)
  installChromeOverlay(ctx, markNativeHandle);

  // 5. Browser APIs (constructors, canvas, plugins, observers, etc.)
  installBrowserApis(ctx, markNativeHandle, fingerprint);

  // 6. Fingerprint overrides (last — wins over defaults)
  applyFingerprint(ctx, fingerprint, markNativeHandle);

  // 7. Fix cross-realm prototype chains (must be AFTER all stubs are installed)
  // jsdom creates constructors in the host realm. Their prototype chains terminate
  // at the HOST realm's Object.prototype instead of the vm context's.
  // This is detectable: Object.getPrototypeOf(X.prototype) !== Object.prototype
  // Fix: walk each prototype chain and replace the terminal Object.prototype with ctx's.
  {
    const ctxObjProto = (ctx.Object || Object).prototype;
    function fixRealmChain(proto) {
      if (!proto || proto === ctxObjProto) return;
      let current = proto;
      let depth = 0;
      while (current && depth < 20) {
        const parent = Object.getPrototypeOf(current);
        if (parent === null) break;
        if (parent === ctxObjProto) break; // already correct
        // Terminal: parent's parent is null → it's some Object.prototype
        if (Object.getPrototypeOf(parent) === null) {
          try { Object.setPrototypeOf(current, ctxObjProto); } catch (e) {}
          break;
        }
        current = parent;
        depth++;
      }
    }
    // Fix all constructor prototypes that might be from jsdom's realm
    const ctorsToFix = [
      'MutationObserver', 'IntersectionObserver', 'ResizeObserver', 'PerformanceObserver',
      'WebSocket', 'Worker', 'SharedWorker', 'MessageChannel', 'BroadcastChannel',
      'Event', 'CustomEvent', 'EventTarget', 'Node', 'Element', 'HTMLElement',
      'Document', 'HTMLDocument', 'Text', 'Comment', 'DocumentFragment',
      'XMLHttpRequest', 'DOMParser', 'Range', 'Selection', 'TreeWalker',
      'NodeIterator', 'NamedNodeMap', 'NodeList', 'HTMLCollection', 'DOMTokenList',
      'Attr', 'CharacterData', 'FormData', 'Headers', 'Response', 'Request',
      'AbortController', 'AbortSignal', 'URL', 'URLSearchParams',
      'Notification', 'RTCPeerConnection', 'OffscreenCanvas',
      'Image', 'Storage', 'CSSStyleDeclaration',
    ];
    for (const name of ctorsToFix) {
      if (ctx[name] && ctx[name].prototype) {
        fixRealmChain(ctx[name].prototype);
      }
    }
  }

  // 8. Strip .prototype from native window functions (must be LAST)
  // In Chrome: 'prototype' in alert === false (property doesn't exist at all).
  // Strategy: delete if configurable. If not, replace the function with a bound version
  // (bound functions have no .prototype) then markNative the replacement.
  const nativeFuncsNoProto = [
    'alert', 'confirm', 'prompt', 'open', 'close', 'print', 'stop',
    'focus', 'blur', 'postMessage', 'getComputedStyle', 'getSelection',
    'matchMedia', 'scroll', 'scrollTo', 'scrollBy', 'moveTo', 'moveBy',
    'resizeTo', 'resizeBy', 'atob', 'btoa', 'fetch', 'find',
    'requestAnimationFrame', 'cancelAnimationFrame',
    'requestIdleCallback', 'cancelIdleCallback',
    'webkitRequestAnimationFrame', 'webkitCancelAnimationFrame',
    'getScreenDetails', 'queryLocalFonts',
    'showDirectoryPicker', 'showOpenFilePicker', 'showSaveFilePicker',
    'setTimeout', 'setInterval', 'clearTimeout', 'clearInterval',
    'queueMicrotask', 'structuredClone', 'reportError',
    'addEventListener', 'removeEventListener', 'dispatchEvent',
    'createImageBitmap', 'captureEvents', 'releaseEvents',
    'webkitRequestFileSystem', 'webkitResolveLocalFileSystemURL',
  ];
  for (const name of nativeFuncsNoProto) {
    const fn = ctx[name];
    if (typeof fn !== 'function') continue;
    // Try delete first (works for our stubs which have configurable prototype)
    let success = false;
    try {
      const desc = Object.getOwnPropertyDescriptor(fn, 'prototype');
      if (!desc) { success = true; } // no prototype property at all
      else if (desc.configurable) {
        delete fn.prototype;
        success = !('prototype' in fn);
      }
    } catch (e) {}
    if (!success) {
      // Fallback: replace with a bound function wrapper (bound functions have NO .prototype)
      // Function.prototype.bind creates a function without .prototype property
      try {
        const bound = fn.bind(ctx);
        // Restore name and length
        markNativeHandle.markNative(bound, name, fn.length);
        ctx[name] = bound;
      } catch (e) {}
    }
  }

  return { markNativeHandle };
}

module.exports = { installArmor };
