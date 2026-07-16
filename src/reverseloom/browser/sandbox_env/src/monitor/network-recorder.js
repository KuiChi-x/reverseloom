'use strict';

/**
 * network-recorder.js — Intercepts XMLHttpRequest and fetch.
 *
 * Records all outgoing requests with:
 *   - transport: 'xhr' | 'fetch'
 *   - method, url, headers
 *   - body: { body_location, body_text, body_encoding } | null
 *   - ts: timestamp
 *
 * Provides stubbed responses (empty 200) so scripts don't crash.
 * Body capture: inline for ≤5000 chars, truncated for larger.
 */

const MAX_REQUESTS = 200;
const BODY_INLINE_LIMIT = 5000;       // text body: inline 阈值（字符）
const BINARY_INLINE_LIMIT = 2048;     // binary body: inline 阈值（字节），base64 后 ~2730 字符

/**
 * Capture request body in structured format.
 * - Small text/binary → inline in report
 * - Large text/binary → write to file, return path
 *
 * Binary data is base64-encoded (inline or file).
 */
function captureBody(body, requestIndex) {
  if (body === null || body === undefined) return null;

  // String body (most common: JSON, form-urlencoded)
  if (typeof body === 'string') {
    if (body.length <= BODY_INLINE_LIMIT) {
      return { body_location: 'inline', body_text: body, body_encoding: 'text' };
    }
    // Large text → write file
    const filename = `body_${requestIndex}.txt`;
    writeBodyFile(filename, body);
    return {
      body_location: 'file',
      body_path: filename,
      body_encoding: 'text',
      byte_length: Buffer.byteLength(body, 'utf-8'),
    };
  }

  // Binary body detection (cross-vm-safe: check byteLength instead of instanceof)
  if (isArrayBufferLike(body) || isTypedArray(body)) {
    const bytes = toUint8Array(body);
    if (bytes) {
      if (bytes.length <= BINARY_INLINE_LIMIT) {
        // Small binary → inline base64
        const base64 = Buffer.from(bytes).toString('base64');
        return {
          body_location: 'inline',
          body_base64: base64,
          body_encoding: 'base64',
          byte_length: bytes.length,
        };
      }
      // Large binary → write file
      const filename = `body_${requestIndex}.bin`;
      writeBodyFile(filename, Buffer.from(bytes));
      return {
        body_location: 'file',
        body_path: filename,
        body_encoding: 'binary',
        byte_length: bytes.length,
      };
    }
  }

  // Fallback: stringify whatever it is
  const text = String(body);
  if (text.length <= BODY_INLINE_LIMIT) {
    return { body_location: 'inline', body_text: text, body_encoding: 'text' };
  }
  const filename = `body_${requestIndex}.txt`;
  writeBodyFile(filename, text);
  return {
    body_location: 'file',
    body_path: filename,
    body_encoding: 'text',
    byte_length: Buffer.byteLength(text, 'utf-8'),
  };
}

/** Write body to file next to the running script (CWD) */
function writeBodyFile(filename, content) {
  try {
    const fs = require('fs');
    const path = require('path');
    fs.writeFileSync(path.resolve(process.cwd(), filename), content);
  } catch (e) {
    // Non-fatal: if file write fails, body is lost but execution continues
  }
}

/** Cross-vm ArrayBuffer detection (instanceof fails across vm boundary) */
function isArrayBufferLike(obj) {
  return obj && typeof obj === 'object' &&
    typeof obj.byteLength === 'number' &&
    typeof obj.slice === 'function' &&
    !('buffer' in obj); // has byteLength+slice but no buffer → ArrayBuffer
}

/** Cross-vm TypedArray detection */
function isTypedArray(obj) {
  return obj && typeof obj === 'object' &&
    typeof obj.byteLength === 'number' &&
    typeof obj.byteOffset === 'number' &&
    'buffer' in obj; // has byteLength+byteOffset+buffer → TypedArray/DataView
}

/** Convert various binary types to Uint8Array */
function toUint8Array(body) {
  try {
    if (isArrayBufferLike(body)) {
      // ArrayBuffer → copy via host's Uint8Array
      return new Uint8Array(body.slice(0));
    }
    if (isTypedArray(body)) {
      // TypedArray → copy from its underlying buffer
      const buf = body.buffer.slice(body.byteOffset, body.byteOffset + body.byteLength);
      return new Uint8Array(buf);
    }
  } catch (e) {
    // If cross-vm copy fails, try direct iteration
    try {
      if (typeof body.length === 'number') {
        return new Uint8Array(Array.from(body));
      }
    } catch (e2) {}
  }
  return null;
}

