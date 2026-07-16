'use strict';

/**
 * node-hider.js — Remove all traces of Node.js from the VM context.
 *
 * In a real browser:
 *   typeof process → "undefined"
 *   process → ReferenceError: process is not defined
 *
 * In jsdom vm context after deletion:
 *   typeof process → "undefined" (V8 scope lookup finds nothing)
 *   process → ReferenceError (V8 throws naturally for undeclared names)
 *
 * We just need to DELETE these — V8 handles the rest correctly.
 * Using a getter that throws would break `typeof` (V8 invokes getters for typeof).
 */

function installNodeHider(ctx) {
  const nodeGlobals = [
    'process', 'global', 'require', 'module', 'exports',
    '__dirname', '__filename', 'Buffer', 'setImmediate',
    'clearImmediate', 'GLOBAL', 'root',
  ];

  for (const name of nodeGlobals) {
    try { delete ctx[name]; } catch (e) {}
  }

  // SharedArrayBuffer: present in Node.js but absent in Chrome pages without
  // cross-origin isolation (COOP/COEP headers). Most websites don't have these
  // headers, so SAB being defined is a Node.js tell.
  try { delete ctx.SharedArrayBuffer; } catch (e) {}
}

module.exports = { installNodeHider };
