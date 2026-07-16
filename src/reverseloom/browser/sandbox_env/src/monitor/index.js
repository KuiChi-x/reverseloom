'use strict';

/**
 * monitor/index.js — Monitor switch controller.
 * When ON: wraps context globals with Deep Proxy + Phantom Chain.
 * When OFF: never loaded (zero overhead).
 *
 * Network recorder and cookie trap are always active (installed separately).
 * This module only adds the Proxy monitoring layer for missing API detection.
 */

const { createProxyFactory } = require('./proxy-factory');
const { createRecorder } = require('./recorder');

/**
 * Install monitoring layer on the VM context.
 * Wraps key objects so that undefined property accesses are tracked
 * and reported in the todo list.
 *
 * @param {object} ctx - vm context (armor already installed)
 * @returns {object} - monitorHandle with recorder access
 */
function installMonitor(ctx, markNative) {
  const recorder = createRecorder();
  const proxyFactory = createProxyFactory(recorder);

  // Wrap top-level objects with monitoring proxies.
  // Note: with jsdom, these are real objects with proper prototypes.
  // The Proxy layer adds missing-property detection on top.
  // Preserve original descriptor type: accessor → markNative'd getter; data → value assignment.
  const targets = ['navigator', 'screen', 'document', 'location', 'chrome', 'performance'];

  for (const name of targets) {
    const obj = ctx[name];
    if (obj !== null && obj !== undefined && typeof obj === 'object') {
      const wrapped = proxyFactory.wrap(obj, name);
      try {
        // Check if property is currently a data property (e.g. window.chrome)
        const desc = Object.getOwnPropertyDescriptor(ctx, name);
        if (desc && 'value' in desc && !desc.get) {
          // Data property — keep as data, just replace value with proxy-wrapped version
          Object.defineProperty(ctx, name, {
            value: wrapped,
            writable: desc.writable !== false,
            enumerable: true,
            configurable: true,
          });
        } else {
          // Accessor property — replace getter (markNative'd) with monitor version
          const getter = function() { return wrapped; };
          const setter = function(v) {}; // Chrome has setters on Window.prototype, they're no-ops
          if (markNative) {
            markNative(getter, `get ${name}`);
            markNative(setter, `set ${name}`);
          }
          Object.defineProperty(ctx, name, {
            get: getter,
            set: setter,
            enumerable: true,
            configurable: true,
          });
        }
      } catch (e) {
        // If defineProperty fails (non-configurable), skip this target
      }
    }
  }

  // --- Global miss monitoring: prototype chain Proxy ---
  // JS property lookup walks the prototype chain. If a property doesn't exist
  // as an own property on ctx, it falls through to the prototype.
  // We insert a Proxy there to catch ALL missing global accesses (zero maintenance).
  const origProto = Object.getPrototypeOf(ctx);
  const globalMissProxy = new Proxy(origProto || Object.prototype, {
    get(target, prop, receiver) {
      if (typeof prop === 'string' && prop !== 'constructor' && prop !== '__proto__') {
        if (!(prop in target)) {
          recorder.missingAccess(`window.${prop}`, 'property', 0);
        }
      }
      return Reflect.get(target, prop, receiver);
    },
    has(target, prop) {
      const exists = Reflect.has(target, prop);
      if (!exists && typeof prop === 'string' && prop !== 'constructor' && prop !== '__proto__') {
        recorder.hasCheck(`window.${prop}`);
        recorder.missingAccess(`window.${prop}`, 'has_check', 0);
      }
      return exists;
    },
  });
  Object.setPrototypeOf(ctx, globalMissProxy);

  // --- Make Proxy transparent to instanceof checks ---
  // The Proxy sits between ctx and Window.prototype in the chain.
  // `instanceof` uses internal [[GetPrototypeOf]] which sees the Proxy, not Window.prototype.
  // Fix: patch Window[Symbol.hasInstance] so `window instanceof Window === true`.
  if (ctx.Window) {
    const origWindowHasInstance = ctx.Window[Symbol.hasInstance];
    const windowHasInstanceFn = function(instance) {
      if (instance === ctx) return true;
      if (origWindowHasInstance) return origWindowHasInstance.call(this, instance);
      return false;
    };
    if (markNative) markNative(windowHasInstanceFn, '[Symbol.hasInstance]');
    Object.defineProperty(ctx.Window, Symbol.hasInstance, {
      value: windowHasInstanceFn,
      writable: true, configurable: true,
    });
  }

  // --- Make Proxy transparent to prototype chain inspection ---
  // Object.getPrototypeOf(window) must return origProto (not the Proxy itself)
  const origGetProto = ctx.Object.getPrototypeOf;
  const patchedGetProto = function getPrototypeOf(obj) {
    if (obj === ctx) return origProto;
    return origGetProto(obj);
  };
  if (markNative) markNative(patchedGetProto, 'getPrototypeOf');
  ctx.Object.getPrototypeOf = patchedGetProto;
  if (ctx.Reflect) {
    const origReflectGetProto = ctx.Reflect.getPrototypeOf;
    const patchedReflectGetProto = function getPrototypeOf(obj) {
      if (obj === ctx) return origProto;
      return origReflectGetProto(obj);
    };
    if (markNative) markNative(patchedReflectGetProto, 'getPrototypeOf');
    ctx.Reflect.getPrototypeOf = patchedReflectGetProto;
  }

  // --- Patch __proto__ accessor so window.__proto__ === Window.prototype ---
  // The native __proto__ getter returns the internal [[Prototype]] which is the Proxy.
  // Patch Object.prototype.__proto__ getter to return origProto for ctx.
  try {
    const protoDesc = Object.getOwnPropertyDescriptor(ctx.Object.prototype, '__proto__');
    if (protoDesc && protoDesc.get) {
      const origProtoGetter = protoDesc.get;
      const origProtoSetter = protoDesc.set;
      const patchedProtoGetter = function() {
        if (this === ctx) return origProto;
        return origProtoGetter.call(this);
      };
      if (markNative) markNative(patchedProtoGetter, 'get __proto__');
      Object.defineProperty(ctx.Object.prototype, '__proto__', {
        get: patchedProtoGetter,
        set: origProtoSetter,
        enumerable: false,
        configurable: true,
      });
    }
  } catch (e) {}

  // --- Fix EventTarget[Symbol.hasInstance] after wrapping navigator ---
  // chrome-overlay patches EventTarget[Symbol.hasInstance] with `instance === nav`,
  // but after monitor wraps navigator with a Proxy, target code sees the Proxy.
  // Re-patch to recognize the wrapped proxy version.
  if (ctx.EventTarget) {
    const wrappedNav = ctx.navigator; // getter returns the proxy-wrapped navigator
    const origETHasInstance = ctx.EventTarget[Symbol.hasInstance];
    const patchedETHasInstance = function(instance) {
      if (instance === wrappedNav) return true;
      if (origETHasInstance) return origETHasInstance.call(this, instance);
      return false;
    };
    if (markNative) markNative(patchedETHasInstance, '[Symbol.hasInstance]');
    Object.defineProperty(ctx.EventTarget, Symbol.hasInstance, {
      value: patchedETHasInstance,
      writable: true, configurable: true,
    });
  }

  return {
    recorder,
    getReport() {
      return recorder.summarize();
    },
  };
}

module.exports = { installMonitor };
