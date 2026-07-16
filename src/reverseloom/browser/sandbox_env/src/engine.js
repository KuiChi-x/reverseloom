'use strict';

/**
 * engine.js — VM execution engine using jsdom.
 *
 * Creates a jsdom environment, applies anti-detection armor,
 * installs monitoring, and executes user scripts with clean error capture.
 *
 * KEY INSIGHT: Error stacks must be captured INSIDE the vm context.
 * When errors cross the vm boundary, Node.js prepends the full source line
 * (200KB+ for minified code). By wrapping execution in vm-internal try/catch,
 * we get clean stacks from prepareStackTrace.
 */

const vm = require('vm');
const { JSDOM, VirtualConsole } = require('jsdom');
const { installArmor } = require('./armor/index');
const { scanAllPrototypes } = require('./armor/mark-native');
const { installMonitor } = require('./monitor/index');
const { installNetworkRecorder } = require('./monitor/network-recorder');
const { installCookieTrap } = require('./monitor/cookie-trap');
const { buildReport } = require('./report/reporter');

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * prepareStackTrace code to inject into the vm context.
 * Produces compact "at func (file:line:col)" format.
 * WHITELIST approach: only keep frames from user scripts.
 *
 * CRITICAL: Must be a normal data property (writable, configurable) like real Chrome.
 * Target scripts CAN overwrite it — we intercept by re-applying after target runs.
 * We do NOT make it non-configurable (trivial detection vector).
 */
const PREPARE_STACK_TRACE_CODE = `
(function() {
  Error.prepareStackTrace = function(error, frames) {
    var header = (error.name || 'Error') + ': ' + error.message;
    var lines = [];
    for (var i = 0; i < Math.min(frames.length, 20); i++) {
      var frame = frames[i];
      var file = frame.getFileName() || '';
      var keep = (file.indexOf('http') === 0) || file.indexOf('patches.js') !== -1 || file.indexOf('call.js') !== -1;
      if (!keep) continue;
      var fn = frame.getFunctionName() || frame.getMethodName() || '<anonymous>';
      var line = frame.getLineNumber() || 0;
      var col = frame.getColumnNumber() || 0;
      lines.push('    at ' + fn + ' (' + file + ':' + line + ':' + col + ')');
    }
    return header + (lines.length ? '\\n' + lines.join('\\n') : '');
  };
  Error.stackTraceLimit = 20;
})();
`;

/**
 * Run code inside vm with error captured INTERNALLY (clean stack).
 * Uses vm.Script to preserve filename in stack traces.
 * Returns error via wrapper return value — never sets properties on window.
 */
function safeRunInContext(code, ctx, options) {
  const { filename, timeout } = options;

  // Wrapper returns null on success, error object on failure
  // CRITICAL: wrapper must be on line 1 to avoid line-number offset in stack traces.
  // Target code starts at line 1 (same position as a real <script> load).
  const wrappedCode = `(function(){try{${code}\nreturn null}catch(e){return{message:e.message,stack:e.stack,name:e.name}}})();`;

  try {
    const script = new vm.Script(wrappedCode, { filename });
    const result = script.runInContext(ctx, { timeout: timeout || 30000 });
    if (result) {
      return { ok: false, error: result };
    }
    return { ok: true };
  } catch (e) {
    // Only reaches here if the WRAPPER itself fails (syntax error, timeout)
    return {
      ok: false,
      error: { message: e.message, stack: e.stack || '', name: e.name },
    };
  }
}

/**
 * Run async code inside vm with error captured INTERNALLY.
 * Returns result/error via Promise resolution — no globals set on window.
 */
