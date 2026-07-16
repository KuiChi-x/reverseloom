'use strict';

/**
 * report/reporter.js — Assembles the final report.
 * Designed for AI consumption: "what's missing, what to patch next."
 */

/**
 * Build the final report from monitor data and execution results.
 * @param {object} params
 * @returns {object} - Report JSON
 */
function buildReport({ monitorHandle, networkRecorder, cookieTrap, result, blockingError }) {
  const report = { ok: !blockingError, result: result !== undefined ? result : null };

  // Blocking error
  if (blockingError) {
    report.blocking_error = {
      message: blockingError.message,
      stack: blockingError.stack,
      caused_by: blockingError.caused_by,
    };
  }

  // Todo list from monitor (Deep Proxy data)
  if (monitorHandle) {
    const monitorReport = monitorHandle.getReport();
    report.todo = buildTodoList(monitorReport, blockingError);
    report.trace = buildTrace(monitorReport);
  } else {
    report.todo = [];
    report.trace = { total_reads: 0, total_writes: 0, total_calls: 0, total_has_checks: 0 };
  }

  // Network requests
  if (networkRecorder) {
    const requests = networkRecorder.getAll();
    if (requests.length) {
      report.network = requests;
    }
  }

  // Cookies
  if (cookieTrap) {
    const cookies = cookieTrap.getAll();
    const cookieWrites = cookieTrap.getWritten();
    if (Object.keys(cookies).length) report.cookies = cookies;
    if (cookieWrites.length) report.cookie_writes = cookieWrites;
  }

  return report;
}

/**
 * Determine if a missing access should appear in the todo list.
 * General behavioral rules — NO hardcoded names:
 *
 * 1. has_check type → target did `'X' in obj` → it handles absence → skip
 * 2. window.X (single-level global) → if code didn't crash, it doesn't need it → skip
 * 3. Deep object misses (navigator.X, screen.X, chrome.X, etc.) → show (AI evaluates)
 * 4. Blocking errors → always show (handled separately in buildTodoList)
 */
function shouldShowInTodo(item) {
  // has_check = target was explicitly probing presence (`'X' in obj`)
  // It handles both cases — don't tell AI to define it
  if (item.type === 'has_check') return false;

  // Single-level window global (e.g. window.someVar):
  // If code continued without crashing, it doesn't depend on this value.
  // Covers: bot probes, feature detection, cross-browser compat checks.
  const parts = item.path.split('.');
  if (parts[0] === 'window' && parts.length === 2) return false;

  // Everything else: misses on known Chrome objects (navigator.X, screen.X,
  // chrome.X, performance.X, document.X, location.X, or deeper paths).
  // These are APIs we've modeled — a miss here is likely a real gap.
  return true;
}

/**
 * Convert recorder's missing/accessed data into actionable todo items.
 */
function buildTodoList(monitorReport, blockingError) {
  const todos = [];
  const { missing } = monitorReport;

  // Group missing by path, deduplicate, sort by access count
  const pathMap = new Map();
  for (const entry of missing) {
    const key = entry.path;
    if (!pathMap.has(key)) {
      pathMap.set(key, { ...entry, count: 1 });
    } else {
      pathMap.get(key).count++;
    }
  }

  // Convert to todo items, sorted by count descending
  const sorted = [...pathMap.values()].sort((a, b) => b.count - a.count);

  for (const item of sorted) {
    // Behavioral filter: skip noise, show real gaps
    if (!shouldShowInTodo(item)) continue;

    const todo = {
      action: 'define',
      path: item.path,
      reason: `accessed ${item.count}x, expected ${item.type || 'unknown'}`,
      expected: inferExpectedType(item, blockingError),
    };
    if (item.stack) todo.stack = item.stack;
    if (item.argCount !== undefined) {
      todo.reason += ` (${item.argCount} args)`;
      todo.action = 'define_function';
    }
    todos.push(todo);
  }

  // If blocking error points to a missing path, ensure it's first with blocking flag
  if (blockingError && blockingError.caused_by) {
    const blockingPath = extractPathFromCause(blockingError.caused_by);
    if (blockingPath) {
      const idx = todos.findIndex(t => t.path === blockingPath);
      if (idx >= 0) {
        const [item] = todos.splice(idx, 1);
        item.blocking = true;
        todos.unshift(item);
      } else {
        todos.unshift({
          action: 'define',
          path: blockingPath,
          reason: `blocking error: ${blockingError.message}`,
          expected: inferExpectedType({ path: blockingPath, type: 'property' }, blockingError),
          blocking: true,
        });
      }
    }
  }

  return todos;
}

function buildTrace(monitorReport) {
  return {
    total_reads: monitorReport.totalReads || 0,
    total_writes: monitorReport.totalWrites || 0,
    total_calls: monitorReport.totalCalls || 0,
    total_has_checks: monitorReport.totalHasChecks || 0,
    has_checks: monitorReport.hasChecks || [],
  };
}

/**
 * Infer what type the AI should return for a missing property.
 */
function inferExpectedType(item, blockingError) {
  // From blocking error message
  if (blockingError && blockingError.message) {
    const msg = blockingError.message;
    if (msg.includes('is not a function')) return 'function';
    if (msg.match(/Cannot read propert/)) {
      const m = msg.match(/reading '([^']+)'/);
      return m ? `object (needs .${m[1]})` : 'object';
    }
    if (msg.includes('is not a constructor')) return 'constructor';
  }
  // From access pattern
  if (item.type === 'has_check') return 'truthy (feature gate)';
  if (item.argCount !== undefined) return `function (${item.argCount} args)`;
  // From path heuristics
  const last = item.path.split('.').pop();
  if (/^(get|set|add|remove|request|query|open|close|start|stop|create)/.test(last)) return 'function';
  if (/^on[A-Z]/.test(last)) return 'null (event handler)';
  if (/^[A-Z]/.test(last)) return 'constructor or object';
  return 'unknown';
}

/**
 * Extract path from error cause message.
 * Handles both formats:
 *   - "X is not defined" / "X is not defined in the context"
 *   - "undefined object accessed for property 'X'" (our inferCause format)
 *   - Chrome: "Cannot read properties of undefined (reading 'X')"
 */
function extractPathFromCause(cause) {
  // Direct "X is not defined"
  const match = cause.match(/([\w.]+) is (not defined|undefined)/);
  if (match) return match[1];
  // Chrome TypeError: "undefined object accessed for property 'X'"
  const propMatch = cause.match(/accessed for property '([\w.]+)'/);
  if (propMatch) return propMatch[1];
  return null;
}

module.exports = { buildReport };
