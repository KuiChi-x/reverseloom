'use strict';

/**
 * mark-native.js — Function.prototype.toString defense.
 *
 * Makes any function appear as native code:
 *   func.toString() === "function funcName() { [native code] }"
 *
 * Technique: WeakMap-based registry + patched Function.prototype.toString.
 * NO own toString is set on individual functions (Chrome doesn't do this).
 * Must be installed FIRST — all other armor modules depend on it.
 */

/**
 * Install markNative into a jsdom VM context.
 * @param {object} ctx - vm context (from dom.getInternalVMContext())
 * @returns {object} - { markNative, markGetter, markAccessor }
 */
function installMarkNative(ctx) {
  // WeakMap: stores the "native name" for each marked function
  const nativeRegistry = new WeakMap();

  // Regex patterns that catch jsdom internal function signatures.
  // These leak via toString() when a function hasn't been explicitly markNative'd.
  const jsdomPatterns = [
    /^\s*\w+\s*\([^)]*\)\s*\{[\s\S]*?const\s+esValue\s*=/,
    /^\s*function\s*\([^)]*\)\s*\{[\s\S]*?this\._globalObject/,
    /^\s*\w+\s*\([^)]*\)\s*\{\s*const\s+\w+\s*=\s*this\s*!==/,
    /notImplementedMethod|notImplemented/,
    /^\s*function\s*\w*\s*\([^)]*\)\s*\{\s*return\s+[\w.]+\.call\(this/,
  ];

  // Patch Function.prototype.toString in the context
  const ctxFunction = ctx.Function || Function;
  const origToString = ctxFunction.prototype.toString;
  // Also grab the HOST realm's Function.prototype.toString for cross-realm calls
  const hostOrigToString = Function.prototype.toString;

  const patchedToString = function toString() {
    if (typeof this === 'function' && nativeRegistry.has(this)) {
      const n = nativeRegistry.get(this);
      return `function ${n}() { [native code] }`;
    }
    // Fallback: check source against jsdom patterns (catches unregistered DOM functions)
    if (typeof this === 'function') {
      let src;
      try {
        // Try vm-context toString first, then host toString (for cross-realm functions)
        try { src = origToString.call(this); } catch (e2) { src = hostOrigToString.call(this); }
      } catch (e) {
        return 'function () { [native code] }';
      }
      for (let i = 0; i < jsdomPatterns.length; i++) {
        if (jsdomPatterns[i].test(src)) {
          return `function ${this.name || ''}() { [native code] }`;
        }
      }
      return src;
    }
    return origToString.call(this);
  };

  // The patched toString must itself look native
  nativeRegistry.set(patchedToString, 'toString');

  Object.defineProperty(ctxFunction.prototype, 'toString', {
    value: patchedToString,
    writable: true,
    enumerable: false,
    configurable: true,
  });

  // Fix toString prototype chain
  try { Object.setPrototypeOf(patchedToString, ctxFunction.prototype); } catch (e) {}

  // Also mark the original toString
  nativeRegistry.set(origToString, 'toString');

  // Patch the HOST realm's Function.prototype.toString as well.
  // This ensures that when code in the vm calls fn.toString() on a host-realm function
  // (which would find the host's Function.prototype.toString via prototype chain),
  // it still returns [native code]. Without this, fn.toString() on cross-realm functions
  // bypasses the vm-patched toString.
  const hostPatchedToString = function toString() {
    if (typeof this === 'function' && nativeRegistry.has(this)) {
      const n = nativeRegistry.get(this);
      return `function ${n}() { [native code] }`;
    }
    if (typeof this === 'function') {
      let src;
      try { src = hostOrigToString.call(this); } catch (e) {
        return 'function () { [native code] }';
      }
      for (let i = 0; i < jsdomPatterns.length; i++) {
        if (jsdomPatterns[i].test(src)) {
          return `function ${this.name || ''}() { [native code] }`;
        }
      }
      return src;
    }
    return hostOrigToString.call(this);
  };
  nativeRegistry.set(hostPatchedToString, 'toString');

  Object.defineProperty(Function.prototype, 'toString', {
    value: hostPatchedToString,
    writable: true,
    enumerable: false,
    configurable: true,
  });

  /**
   * Mark a function as native. When .toString() is called, it will return
   * "function <name>() { [native code] }"
   *
   * Does NOT set own toString property (Chrome native functions don't have one).
   * Instead relies on Function.prototype.toString being patched to check the registry.
   */
  function markNative(fn, name, length) {
    if (typeof fn !== 'function') return fn;
    const displayName = name || fn.name || '';
    nativeRegistry.set(fn, displayName);
    // Fix .name if different
    if (fn.name !== displayName && displayName) {
      try {
        Object.defineProperty(fn, 'name', { value: displayName, configurable: true });
      } catch (e) {}
    }
    // Fix .length (parameter count) if specified
    if (length !== undefined && fn.length !== length) {
      try {
        Object.defineProperty(fn, 'length', { value: length, configurable: true });
      } catch (e) {}
    }
    return fn;
  }

  /**
   * Mark a getter/setter as native.
   */
  function markGetter(fn, name) {
    return markNative(fn, name);
  }

  /**
   * Mark both getter and setter of a property descriptor.
   */
  function markAccessor(descriptor, name) {
    if (descriptor.get) markNative(descriptor.get, `get ${name}`);
    if (descriptor.set) markNative(descriptor.set, `set ${name}`);
    return descriptor;
  }

  return {
    markNative,
    markGetter,
    markAccessor,
    nativeRegistry,
  };
}

/**
 * Walk a prototype chain and markNative every function found.
 * @param {object} obj - starting object (typically SomeClass.prototype)
 * @param {function} markNative - the markNative function
 * @param {number} maxDepth - how deep to walk (default 10)
 */
function scanPrototypeChain(obj, markNative, maxDepth, ctxObjectProto) {
  let proto = obj;
  const stopAt = ctxObjectProto || Object.prototype;
  for (let d = 0; d < (maxDepth || 10) && proto; d++) {
    let names;
    try { names = Object.getOwnPropertyNames(proto); } catch (e) { break; }
    for (let i = 0; i < names.length; i++) {
      try {
        const desc = Object.getOwnPropertyDescriptor(proto, names[i]);
        if (!desc) continue;
        if (typeof desc.value === 'function') markNative(desc.value);
        if (typeof desc.get === 'function') markNative(desc.get);
        if (typeof desc.set === 'function') markNative(desc.set);
      } catch (e) {}
    }
    proto = Object.getPrototypeOf(proto);
    if (proto === stopAt || proto === null) break;
  }
}

/**
 * Mark ALL functions on all major DOM prototype chains as native.
 * This ensures jsdom internal functions don't leak via toString().
 * @param {object} ctx - vm context (window)
 * @param {function} markNative - the markNative function
 */
function scanAllPrototypes(ctx, markNative) {
  const protoTargets = [
    ctx.Document && ctx.Document.prototype,
    ctx.HTMLDocument && ctx.HTMLDocument.prototype,
    ctx.Element && ctx.Element.prototype,
    ctx.HTMLElement && ctx.HTMLElement.prototype,
    ctx.Node && ctx.Node.prototype,
    ctx.EventTarget && ctx.EventTarget.prototype,
    ctx.XMLHttpRequest && ctx.XMLHttpRequest.prototype,
    ctx.HTMLCanvasElement && ctx.HTMLCanvasElement.prototype,
    ctx.HTMLInputElement && ctx.HTMLInputElement.prototype,
    ctx.HTMLFormElement && ctx.HTMLFormElement.prototype,
    ctx.HTMLAnchorElement && ctx.HTMLAnchorElement.prototype,
    ctx.HTMLImageElement && ctx.HTMLImageElement.prototype,
    ctx.HTMLDivElement && ctx.HTMLDivElement.prototype,
    ctx.HTMLSpanElement && ctx.HTMLSpanElement.prototype,
    ctx.HTMLBodyElement && ctx.HTMLBodyElement.prototype,
    ctx.HTMLHeadElement && ctx.HTMLHeadElement.prototype,
    ctx.HTMLScriptElement && ctx.HTMLScriptElement.prototype,
    ctx.HTMLStyleElement && ctx.HTMLStyleElement.prototype,
    ctx.HTMLLinkElement && ctx.HTMLLinkElement.prototype,
    ctx.HTMLMetaElement && ctx.HTMLMetaElement.prototype,
    ctx.Window && ctx.Window.prototype,
    ctx.Location && ctx.Location.prototype,
    ctx.DOMParser && ctx.DOMParser.prototype,
    ctx.URL && ctx.URL.prototype,
    ctx.Event && ctx.Event.prototype,
    ctx.CustomEvent && ctx.CustomEvent.prototype,
    ctx.MutationObserver && ctx.MutationObserver.prototype,
    ctx.ResizeObserver && ctx.ResizeObserver.prototype,
    ctx.DOMTokenList && ctx.DOMTokenList.prototype,
    ctx.NamedNodeMap && ctx.NamedNodeMap.prototype,
    ctx.NodeList && ctx.NodeList.prototype,
    ctx.HTMLCollection && ctx.HTMLCollection.prototype,
    ctx.CSSStyleDeclaration && ctx.CSSStyleDeclaration.prototype,
    ctx.Text && ctx.Text.prototype,
    ctx.Comment && ctx.Comment.prototype,
    ctx.DocumentFragment && ctx.DocumentFragment.prototype,
    ctx.Range && ctx.Range.prototype,
    ctx.Selection && ctx.Selection.prototype,
    ctx.TreeWalker && ctx.TreeWalker.prototype,
    ctx.NodeIterator && ctx.NodeIterator.prototype,
    ctx.Attr && ctx.Attr.prototype,
    ctx.CharacterData && ctx.CharacterData.prototype,
    ctx.Storage && ctx.Storage.prototype,
    ctx.FormData && ctx.FormData.prototype,
    ctx.Headers && ctx.Headers.prototype,
    ctx.Response && ctx.Response.prototype,
    ctx.Request && ctx.Request.prototype,
    ctx.AbortController && ctx.AbortController.prototype,
    ctx.AbortSignal && ctx.AbortSignal.prototype,
  ];
  const ctxObjProto = (ctx.Object || Object).prototype;
  for (let i = 0; i < protoTargets.length; i++) {
    if (protoTargets[i]) scanPrototypeChain(protoTargets[i], markNative, 3, ctxObjProto);
  }
  // Also scan document and navigator instances deeply
  if (ctx.document) scanPrototypeChain(ctx.document, markNative, 5, ctxObjProto);
  if (ctx.navigator) scanPrototypeChain(ctx.navigator, markNative, 5, ctxObjProto);
}

module.exports = { installMarkNative, scanAllPrototypes };
