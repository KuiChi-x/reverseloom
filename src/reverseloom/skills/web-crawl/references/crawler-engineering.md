# Crawler Engineering Reference

## Contents

- [Strategy selection](#strategy-selection)
- [Project layout](#project-layout)
- [HTTP crawlers](#http-crawlers)
- [Browser crawlers](#browser-crawlers)
- [Pagination and checkpoints](#pagination-and-checkpoints)
- [Retries and rate limits](#retries-and-rate-limits)
- [Shell execution](#shell-execution)
- [Completion criteria](#completion-criteria)

## Strategy selection

Use the least expensive implementation that preserves correctness:

| Situation | Preferred implementation |
|---|---|
| A few visible values | Browser actions and direct answer |
| Existing export button | Trigger or reproduce the export |
| Stable JSON response | HTTP crawler with `curl_cffi` |
| Login plus normal navigation | Patchright browser crawler |
| Many detail pages | Queue URLs and checkpoint progress |
| Unknown signing or encryption | Load `deep-reverse` before implementing |

Do not reproduce a private protocol when browser-driven collection already satisfies a one-time task. Do not retain browser automation when the user explicitly requires a browser-independent crawler.

## Project layout

Create crawler files in the current session artifact directory:

```text
crawler.py
config.json
checkpoint.json
output/
validation.json
README.md
```

Keep the initial implementation small. Add files only when they carry real state or improve delivery.

## HTTP crawlers

Use `curl_cffi.requests.Session` when collecting through HTTP. Reuse one session for cookies and connection pooling. Set an explicit browser impersonation only when the target requires it.

Required properties:

- explicit connect/read timeout;
- bounded retry policy;
- status and content-type checks;
- detection of login, challenge, and error pages;
- deterministic page or cursor advancement;
- incremental output writes;
- no secrets embedded in source;
- concise progress logging.

Build the request from observed evidence. Do not guess hidden headers or signing inputs. Load `deep-reverse` when required values cannot be regenerated reliably.

## Browser crawlers

Use Patchright when the workflow depends on rendered DOM, user interactions, browser login state, downloads, or browser-only state.

Required properties:

- use a persistent context only when state must survive;
- wait for specific page conditions rather than arbitrary long sleeps;
- close pages and contexts in `finally` blocks;
- extract structured values in-page and write them immediately;
- cap parallel pages to avoid memory growth;
- preserve the user-visible ordering when it matters;
- save failure URLs and reasons for later retry.

Prefer stable semantic attributes and observed page structure. If the generated crawler is delivered for reuse, document assumptions that may change.

## Pagination and checkpoints

Support the site's actual pagination model:

- page number;
- offset and limit;
- opaque cursor;
- date or ID range;
- next-page link;
- queue of detail URLs.

For non-trivial runs, save checkpoint state after each committed batch. A checkpoint should contain only restart information, for example:

```json
{
  "next_cursor": "...",
  "page": 42,
  "rows_written": 41000,
  "updated_at": "2026-07-14T12:00:00Z"
}
```

Write data before advancing the checkpoint. Make duplicate writes harmless through a primary key, unique index, or deterministic deduplication key.

## Retries and rate limits

- Retry transient network failures and selected 5xx responses.
- Respect `Retry-After` on 429 responses.
- Do not blindly retry authentication, challenge, schema, or permission failures.
- Use exponential backoff with a maximum delay.
- Keep concurrency conservative until a pilot proves stability.
- Stop when the response changes materially rather than writing error content as data.

## Shell execution

Use `write_file` or `write_artifact` to create readable source files, then execute them with `run_shell` from the artifact directory.

Pilot example:

```text
python crawler.py --limit 200 --output output/pilot.jsonl
```

Full run example:

```text
python crawler.py --resume --output output/data.jsonl
```

Before the pilot, verify only the imports required by the generated crawler. Do not install packages silently; report a missing runtime dependency or use a simpler available format.

Set `timeout_seconds` according to the expected run. Keep stdout to progress summaries; write records and verbose logs to files. The shell environment provides:

- `REVERSELOOM_ARTIFACT_DIR`;
- `REVERSELOOM_PYTHON_PATH` for the active Python environment in source or pip installations;
- `REVERSELOOM_NODE_PATH` when Patchright's bundled Node is available;
- `REVERSELOOM_SANDBOX_BUNDLE` when the reverse sandbox bundle is packaged;
- `NODE_PATH` for the bundled sandbox runtime.

## Completion criteria

A crawler task is complete only when:

- the requested scope is covered or explicitly bounded;
- a pilot has been compared with source evidence;
- output can be reopened and counted;
- pagination has no unexplained gaps or loops;
- duplicates and rejected rows are reported;
- generated source runs from the documented command;
- the final response distinguishes observed facts from assumptions.
