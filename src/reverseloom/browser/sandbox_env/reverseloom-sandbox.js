'use strict';

/**
 * reverseloom-sandbox.js — Entry point.
 * Reads JSON from stdin, runs the sandbox engine, writes JSON to stdout.
 */

const fs = require('fs');
const { parseAndValidate } = require('./src/config');
const { run } = require('./src/engine');

async function main() {
  // Read stdin
  let input = '';
  try {
    input = fs.readFileSync(0, 'utf-8');
  } catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: `Failed to read stdin: ${e.message}` }));
    process.exit(1);
  }

  // Parse and validate
  const parsed = parseAndValidate(input);
  if (!parsed.ok) {
    process.stdout.write(JSON.stringify({ ok: false, error: parsed.error }));
    process.exit(1);
  }

  const config = parsed.config;

  // Resolve script content
  if (!config.script_content) {
    try {
      config.script_content = fs.readFileSync(config.script_path, 'utf-8');
    } catch (e) {
      process.stdout.write(JSON.stringify({ ok: false, error: `Cannot read script_path: ${e.message}` }));
      process.exit(1);
    }
  }

  // Run engine
  try {
    const report = await run(config);
    process.stdout.write(JSON.stringify(report));
    process.exit(0);  // Force exit — jsdom timers (setInterval from VMP) keep event loop alive
  } catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: `Engine crash: ${e.message}`, stack: e.stack }));
    process.exit(1);
  }
}

main();