/**
 * Install network recording on a jsdom VM context.
 * Replaces jsdom's XMLHttpRequest and fetch with recording stubs.
 *
 * @param {object} ctx - vm context
 * @param {object|null} recorder - access recorder (for monitor integration)
 * @param {object} markNativeHandle - { markNative }
 * @returns {object} - networkRecorder handle
 */
function installNetworkRecorder(ctx, recorder, markNativeHandle) {
  const { markNative } = markNativeHandle || { markNative: (fn) => fn };
  const requests = [];

  // WeakMap stores internal state per-instance (invisible to target script)
  const xhrState = new WeakMap();

  // --- XMLHttpRequest stub ---
  function XMLHttpRequest() {
    if (!(this instanceof XMLHttpRequest) && !new.target) {
      throw new TypeError("Failed to construct 'XMLHttpRequest': Please use the 'new' operator");
    }
    xhrState.set(this, { method: 'GET', url: '', headers: {}, async: true, listeners: {} });
    this.readyState = 0;
    this.status = 0;
    this.statusText = '';
    this.responseText = '';
    this.response = '';
    this.responseType = '';
    this.responseURL = '';
    this.responseXML = null;
    this.withCredentials = false;
    this.timeout = 0;
    this.upload = {
      addEventListener: markNative(function addEventListener() {}, 'addEventListener', 2),
      removeEventListener: markNative(function removeEventListener() {}, 'removeEventListener', 2),
      onabort: null,
      onerror: null,
      onload: null,
      onloadend: null,
      onloadstart: null,
      onprogress: null,
      ontimeout: null,
    };

    this.onreadystatechange = null;
    this.onload = null;
    this.onerror = null;
    this.onabort = null;
    this.ontimeout = null;
    this.onprogress = null;
    this.onloadstart = null;
    this.onloadend = null;
  }

  // CRITICAL: Prototype must be in vm context realm so Object.getPrototypeOf(XHR.prototype) === Object.prototype
  const ctxObjectProto = (ctx.Object || Object).prototype;
  XMLHttpRequest.prototype = Object.create(ctxObjectProto);
  Object.defineProperty(XMLHttpRequest.prototype, 'constructor', { value: XMLHttpRequest, writable: true, configurable: true });

  // Chrome does NOT set Symbol.toStringTag on XMLHttpRequest.prototype.
  // The [object XMLHttpRequest] string comes from internal V8 class name, not toStringTag.
  // However, in a vm context we have no class slot — Object.prototype.toString returns [object Object].
  // Trade-off: adding toStringTag fixes toString output (checked by most scripts) but
  // introduces 'Symbol.toStringTag in XHR.prototype === true' (rarely checked).
  // The toString check is overwhelmingly more common, so we add it.
  // MUST be after prototype replacement above.
  Object.defineProperty(XMLHttpRequest.prototype, Symbol.toStringTag, {
    value: 'XMLHttpRequest', configurable: true,
  });

  XMLHttpRequest.prototype.open = function(method, url, async) {
    const state = xhrState.get(this);
    if (state) {
      state.method = method || 'GET';
      state.url = url || '';
      state.async = async !== false;
    }
    this.readyState = 1;
  };
  markNative(XMLHttpRequest.prototype.open, 'open', 2);

  XMLHttpRequest.prototype.setRequestHeader = function(name, value) {
    const state = xhrState.get(this);
    if (state) state.headers[name] = value;
  };
  markNative(XMLHttpRequest.prototype.setRequestHeader, 'setRequestHeader');

  XMLHttpRequest.prototype.send = function(body) {
    const state = xhrState.get(this) || { method: 'GET', url: '', headers: {}, listeners: {} };
    const entry = {
      transport: 'xhr',
      method: state.method,
      url: state.url,
      headers: { ...state.headers },
      body: captureBody(body, requests.length),
      ts: Date.now(),
    };

    if (requests.length < MAX_REQUESTS) {
      requests.push(entry);
    }
    if (recorder) {
      recorder.call(`XMLHttpRequest.send(${state.url})`, body ? 1 : 0);
    }

    // Helper: create a minimal Event-like object for callbacks
    const self = this;
    function makeEvent(type) {
      return { type: type, target: self, currentTarget: self, loaded: 0, total: 0, lengthComputable: false, bubbles: false, cancelable: false, defaultPrevented: false, isTrusted: true, timeStamp: Date.now() };
    }

    function fireRSC() {
      const evt = makeEvent('readystatechange');
      if (self.onreadystatechange) { try { self.onreadystatechange(evt); } catch (e) {} }
      const rsListeners = state.listeners['readystatechange'] || [];
      for (let i = 0; i < rsListeners.length; i++) { try { rsListeners[i].call(self, evt); } catch (e) {} }
    }

    // Fire callbacks synchronously (VMP relies on immediate response availability).
    // Transition: readyState 2 (HEADERS_RECEIVED) → 3 (LOADING) → 4 (DONE)
    this.readyState = 2;
    fireRSC();
    this.readyState = 3;
    fireRSC();

    this.readyState = 4;
    this.status = 200;
    this.statusText = 'OK';
    this.responseText = '{}';
    this.response = '{}';
    this.responseURL = state.url;

    fireRSC();

    const loadEvt = makeEvent('load');
    if (this.onload) { try { this.onload(loadEvt); } catch (e) {} }
    const loadListeners = state.listeners['load'] || [];
    for (let i = 0; i < loadListeners.length; i++) { try { loadListeners[i].call(this, loadEvt); } catch (e) {} }

    const loadendEvt = makeEvent('loadend');
    if (this.onloadend) { try { this.onloadend(loadendEvt); } catch (e) {} }
    const loadendListeners = state.listeners['loadend'] || [];
    for (let i = 0; i < loadendListeners.length; i++) { try { loadendListeners[i].call(this, loadendEvt); } catch (e) {} }
  };
  markNative(XMLHttpRequest.prototype.send, 'send', 0);

  XMLHttpRequest.prototype.abort = function() { this.readyState = 0; };
  XMLHttpRequest.prototype.getResponseHeader = function(name) {
    const headers = {
      'content-type': 'application/json; charset=utf-8',
      'content-length': '2',
      'cache-control': 'no-cache, no-store',
      'date': new Date().toUTCString(),
    };
    return headers[(name || '').toLowerCase()] || null;
  };
  XMLHttpRequest.prototype.getAllResponseHeaders = function() {
    return 'content-type: application/json; charset=utf-8\r\ncontent-length: 2\r\ncache-control: no-cache, no-store\r\ndate: ' + new Date().toUTCString() + '\r\n';
  };
  XMLHttpRequest.prototype.overrideMimeType = function(mime) {};
  XMLHttpRequest.prototype.dispatchEvent = function(event) { return true; };
  XMLHttpRequest.prototype.addEventListener = function(type, listener) {
    if (typeof listener !== 'function') return;
    const state = xhrState.get(this);
    if (!state) return;
    if (!state.listeners[type]) state.listeners[type] = [];
    state.listeners[type].push(listener);
  };
  XMLHttpRequest.prototype.removeEventListener = function(type, listener) {
    const state = xhrState.get(this);
    if (!state || !state.listeners[type]) return;
    const idx = state.listeners[type].indexOf(listener);
    if (idx !== -1) state.listeners[type].splice(idx, 1);
  };

  markNative(XMLHttpRequest.prototype.abort, 'abort', 0);
  markNative(XMLHttpRequest.prototype.getResponseHeader, 'getResponseHeader', 1);
  markNative(XMLHttpRequest.prototype.getAllResponseHeaders, 'getAllResponseHeaders', 0);
  markNative(XMLHttpRequest.prototype.overrideMimeType, 'overrideMimeType', 1);
  markNative(XMLHttpRequest.prototype.dispatchEvent, 'dispatchEvent', 1);
  markNative(XMLHttpRequest.prototype.addEventListener, 'addEventListener', 2);
  markNative(XMLHttpRequest.prototype.removeEventListener, 'removeEventListener', 2);
  markNative(XMLHttpRequest, 'XMLHttpRequest');

  // XHR state constants (non-writable, enumerable, non-configurable — matches Chrome)
  const xhrConstants = { UNSENT: 0, OPENED: 1, HEADERS_RECEIVED: 2, LOADING: 3, DONE: 4 };
  for (const [name, value] of Object.entries(xhrConstants)) {
    Object.defineProperty(XMLHttpRequest, name, { value, writable: false, enumerable: true, configurable: false });
    Object.defineProperty(XMLHttpRequest.prototype, name, { value, writable: false, enumerable: true, configurable: false });
  }

  ctx.XMLHttpRequest = XMLHttpRequest;

  // --- fetch stub ---
  function fetch(url, options) {
    const method = (options && options.method) || 'GET';
    const body = options && options.body;
    const headers = (options && options.headers) || {};

    const resolvedUrl = typeof url === 'string' ? url : (url && url.url) || String(url);

    // Real browsers reject non-HTTP(S) URLs with TypeError (detection vector!)
    // Chrome error format: "Failed to execute 'fetch' on 'Window': URL scheme "ftp" is not supported."
    const lowerUrl = resolvedUrl.toLowerCase();
    if (lowerUrl && !lowerUrl.startsWith('http:') && !lowerUrl.startsWith('https:') && !lowerUrl.startsWith('/') && !lowerUrl.startsWith('.')) {
      const entry = {
        transport: 'fetch',
        method,
        url: resolvedUrl,
        headers: typeof headers === 'object' && !(headers instanceof ctx.Headers) ? { ...headers } : {},
        body: captureBody(body, requests.length),
        ts: Date.now(),
        rejected: true,
      };
      if (requests.length < MAX_REQUESTS) requests.push(entry);
      // Extract scheme for Chrome-matching error message
      const colonIdx = resolvedUrl.indexOf(':');
      const scheme = colonIdx > 0 ? resolvedUrl.substring(0, colonIdx) : resolvedUrl;
      return Promise.reject(new (ctx.TypeError || TypeError)(
        `Failed to execute 'fetch' on 'Window': URL scheme "${scheme}" is not supported.`
      ));
    }

    const entry = {
      transport: 'fetch',
      method,
      url: resolvedUrl,
      headers: typeof headers === 'object' && !(headers instanceof ctx.Headers) ? { ...headers } : {},
      body: captureBody(body, requests.length),
      ts: Date.now(),
    };

    if (requests.length < MAX_REQUESTS) {
      requests.push(entry);
    }
    if (recorder) {
      recorder.call(`fetch(${resolvedUrl})`, body ? 1 : 0);
    }

    // Return mock Response using context's Response constructor (correct instanceof)
    // Body is '{}' so json() returns {} without throwing (prevents unnecessary script crashes)
    let mockResponse;
    try {
      mockResponse = new ctx.Response('{}', {
        status: 200,
        statusText: 'OK',
        headers: new ctx.Headers({ 'content-type': 'application/json' }),
      });
    } catch (e) {
      // Fallback: plain object if Response constructor unavailable
      mockResponse = {
        ok: true, status: 200, statusText: 'OK', url: resolvedUrl,
        headers: new (ctx.Headers || Map)(), redirected: false, type: 'basic',
        body: null, bodyUsed: false,
        text() { return Promise.resolve(''); },
        json() { return Promise.resolve({}); },
        arrayBuffer() { return Promise.resolve(new ArrayBuffer(0)); },
        blob() { return Promise.resolve(new (ctx.Blob || Object)()); },
        clone() { return this; },
      };
    }
    return Promise.resolve(mockResponse);
  }
  markNative(fetch, 'fetch', 1);
  // Chrome: 'prototype' in fetch === false. Normal functions have non-configurable prototype.
  // Workaround: replace with a bound version (bound functions have no .prototype property).
  const boundFetch = fetch.bind(undefined);
  markNative(boundFetch, 'fetch', 1);
  ctx.fetch = boundFetch;

  // --- Navigator.sendBeacon stub ---
  // Chrome defines sendBeacon on Navigator.prototype (not the instance)
  if (ctx.navigator) {
    const navProto = Object.getPrototypeOf(ctx.navigator);
    const sendBeaconTarget = navProto || ctx.navigator;
    const sendBeacon = function sendBeacon(url, data) {
      const entry = {
        transport: 'beacon',
        method: 'POST',
        url: typeof url === 'string' ? url : String(url),
        headers: { 'Content-Type': 'text/plain' },
        body: captureBody(data, requests.length),
        ts: Date.now(),
      };
      if (requests.length < MAX_REQUESTS) requests.push(entry);
      return true;
    };
    markNative(sendBeacon, 'sendBeacon');
    Object.defineProperty(sendBeaconTarget, 'sendBeacon', {
      value: sendBeacon,
      writable: true,
      enumerable: true,
      configurable: true,
    });
  }

  // --- Public API ---
  const networkRecorder = {
    getAll() { return requests; },
    clear() { requests.length = 0; },
  };

  return networkRecorder;
}

module.exports = { installNetworkRecorder };