async function safeRunAsyncInContext(code, ctx, options) {
  const { filename, timeout } = options;

  const wrappedCode = `
(async function() {
  try {
    var __result__ = await (async function() { ${code} })();
    return { ok: true, result: __result__ };
  } catch(e) {
    return { ok: false, error: { message: e.message, stack: e.stack, name: e.name } };
  }
})()
`;

  try {
    const script = new vm.Script(wrappedCode, { filename });
    const outcome = await script.runInContext(ctx, { timeout: timeout || 30000 });
    if (outcome && !outcome.ok) {
      return { ok: false, error: outcome.error };
    }
    return { ok: true, result: outcome ? outcome.result : undefined };
  } catch (e) {
    return {
      ok: false,
      error: { message: e.message, stack: e.stack || '', name: e.name },
    };
  }
}

/**
 * Run the sandbox lifecycle.
 * @param {object} config - Validated config from config.js
 * @returns {object} - Report JSON
 */
async function run(config) {
  const {
    url,
    script_url,
    fingerprint,
    patches,
    call,
    monitor,
    script_content,
  } = config;

  // Phase 1: Create jsdom environment (complete DOM, correct prototypes)
  const virtualConsole = new VirtualConsole();  // swallow "Not implemented" warnings
  const dom = new JSDOM('<!DOCTYPE html><html><head></head><body></body></html>', {
    url: url || 'https://localhost/',
    referrer: url || '',
    contentType: 'text/html',
    pretendToBeVisual: true,
    runScripts: 'outside-only',
    virtualConsole,
  });

  const ctx = dom.getInternalVMContext();

  // Phase 2: Install prepareStackTrace (before anything else runs code)
  vm.runInContext(PREPARE_STACK_TRACE_CODE, ctx);

  // Phase 3: Install anti-detection armor
  const { markNativeHandle } = installArmor(ctx, { fingerprint });
  const { markNative: mn } = markNativeHandle;

  // Phase 3b: Wrap setTimeout/setInterval to return positive integers (Chrome behavior)
  // Node.js returns Timeout objects; real browsers return numeric IDs.
  {
    // Chrome timer IDs don't start at 1 — they start at a random-ish positive integer
    let _timerId = Math.floor(Math.random() * 50) + 2;
    const _timerMap = new Map();
    const origSetTimeout = ctx.setTimeout;
    const origSetInterval = ctx.setInterval;
    const origClearTimeout = ctx.clearTimeout;
    const origClearInterval = ctx.clearInterval;

    // Chrome: setTimeout.length === 1, setInterval.length === 1
    // Chrome: extra args after delay are passed to the callback
    ctx.setTimeout = mn(function setTimeout(fn) {
      const id = ++_timerId;
      const args = arguments.length > 2 ? Array.prototype.slice.call(arguments, 2) : [];
      const ms = arguments[1];
      const wrappedFn = args.length > 0 ? function() { fn.apply(null, args); } : fn;
      const handle = origSetTimeout.call(null, wrappedFn, ms);
      _timerMap.set(id, handle);
      return id;
    }, 'setTimeout', 1);

    ctx.setInterval = mn(function setInterval(fn) {
      const id = ++_timerId;
      const args = arguments.length > 2 ? Array.prototype.slice.call(arguments, 2) : [];
      const ms = arguments[1];
      const wrappedFn = args.length > 0 ? function() { fn.apply(null, args); } : fn;
      const handle = origSetInterval.call(null, wrappedFn, ms);
      _timerMap.set(id, handle);
      return id;
    }, 'setInterval', 1);

    // Chrome: clearTimeout.length === 0, clearInterval.length === 0
    ctx.clearTimeout = mn(function clearTimeout() {
      const id = arguments[0];
      const handle = _timerMap.get(id);
      if (handle !== undefined) {
        origClearTimeout.call(null, handle);
        _timerMap.delete(id);
      } else {
        origClearTimeout.call(null, id);
      }
    }, 'clearTimeout', 0);

    ctx.clearInterval = mn(function clearInterval() {
      const id = arguments[0];
      const handle = _timerMap.get(id);
      if (handle !== undefined) {
        origClearInterval.call(null, handle);
        _timerMap.delete(id);
      } else {
        origClearInterval.call(null, id);
      }
    }, 'clearInterval', 0);

    // Chrome: native timer functions have no .prototype property
    // 'prototype' in setTimeout === false in real Chrome.
    // Function.prototype is configurable:false, so we use bind() to produce prototype-less copies.
    const timerFuncs = ['setTimeout', 'setInterval', 'clearTimeout', 'clearInterval'];
    for (const tfn of timerFuncs) {
      const orig = ctx[tfn];
      const bound = orig.bind(undefined);
      mn(bound, tfn, orig.length);
      ctx[tfn] = bound;
    }
  }

  // Phase 3c: Re-install prepareStackTrace after armor (armor may have touched Error).
  // Target scripts CAN overwrite it (Chrome behavior), but we re-apply after target runs.
  vm.runInContext(PREPARE_STACK_TRACE_CODE, ctx);

  // Set document.currentScript.src to the script URL (VMP reads this to derive endpoints)
  const { getScriptElement } = require('./armor/browser-apis');
  const scriptEl = getScriptElement(ctx);
  if (scriptEl) {
    scriptEl.setAttribute('src', script_url || url);
  }

  // Phase 4: Install network recorder + cookie trap (always active)
  const networkRecorder = installNetworkRecorder(ctx, null, markNativeHandle);
  const cookieTrap = installCookieTrap(ctx, null, markNativeHandle);

  // Phase 4b: First prototype scan — marks all existing DOM functions as native
  scanAllPrototypes(ctx, mn);

  // Phase 5: Install monitor (Deep Proxy + Phantom Chain) if enabled
  let monitorHandle = null;
  if (monitor) {
    monitorHandle = installMonitor(ctx, markNativeHandle.markNative);
  }

  // Expose markNative to patches via a closure variable (NOT on window)
  // Patches can access it because we inject it as a local var in the wrapper
  const markNativeForPatches = markNativeHandle.markNative;

  // Phase 6: Execute user patches (markNative injected as local, invisible to target)
  if (patches && patches.trim()) {
    const patchWrapper = `(function(markNative) {\n${patches}\n})`;
    try {
      const script = new vm.Script(patchWrapper, { filename: 'patches.js' });
      const fn = script.runInContext(ctx, { timeout: 10000 });
      fn(markNativeForPatches);
    } catch (e) {
      return {
        ok: false,
        error: `patches threw: ${e.message}`,
        stack: e.stack || '',
      };
    }
  }

  // Phase 6b: Final prototype scan — catches anything patches added
  scanAllPrototypes(ctx, mn);

  // Phase 6c: Re-install prepareStackTrace before target execution.
  // Patches may have touched Error; this ensures clean stacks for target.
  // CRITICAL: Chrome does NOT expose Error.prepareStackTrace (it's V8/Node-only).
  // We install it to get clean stacks, but must delete it before target can probe.
  vm.runInContext(PREPARE_STACK_TRACE_CODE, ctx);
  // Delete the user-visible property — V8 still uses the last-set value internally
  // for stack formatting, even after the property is deleted from user space.
  vm.runInContext('delete Error.prepareStackTrace;', ctx);

  // Phase 6d: Hide jsdom internal _ props from `in` operator.
  // Cannot delete them (jsdom uses _globalObject for event dispatch async).
  // The jsdom-hider already hides them from enumeration/getOwnPropertyNames/getOwnPropertyDescriptor.
  // Direct access via `window._globalObject` remains but requires knowing the exact property name.

  // Phase 6e: Override document.readyState to 'complete'.
  // Must be AFTER jsdom finishes internal initialization (fires DOMContentLoaded etc.)
  // Define on Document.prototype so document.hasOwnProperty('readyState') === false.
  if (ctx.document) {
    const docProto = Object.getPrototypeOf(ctx.document);
    const readyTarget = docProto || ctx.document;
    Object.defineProperty(readyTarget, 'readyState', {
      get: mn(function() { return 'complete'; }, 'get readyState'),
      enumerable: true, configurable: true,
    });
  }

  // Phase 7: Execute target script
  // Strip debugger STATEMENTS at source level (safer than proxying Function/eval)
  // Only match `debugger` as a standalone statement — not inside string/regex literals.
  // Strategy: replace strings, template literals, and regex literals with placeholders,
  // strip debugger statements, then restore.
  let cleanedContent = script_content;
  {
    const tokens = [];
    // Preserve string literals, template literals, AND regex literals
    // Regex literal heuristic: /.../ preceded by operator context (not division)
    const preserved = cleanedContent.replace(
      /("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`|\/(?![*/])(?:[^/\\]|\\.)+\/[gimsuy]*)/g,
      function(m) {
        tokens.push(m);
        return '\x00TOK' + (tokens.length - 1) + '\x00';
      }
    );
    // Now safely strip standalone debugger statements
    const stripped = preserved.replace(/\bdebugger\b\s*;?/g, '         ');
    // Restore tokens
    cleanedContent = stripped.replace(/\x00TOK(\d+)\x00/g, function(_, i) {
      return tokens[parseInt(i)];
    });
  }
  // Use script_url as filename so Error stacks show real URL (anti-detection)
  const targetFilename = script_url || url || 'target.js';
  const execResult = safeRunInContext(cleanedContent, ctx, { filename: targetFilename, timeout: 30000 });

  // After script execution: null out document.currentScript (matches real Chrome behavior)
  // In real browsers, currentScript is only non-null during synchronous script execution.
  // In async contexts (setTimeout, Promise, rAF), it must be null — detection vector!
  const nullCurrentScriptGetter = mn(function() { return null; }, 'get currentScript');
  Object.defineProperty(ctx.document, 'currentScript', {
    get: nullCurrentScriptGetter,
    enumerable: true,
    configurable: true,
  });

  if (!execResult.ok) {
    const report = buildReport({
      monitorHandle,
      networkRecorder,
      cookieTrap,
      blockingError: {
        message: execResult.error.message,
        stack: execResult.error.stack,
        caused_by: inferCause(execResult.error),
      },
    });
    return report;
  }

  // Flush pending async callbacks (XHR responses, setTimeout(0), etc.)
  // This ensures XHR callbacks scheduled during script execution fire before call.code
  await new Promise(resolve => setTimeout(resolve, 10));

  // Phase 8: Execute call.code + wait
  let callResult = null;
  let callError = null;
  if (call.code && call.code.trim()) {
    const callExec = await safeRunAsyncInContext(call.code, ctx, { filename: 'call.js', timeout: 30000 });
    if (!callExec.ok) {
      callError = {
        message: callExec.error.message,
        stack: callExec.error.stack,
        caused_by: inferCause(callExec.error),
      };
    } else {
      callResult = callExec.result;
    }
  }

  await sleep(Number(call.wait_ms) || 500);

  // Phase 9: Build report
  const report = buildReport({
    monitorHandle,
    networkRecorder,
    cookieTrap,
    result: callResult,
    blockingError: callError,
  });

  return report;
}

/**
 * Infer the root cause of an error from its message.
 */
function inferCause(err) {
  const msg = err.message || '';
  const match = msg.match(/Cannot read propert(?:y|ies) of (undefined|null) \(reading '([^']+)'\)/);
  if (match) {
    return `${match[1]} object accessed for property '${match[2]}'`;
  }
  const fnMatch = msg.match(/(.+) is not a function/);
  if (fnMatch) {
    return `${fnMatch[1]} is not defined as a function`;
  }
  const defMatch = msg.match(/(.+) is not defined/);
  if (defMatch) {
    return `${defMatch[1]} is not defined in the context`;
  }
  return msg;
}

module.exports = { run };
