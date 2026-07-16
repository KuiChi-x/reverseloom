'use strict';

/**
 * cookie-trap.js — Intercepts document.cookie reads/writes.
 *
 * Records all cookie writes (for report) and maintains a cookie jar.
 * Works with jsdom: overrides jsdom's built-in cookie handling with our trap.
 */

function installCookieTrap(ctx, recorder, markNativeHandle) {
  const cookieJar = {}; // name → { value, attributes }
  const cookieWrites = []; // raw set-cookie strings
  const MAX_WRITES = 500;
  const markNative = markNativeHandle ? markNativeHandle.markNative : null;

  /**
   * Parse a cookie string "name=value; path=/; ..."
   */
  function parseCookie(str) {
    const parts = str.split(';').map(s => s.trim());
    const [nameValue, ...attrs] = parts;
    const eqIdx = nameValue.indexOf('=');
    if (eqIdx < 0) return null;
    const name = nameValue.slice(0, eqIdx).trim();
    const value = nameValue.slice(eqIdx + 1).trim();
    return { name, value, attributes: attrs.join('; ') };
  }

  /**
   * Get all cookies as "name=value; name2=value2" string.
   */
  function getCookieString() {
    return Object.entries(cookieJar)
      .map(([name, { value }]) => `${name}=${value}`)
      .join('; ');
  }

  /**
   * Set a cookie from a "name=value; attrs..." string.
   */
  function setCookieString(str) {
    if (typeof str !== 'string') return;

    if (cookieWrites.length < MAX_WRITES) {
      cookieWrites.push(str);
    }
    if (recorder) {
      recorder.write('document.cookie', 'string');
    }

    const parsed = parseCookie(str);
    if (parsed) {
      cookieJar[parsed.name] = { value: parsed.value, attributes: parsed.attributes };
    }
  }

  // Install cookie getter/setter on Document.prototype (not instance)
  // Chrome defines cookie on Document.prototype: document.hasOwnProperty('cookie') === false
  const doc = ctx.document;
  if (doc) {
    const docProto = Object.getPrototypeOf(doc) || doc;
    const cookieGetter = function() {
      if (recorder) recorder.read('document.cookie', 'string');
      return getCookieString();
    };
    const cookieSetter = function(val) {
      setCookieString(val);
    };
    if (markNative) {
      markNative(cookieGetter, 'get cookie');
      markNative(cookieSetter, 'set cookie');
    }
    Object.defineProperty(docProto, 'cookie', {
      get: cookieGetter,
      set: cookieSetter,
      enumerable: true,
      configurable: true,
    });
  }

  const cookieTrap = {
    getAll() { return { ...cookieJar }; },
    getWritten() { return [...cookieWrites]; },
    getString() { return getCookieString(); },
    clear() {
      Object.keys(cookieJar).forEach(k => delete cookieJar[k]);
      cookieWrites.length = 0;
    },
  };

  return cookieTrap;
}

module.exports = { installCookieTrap };
