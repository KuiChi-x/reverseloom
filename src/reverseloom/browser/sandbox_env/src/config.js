'use strict';

/**
 * config.js — Input validation and defaults for reverseloom-sandbox v2.
 * Parses stdin JSON, validates required fields, applies defaults.
 */

const DEFAULTS = {
  url: 'https://localhost',
  fingerprint: {},
  patches: '',
  call: { code: '', wait_ms: 500 },
  monitor: true,
};

function parseAndValidate(raw) {
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch (e) {
    return { ok: false, error: `Invalid JSON input: ${e.message}` };
  }

  if (!payload || typeof payload !== 'object') {
    return { ok: false, error: 'Input must be a JSON object' };
  }

  const { script_path, script_content } = payload;

  // Must have either script_path or script_content
  if (!script_path && !script_content) {
    return { ok: false, error: 'Must provide either script_path or script_content' };
  }

  const config = {
    script_path: script_path || null,
    script_content: script_content || null,
    url: payload.url || DEFAULTS.url,
    script_url: typeof payload.script_url === 'string' ? payload.script_url : null,
    fingerprint: payload.fingerprint && typeof payload.fingerprint === 'object'
      ? payload.fingerprint : DEFAULTS.fingerprint,
    patches: typeof payload.patches === 'string' ? payload.patches : DEFAULTS.patches,
    call: {
      code: (payload.call && typeof payload.call.code === 'string') ? payload.call.code : DEFAULTS.call.code,
      wait_ms: (payload.call && typeof payload.call.wait_ms === 'number') ? payload.call.wait_ms : DEFAULTS.call.wait_ms,
    },
    monitor: payload.monitor !== undefined ? Boolean(payload.monitor) : DEFAULTS.monitor,
  };

  return { ok: true, config };
}

module.exports = { parseAndValidate, DEFAULTS };
