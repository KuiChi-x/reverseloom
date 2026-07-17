# Reference: Node/JSDOM Sandbox Reproduction

## Contents

- [Load Trigger](#load-trigger)
- [Runtime Contract](#runtime-contract)
- [Payload Shape](#payload-shape)
- [`patches` vs `call.code`](#patches-vs-callcode---its-a-timing-split)
- [Coverage Loop](#coverage-loop)
- [Report Shape](#report-shape-success)
- [Failure-Mode Catalogue](#failure-mode-catalogue)
- [Operational Notes](#operational-notes)

> **Entry point:** load from the DEEP_REVERSE triage Step 3 after you have the
> generator's entry (`window.X(args)` or module export) and input/output
> shape documented from Path A / Path C dump.
>
> **Goal:** make the sandbox reproduce the exact browser branch that generates
> the required value and extract it.
>
> **Non-goal:** pre-patch every browser API. Patch only the first blocker
> observed on each iteration.

---

## Load Trigger

Read and apply this reference only when ALL are true:

- At least one required param, header, cookie, or body must be generated at runtime.
- Static rewrite is ruled out (VMP, WASM, env-dependent, closure-bound, or
  three I/O samples unobtainable — see DEEP_REVERSE `<static_rewrite_rules>`).
- The generator's entry and its input/output shape are already documented
  from Path A breakpoint analysis or Path C `dump_runtime_asset`.

If you cannot name the generator and its call shape, go back to the
DEEP_REVERSE triage before running the sandbox.

---

## Runtime Contract

Invoke the bundle as a subprocess and send the payload as a single JSON line on
stdin. The **last non-empty stdout line** is the JSON report; earlier lines are
diagnostic logs (ignore them).

Mount the verified engine by listing it in `run_shell(runtime_files=[...])`: the
exact name `reverseloom-sandbox.bundle.js` is copied next to your wrapper and
registered so it travels with the delivery automatically. List every other
dependency (dumped scripts, WASM, `browser_fingerprint.json`) the same way, and
reference each by its relative filename — never hand-roll a jsdom setup of your own.

```python
node = os.environ.get("REVERSELOOM_NODE_PATH") or "node"
bundle = "reverseloom-sandbox.bundle.js"  # mount via run_shell(runtime_files=["reverseloom-sandbox.bundle.js", ...])
proc = subprocess.run(
    [node, bundle],
    input=json.dumps(payload),
    text=True,
    encoding="utf-8",
    errors="replace",
    capture_output=True,
    timeout=60,
)
report = json.loads(proc.stdout.splitlines()[-1])
```

Write the Python wrapper and all dumped scripts, WASM files, and fixtures into the current artifact directory. Execute the wrapper with `run_shell`; its environment exposes the bundled Node executable and `NODE_PATH`. Use relative filenames inside the payload so all runtime resources remain colocated. Do not reimplement the sandbox engine, and do not write a standalone generator that builds its own jsdom/window environment — always drive the materialized `reverseloom-sandbox.bundle.js`.

---

## Payload Shape

```json
{
  "script_path": "target_generator.js",
  "url": "https://target.example/page-or-challenge-url",
  "script_url": "https://target.example/path/to/ips.js",
  "fingerprint": { },
  "patches": "",
  "call": {
    "code": "return await window.generatePayload({page: location.href});",
    "wait_ms": 500
  },
  "monitor": true
}
```

| Field | Purpose |
|---|---|
| `script_path` | Local dumped generator; relative filename (files are copied next to the runner). |
| `url` | `window.location`, document URL, same-origin base for relative requests. |
| `script_url` | Sets `document.currentScript.src` and Error stack filenames. Use the real script URL — VMP/SDK reads this to derive API endpoints. |
| `fingerprint` | Stable observed browser facts. Never put one-time challenge values here. Omit unknown fields — do not guess. |
| `patches` | JS executed **before** target script. Standard browser JS — fix up anything the target reads wrong. |
| `call.code` | JS body run **after** target script loads. May `return` and `await`. |
| `call.wait_ms` | Extra settle time for post-load timers / XHR / fetch / collectors. |
| `monitor` | `true` = enable Deep Proxy monitoring + todo report. `false` = raw execution, no monitoring overhead. |

### `fingerprint` — canonical fields

Harvest these from the live browser/session whenever possible. Mismatch is a
diagnostic signal; do not mask it with random values.

```json
{
  "user_agent": "Mozilla/5.0 ... Chrome/146.0.0.0 Safari/537.36",
  "platform": "Win32",
  "languages": ["zh-CN", "zh", "en"],
  "vendor": "Google Inc.",
  "hardware_concurrency": 8,
  "device_memory": 8,
  "max_touch_points": 0,
  "timezone": "Asia/Shanghai",
  "screen_width": 2560,
  "screen_height": 1440,
  "screen_avail_width": 2560,
  "screen_avail_height": 1392,
  "screen_color_depth": 24,
  "screen_pixel_depth": 24,
  "inner_width": 1920,
  "inner_height": 1080,
  "outer_width": 1920,
  "outer_height": 1080,
  "device_pixel_ratio": 1,
  "connection_effective_type": "4g",
  "webgl_vendor": "Google Inc. (NVIDIA)",
  "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce GTX 1060, OpenGL 4.5)",
  "canvas_data_url": "data:image/png;base64,..."
}
```

---

## `patches` vs `call.code` — it's a timing split

- **`patches`** runs **before** the target script. It's for **staging the
  environment**: fix up anything the target will read but jsdom/our base gets wrong or
  missing — a site-specific `window.XXX`, a cookie setter the site has
  clobbered, helper source the target is about to `eval`, etc. `patches` does
  not return anything; its output is the side effects on `window` / `document`
  / `navigator`.
- **`call.code`** runs **after** the target script. It's for **driving and
  extracting**: call the generator the target attached to `window`, dispatch
  events to trigger collectors, read `document.cookie`, then `return` a plain
  JSON-serializable object. That return value is surfaced as `result` in the
  report.

In short: **`patches` sets the stage, `call.code` runs the show and takes the
bow.**

### Helper inventory (available inside `patches`)

| Helper | Signature | Purpose |
|---|---|---|
| `window`, `document`, `navigator` | — | Full browser globals (jsdom + Chrome overlay). |
| `markNative(fn, name?, length?)` | `(fn, name?, length?) => fn` | Tags `fn` so `Function.prototype.toString` reports `function name() { [native code] }`. Required for every stub that the target reads via `toString`. Optional `name` overrides `fn.name`; optional `length` sets `fn.length`. |

### Phase Map (where your code runs)

```
Phase 1  jsdom initialization (complete DOM, prototypes, built-ins)
Phase 2  prepareStackTrace (clean error stacks)
Phase 3  Anti-detection armor:
           ├── mark-native (Function.prototype.toString defense)
           ├── jsdom-hider (hide Symbol/internal props)
           ├── node-hider (delete process/require/Buffer)
           ├── chrome-overlay (navigator/chrome/screen/dims)
           └── fingerprint (user-provided overrides)
Phase 4  Network recorder + Cookie trap (always active)
Phase 5  Monitor (Deep Proxy + Phantom Chain) — if monitor=true
Phase 6  >>> YOUR patches <<<
Phase 7  eval(target script)
Phase 8  eval(call.code) + wait_ms
Phase 9  Report assembly
```

Implications when writing patches:

- The full DOM is already available (jsdom). Basic DOM operations (createElement,
  querySelector, addEventListener) work out of the box.
- Navigator, chrome, screen are already patched with Chrome defaults + user fingerprint.
  Only override them when evidence shows the patch is wrong for this specific site.
- `markNative` is available — use it on any function you define that the target
  may call `.toString()` on.

### `patches` example

```javascript
// Runs BEFORE target script evaluation.
// The base already has: full DOM, Chrome identity, navigator fingerprint,
// correct toString defense. Only patch what THIS site reads wrong.

// 1. A site-specific global the target expects.
Object.defineProperty(window, 'SomeSiteConfig', {
  value: { region: 'cn', experiment: 'A' },
  configurable: true, enumerable: true,
});

// 2. Override a fingerprint value for this specific site.
Object.defineProperty(navigator, 'appVersion', {
  get: markNative(function() { return '5.0 (Linux; Android 10)'; }, 'get appVersion'),
  enumerable: true, configurable: true,
});

// 3. Stub an API the target checks but jsdom doesn't implement.
window.PerformanceObserver = markNative(function PerformanceObserver() {
  this.observe = markNative(function observe() {}, 'observe');
  this.disconnect = markNative(function disconnect() {}, 'disconnect');
}, 'PerformanceObserver');
```

### `call.code` example

```javascript
// Runs AFTER target script evaluation.
if (typeof window.generatePayload !== 'function') {
  return { error: 'generatePayload missing', keys: Object.keys(window).slice(-50) };
}
const payload = await window.generatePayload({ page: location.href });
document.cookie = 'probe=1; path=/';
return { payload, cookie: document.cookie, href: location.href };
```

---

## Coverage Loop

Evidence-driven, one-blocker-at-a-time:

1. Run with `monitor: true` and a small `wait_ms`.
2. Check `todo` — it tells you exactly what's missing and how many times it was accessed.
3. If `blocking_error` exists, fix that first (it prevented execution).
4. Otherwise patch the **first** `todo` item: the missing API or wrong value.
5. Validate against live evidence: generated URL, method, headers, body,
   cookies, and replay response status.
6. Promote generic fixes into `patches`; keep site-only fixes local.

Do not pre-patch surfaces the target never reads. Do not bury mismatches with
random fallback values — they are the next signal.

---

## Report Shape (success)

```json
{
  "ok": true,
  "result": { },
  "todo": [
    {
      "action": "define",
      "path": "navigator.userAgentData",
      "reason": "accessed 3x, expected property",
      "stack": "at init (target.js:1:2345)"
    },
    {
      "action": "define_function",
      "path": "performance.getEntriesByType",
      "reason": "accessed 1x, expected function (1 args)",
      "stack": "at collect (target.js:1:8901)"
    }
  ],
  "trace": { "total_reads": 1234, "total_writes": 56, "total_calls": 789 },
  "network": [
    {
      "transport": "fetch",
      "method": "POST",
      "url": "https://target.example/api",
      "headers": { "Content-Type": "application/json" },
      "body": { "body_location": "inline", "body_text": "{\"key\":\"value\"}", "body_encoding": "text" },
      "ts": 1760000000000
    }
  ],
  "cookies": { "session": { "value": "abc123", "attributes": "path=/" } },
  "cookie_writes": ["session=abc123; path=/"]
}
```

| Field | What it tells you |
|---|---|
| `ok` | `true` = target script + `call.code` both completed. `false` = blocking error. |
| `result` | Exact return value of `call.code`. Put extracted tokens, cookies, headers, computed replay params here. |
| `todo` | **AI action items.** Each entry says what property/function is missing, how many times it was accessed, and where in the target script. Patch the first one, rerun. |
| `todo[].action` | `define` = missing property, `define_function` = missing function (includes arg count). |
| `todo[].path` | Dot-path like `navigator.userAgentData` or `window.X.Y.z`. |
| `todo[].stack` | Source location in target.js where the access happened. |
| `trace` | Summary stats: total property reads/writes/function calls monitored. |
| `network` | Captured XHR/fetch/beacon attempts. Primary diff surface against browser evidence. |
| `network[].body` | `null` when no body. Otherwise `{ body_location: "inline", body_text, body_encoding }`. Truncated at 5000 chars with `full_length` field for larger bodies. |
| `network[].transport` | `"xhr"`, `"fetch"`, or `"beacon"`. |
| `cookies` | Final cookie jar state (name → value + attributes). |
| `cookie_writes` | Every `document.cookie = ...` assignment in order. |

## Report Shape (failure)

```json
{
  "ok": false,
  "result": null,
  "blocking_error": {
    "message": "Cannot read properties of undefined (reading 'doThing')",
    "stack": "TypeError: ...\n    at init (target.js:1:2345)",
    "caused_by": "undefined object accessed for property 'doThing'"
  },
  "todo": [],
  "trace": { "total_reads": 100, "total_writes": 5, "total_calls": 20 }
}
```

When setup/patches fail before target execution:
```json
{ "ok": false, "error": "patches threw: ...", "stack": "..." }
```

---

## Failure-Mode Catalogue

| Symptom in report | Likely cause | First patch |
|---|---|---|
| `ok: false`, `error: "Cannot find module"` | Bundle or target script missing from `runtime_files`. | Mount the files; use session-relative paths. |
| `ok: false`, `blocking_error.caused_by` says "X is not defined" | Target reads a site-specific global. | Define it on `window` in `patches`. |
| `ok: true`, `result` empty, `network: []` | Generator never fires — branch not entered. | Inspect `todo` for missing APIs; patch the first one. |
| `ok: true`, `todo` shows missing API paths | Environment API missing that target expects. | Stub the first `todo` item with correct value/behavior. |
| Generated body differs from browser by one field | Fingerprint mismatch. | Update `fingerprint` from live evidence; do not randomize. |
| Replay returns 403/412/429 but sandbox `result` looks right | Cookie/header not captured. | Check `cookie_writes` and `network[].headers`; missing values usually live there. |
| `patches` throws | Helper misuse or typo before target eval. | Shrink `patches` to the minimum that reproduces; isolate the bad line. |
| Works once, fails on replay | Generator bound to a one-time token, nonce, or server cookie. | Reproduce the precondition (visit the challenge URL first, harvest the cookie) before generation. |

---

## Operational Notes

- `run_shell` returns exit code and bounded combined output —
  decide business success/failure inside your script and print paths/summaries
  you need.
- Use relative paths for runtime resources; the per-run resource directory is the
  CWD.
- For protected HTTP replay, prefer `curl_cffi.requests.Session(impersonate=...)`
  (`chrome136`, `chrome142`, `chrome145`, `chrome146`).
- The sandbox uses jsdom for DOM — basic DOM operations (createElement, querySelector,
  addEventListener) work out of the box. Canvas/WebGL require the `canvas` npm package
  for pixel data; without it they return empty/null (non-blocking, shows in `todo`).
