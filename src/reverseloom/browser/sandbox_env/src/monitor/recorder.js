'use strict';

/**
 * recorder.js — Access recorder for the monitor layer.
 *
 * Records all reads, writes, calls, missing property accesses.
 * Deduplicates and summarizes for the report.
 */

const MAX_ENTRIES = 2000;

function createRecorder() {
  const reads = [];
  const writes = [];
  const calls = [];
  const missing = [];
  const has_checks = [];

  let totalReads = 0;
  let totalWrites = 0;
  let totalCalls = 0;
  let totalHasChecks = 0;

  function read(path, type) {
    totalReads++;
    if (reads.length < MAX_ENTRIES) {
      reads.push({ path, type });
    }
  }

  function write(path, type) {
    totalWrites++;
    if (writes.length < MAX_ENTRIES) {
      writes.push({ path, type });
    }
  }

  function call(path, argCount) {
    totalCalls++;
    if (calls.length < MAX_ENTRIES) {
      calls.push({ path, argCount });
    }
  }

  function missingAccess(path, type, depth, argCount) {
    if (missing.length < MAX_ENTRIES) {
      const entry = { path, type: type || 'property' };
      if (depth !== undefined) entry.depth = depth;
      if (argCount !== undefined) entry.argCount = argCount;
      // Capture stack trace — find first user-script frame (skip internal sandbox frames)
      try {
        throw new Error();
      } catch (e) {
        const lines = (e.stack || '').split('\n');
        // Skip first line ("Error") and internal frames (proxy-factory, recorder, monitor)
        for (let i = 1; i < lines.length; i++) {
          const line = lines[i];
          if (line.includes('proxy-factory') || line.includes('recorder') ||
              line.includes('monitor/') || line.includes('monitor\\') ||
              line.includes('node_modules')) continue;
          // First non-internal frame is the caller
          entry.stack = line.trim();
          break;
        }
      }
      missing.push(entry);
    }
  }

  function hasCheck(path) {
    totalHasChecks++;
    if (has_checks.length < MAX_ENTRIES) {
      has_checks.push({ path });
    }
  }

  function summarize() {
    return {
      totalReads,
      totalWrites,
      totalCalls,
      totalHasChecks,
      missing,
      calls,
      reads: reads.slice(0, 100),
      writes: writes.slice(0, 100),
      hasChecks: has_checks.slice(0, 100),
    };
  }

  function reset() {
    reads.length = 0;
    writes.length = 0;
    calls.length = 0;
    missing.length = 0;
    has_checks.length = 0;
    totalReads = 0;
    totalWrites = 0;
    totalCalls = 0;
    totalHasChecks = 0;
  }

  return {
    read,
    write,
    call,
    missingAccess,
    hasCheck,
    summarize,
    reset,
  };
}

module.exports = { createRecorder };
