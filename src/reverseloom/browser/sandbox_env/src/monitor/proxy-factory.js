'use strict';

/**
 * proxy-factory.js — Deep recursive Proxy wrapper for monitoring.
 *
 * Wraps any object with a Proxy that:
 *   1. Records all property reads/writes/calls
 *   2. Returns Phantom Chain for undefined/missing properties
 *   3. Recursively wraps sub-objects (with memoization to avoid double-wrap)
 *
 * When monitor is OFF, this module is never loaded.
 */

const { isPhantom } = require('./phantom-chain');

/**
 * Create a proxy factory bound to a recorder.
 *
 * @param {object} recorder - from recorder.js
 * @returns {{ wrap: function }}
 */
function createProxyFactory(recorder) {
  // Memoize: prevent double-wrapping the same object
  const wrapped = new WeakMap();

  // Skip wrapping these (they cause issues or are immutable)
  const SKIP_WRAP = new Set([
    'Object', 'Array', 'Function', 'String', 'Number', 'Boolean',
    'Symbol', 'RegExp', 'Date', 'Math', 'JSON', 'Map', 'Set',
    'WeakMap', 'WeakSet', 'Promise', 'Proxy', 'Reflect',
    'Error', 'TypeError', 'RangeError', 'ReferenceError', 'SyntaxError',
    'parseInt', 'parseFloat', 'isNaN', 'isFinite',
    'encodeURI', 'decodeURI', 'encodeURIComponent', 'decodeURIComponent',
    'setTimeout', 'setInterval', 'clearTimeout', 'clearInterval',
    'console', 'Intl', 'Atomics', 'SharedArrayBuffer',
    'ArrayBuffer', 'DataView', 'BigInt',
  ]);

  /**
   * Wrap an object with monitoring Proxy.
   *
   * @param {object} obj - Target object
   * @param {string} name - Path name (e.g., "window", "navigator")
   * @returns {Proxy}
   */
  function wrap(obj, name) {
    if (obj === null || obj === undefined) return obj;
    if (typeof obj !== 'object' && typeof obj !== 'function') return obj;
    if (isPhantom(obj)) return obj;

    // Check memo
    if (wrapped.has(obj)) return wrapped.get(obj);

    const proxy = new Proxy(obj, {
      get(target, prop, receiver) {
        // Symbols pass through (avoid breaking internal mechanics)
        if (typeof prop === 'symbol') {
          return Reflect.get(target, prop, receiver);
        }

        // Skip internal properties
        if (prop === '__proto__' || prop === 'constructor') {
          return Reflect.get(target, prop, receiver);
        }

        const path = `${name}.${prop}`;
        let value;

        try {
          // Check for accessor property (getter) — must call with target as this
          // to avoid "Illegal invocation" from this-checking getters (chrome-overlay, fingerprint)
          let desc = Object.getOwnPropertyDescriptor(target, prop);
          if (!desc) {
            let proto = Object.getPrototypeOf(target);
            while (proto && !desc) {
              desc = Object.getOwnPropertyDescriptor(proto, prop);
              proto = Object.getPrototypeOf(proto);
            }
          }
          if (desc && desc.get) {
            value = desc.get.call(target);
          } else {
            value = Reflect.get(target, prop, receiver);
          }
        } catch (e) {
          // Getter threw (e.g., node-hider traps) — record and re-throw
          recorder.read(path, 'throws');
          throw e;
        }

        recorder.read(path, typeof value);

        // If value is undefined AND property doesn't exist → record miss, return undefined
        // Returning undefined (not a phantom) avoids typeof detection vector.
        // The miss is already recorded; deeper accesses will throw TypeError which
        // the engine captures and reports as blocking_error with full path info.
        if (value === undefined && !(prop in target)) {
          recorder.missingAccess(path, 'property', 0);
          return undefined;
        }

        // If value is null, return as-is
        if (value === null) return value;

        // Don't recursively wrap primitives
        if (typeof value !== 'object' && typeof value !== 'function') {
          return value;
        }

        // Skip known safe/expensive objects
        if (SKIP_WRAP.has(prop)) return value;

        // Recursively wrap objects (with depth awareness via path length)
        const depth = path.split('.').length;
        if (depth > 8) return value; // Stop deep wrapping

        return wrap(value, path);
      },

      set(target, prop, value, receiver) {
        if (typeof prop !== 'symbol') {
          recorder.write(`${name}.${prop}`, typeof value);
        }
        return Reflect.set(target, prop, value, receiver);
      },

      has(target, prop) {
        if (typeof prop !== 'symbol') {
          const path = `${name}.${prop}`;
          recorder.hasCheck(path);
          // If property doesn't exist, also record as missing so todo list picks it up
          const exists = Reflect.has(target, prop);
          if (!exists) {
            recorder.missingAccess(path, 'has_check');
          }
          return exists;
        }
        return Reflect.has(target, prop);
      },

      apply(target, thisArg, args) {
        recorder.call(name, args.length);
        return Reflect.apply(target, thisArg, args);
      },

      construct(target, args, newTarget) {
        recorder.call(`new ${name}`, args.length);
        return Reflect.construct(target, args, newTarget);
      },

      getOwnPropertyDescriptor(target, prop) {
        return Reflect.getOwnPropertyDescriptor(target, prop);
      },

      ownKeys(target) {
        return Reflect.ownKeys(target);
      },

      getPrototypeOf(target) {
        return Reflect.getPrototypeOf(target);
      },

      setPrototypeOf(target, proto) {
        return Reflect.setPrototypeOf(target, proto);
      },

      defineProperty(target, prop, descriptor) {
        return Reflect.defineProperty(target, prop, descriptor);
      },

      deleteProperty(target, prop) {
        return Reflect.deleteProperty(target, prop);
      },

      isExtensible(target) {
        return Reflect.isExtensible(target);
      },

      preventExtensions(target) {
        return Reflect.preventExtensions(target);
      },
    });

    wrapped.set(obj, proxy);
    return proxy;
  }

  return { wrap };
}

module.exports = { createProxyFactory };
