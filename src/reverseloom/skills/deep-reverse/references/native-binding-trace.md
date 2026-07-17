# Native Binding Trace — sandbox environment oracle

When rebuilding the sandbox environment has stalled — the `todo` keeps naming new
missing APIs, or `result` stays wrong and you cannot see why — read the trace of
the real run. It is captured automatically (radar-browser only). If no trace files
exist, the browser did not emit one; continue with ordinary patching.

## The two signals

Everything useful is one of these, both keyed to `line:column` in the deobfuscated
script:

1. **Hash inputs — the answer in plaintext.** Fingerprinters serialize each
   component and feed it to a hash/serialize call (`TextEncoder.encode`,
   `SubtleCrypto.digest`, `JSON.stringify`, `btoa`, `Blob`). Its string `args` are
   the exact bytes hashed. Reproduce those bytes in the sandbox and the hash
   matches — you skip re-deriving every environment read. Read them in call order:
   that sequence *is* the component list.
2. **Leaf values — what the sandbox must return.** Each native read shows its
   concrete scalar (`BatteryManager.level.get = 1`, `NetworkInformation.rtt = 100`).
   Captured on radar-browser, so these already include its spoofing: **match the
   trace, never real hardware.** Any sandbox value that disagrees with its leaf is
   the bug.

Order and value are the signal; call frequency is not — one
`RTCPeerConnectionIceEvent.candidate.get = null` matters, a thousand
`Document.createElement` do not.

## The record

One JSON object per line. `api` is `Interface.method` or `Interface.attr.get`/
`.set`. Primitives carry `value` (or `valueText` for `Infinity`/`NaN`); strings
over 1024 B set `"truncated":true`; objects/arrays give only `{"type":"object"}`
with no contents.

```json
{"line":2253,"column":45,"api":"TextEncoder.encode","args":[{"type":"string","value":"[\"zh-CN\",\"2100万\",\"other\"]"}],"result":{"type":"arrayBufferView"}}
{"line":7525,"column":57,"api":"BatteryManager.level.get","args":[],"result":{"type":"number","value":1}}
```

## Where it is

```
<session>/_native_trace/<domain>_<time>_pid-<pid>/<url-path>_<time>.jsonl
```

`<session>` is the parent of `REVERSELOOM_ARTIFACT_DIR` (which `run_shell`
injects). Expect many dirs — most are browser noise (`new-tab-page`, `gstatic`,
`unknown`), the same domain splits across several dirs (one renderer, many V8
contexts; worker code lands under `unknown`), one file per script. Do not pick a
dir by hand: find the file(s) for the anti-bot script you already identified (by
filename, e.g. `*creep.js*`), or glob all and filter lines to that script.

## Worked example

Target script `creep.js` is stalled — sandbox output hash differs from the
browser's. In the trace file you find, in order:

```
[2253:45] TextEncoder.encode  ["zh-CN","2100万","other"]
[2253:45] TextEncoder.encode  [1400,2560,32]
[2253:45] TextEncoder.encode  ["ANGLE (NVIDIA, RTX 5090 …",...]   "truncated":true
[7525:57] BatteryManager.level.get = 1
[7533:41] NetworkInformation.rtt.get = 100
```

Signal 1 (the `encode` args) is the component list feeding the hash: locale trio,
screen `[w,h,depth]`, WebGL renderer, … Make the sandbox emit those same strings
and the hash matches. The WebGL one is `truncated` — reconstruct its full value
from signal 2 (the `getParameter`/renderer leaf reads at that call site). Set
`battery.level=1`, `rtt=100`, etc. from signal 2 wherever the sandbox disagrees.

## How to extract

Do not eyeball the firehose. With `run_shell`, two passes:

- **Signal 1**: grep lines whose `api` contains `encode|digest|stringify|btoa|Blob`,
  print their `args` strings in file order.
- **Signal 2**: filter lines whose `result.type` is `number|string|boolean|null`,
  print `line:column  api = value`, dedup by `line:column`+value.

Use whatever one-off (grep/python/jq) fits the file; the schema above is all you
need to parse it. Then reproduce signal 1's bytes; use signal 2 for any component
still wrong or `truncated`.

## Limits

- Objects/arrays have no contents — get structure from signal 1's serialized
  string or a Path A breakpoint.
- Strings truncate at 1024 B — rebuild long payloads from their leaf reads.
- No dedup at source — always filter to your script and collapse repeats.
