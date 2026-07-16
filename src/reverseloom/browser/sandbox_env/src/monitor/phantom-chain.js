'use strict';

/**
 * phantom-chain.js — Deep Phantom Proxy for tracking undefined access chains.
 *
 * When code accesses: window.chrome.webstore.install
 * And chrome.webstore is undefined, the Phantom Chain:
 *   1. Returns a Proxy instead of crashing
 *   2. Records "window.chrome.webstore" as missing
 *   3. When .install is accessed on the phantom, records "window.chrome.webstore.install"
 *   4. When the phantom is called as function, records it as missing function
 *
 * Depth-limited to prevent infinite chains (max 6 levels).
 */

const MAX_DEPTH = 6;
const PHANTOM_MARK = Symbol();

/**
 * Create a phantom proxy chain from a base path.
 *
 * @param {string} basePath - The full path so far (e.g., "window.chrome.webstore")
 * @param {object} recorder - The recorder instance
 * @param {number} depth - Current depth (stops at MAX_DEPTH)
 * @returns {Proxy}
 */
function createPhantom(basePath, recorder, depth) {
  if (depth > MAX_DEPTH) return undefined;

  // Use a function as target so the phantom can be called/constructed
  const target = function () {};

  const phantom = new Proxy(target, {
    get(_, prop) {
      // Ignore symbols (prevents issues with type coercion, etc.)
      if (typeof prop === 'symbol') {
        if (prop === PHANTOM_MARK) return true;
        if (prop === Symbol.toPrimitive) return () => undefined;
        if (prop === Symbol.toStringTag) return 'undefined';
        return undefined;
      }

      // Common type coercion traps — return consistent values
      if (prop === 'toString') return () => 'undefined';
      if (prop === 'valueOf') return () => undefined;
      if (prop === 'toJSON') return () => undefined;
      if (prop === 'then') return undefined; // Prevent Promise detection
      if (prop === 'length') return 0;

      const childPath = `${basePath}.${prop}`;
      recorder.missingAccess(childPath, 'property', depth + 1);
      return createPhantom(childPath, recorder, depth + 1);
    },

    set(_, prop, value) {
      if (typeof prop === 'symbol') return true;
      const childPath = `${basePath}.${prop}`;
      recorder.write(childPath, typeof value);
      return true;
    },

    apply(_, thisArg, args) {
      recorder.missingAccess(basePath, 'function', depth, args.length);
      return undefined;
    },

    construct(_, args) {
      recorder.missingAccess(basePath, 'constructor', depth, args.length);
      return {};
    },

    has(_, prop) {
      return false;
    },

    getOwnPropertyDescriptor() {
      return undefined;
    },

    ownKeys() {
      return [];
    },
  });

  return phantom;
}

/**
 * Check if a value is a phantom proxy.
 */
function isPhantom(value) {
  try {
    return value && value[PHANTOM_MARK] === true;
  } catch {
    return false;
  }
}

module.exports = { createPhantom, isPhantom, PHANTOM_MARK, MAX_DEPTH };
