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

This reference covers collection mode. Choose the source first (see
`references/source-selection.md`): a ready download file or an internal JSON/API
beats DOM traversal for anything at scale. The patterns below apply once the
source is chosen.

## The disk-only rule

Bulk data never enters model context. The collector writes records straight to
disk; stdout carries progress only. You return counts, a small sample (≤20
records), and file paths — never the dataset. This holds for every pattern
below: extraction, pagination, retries, validation, and delivery.

## Strategy selection

Use the least expensive implementation that preserves correctness:

| Situation | Preferred implementation |
|---|---|
| A few visible values | Browser actions and direct answer (observation mode) |
| Existing download/export file | Fetch the file directly; parse it if fields are needed |
| Data-bearing internal JSON/API | HTTP collector with `curl_cffi`, reusing the session's cookies/headers |
| Data only in rendered DOM | Browser-driven traversal, writing each batch to disk |
| Many detail pages | Queue URLs and checkpoint progress |
| A required value is computed and not observable anywhere in the session | Out of scope (signing/encryption) — report the blocker and deliver what was collected |

Reuse the live session's existing cookies, headers, and tokens when replaying a
request — they are observable session state, not something to reconstruct. Only
when a required value is genuinely computed and absent from every cookie,
header, response, and embedded state is it out of scope; stop and report it.

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

Build the request from observed evidence. Do not guess hidden headers or signing inputs. If a required value cannot be regenerated reliably, stop and report the blocker instead of guessing or driving a browser to produce it.

## Browser crawlers

Deliver a Patchright browser crawler ONLY when the user explicitly asked for browser automation. In the default analysis scenario a browser-driven crawler is an incomplete deliverable — reproduce the workflow in a browser-independent HTTP client instead. If signing or tokens block reproduction and you cannot regenerate the value from observed evidence, stop and report the blocker. The patterns below apply when browser automation was explicitly requested.

Use Patchright when the workflow depends on rendered DOM, user interactions, browser login state, downloads, or browser-only state.

Required properties:

- use a persistent context only when state must survive;
- wait for specific page conditions rather than arbitrary long sleeps;
- close pages and contexts in `finally` blocks;
- extract structured values in-page and write them immediately;
- cap concurrent pages to avoid memory growth;
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

Use `write_artifact` to create readable source files, then execute them with `run_shell` from the artifact directory.

Pilot example:

```text
python crawler.py --limit 200 --output output/pilot.jsonl
```

Full run example:

```text
python crawler.py --resume --output output/data.jsonl
```

Before the pilot, verify only the imports required by the generated crawler. Do not install packages silently; report a missing runtime dependency or use a simpler available format.

Set `timeout_seconds` according to the expected run. Keep stdout to progress summaries; write records and verbose logs to files. Run scripts with `python`/`python3` and the HTTP libraries available in the environment (e.g. `curl_cffi`); if a dependency is missing, report it or fall back to a simpler available format rather than installing silently.

## Completion criteria

A crawler task is complete only when:

- the requested scope is covered or explicitly bounded;
- a pilot has been compared with source evidence;
- output can be reopened and counted;
- pagination has no unexplained gaps or loops;
- duplicates and rejected rows are reported;
- generated source runs from the documented command;
- the final response distinguishes observed facts from assumptions.
