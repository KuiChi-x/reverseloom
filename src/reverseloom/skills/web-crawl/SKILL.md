---
name: web-crawl
description: "Get data from websites with the least necessary complexity. Use for quick lookups, visible-page extraction, search results, tables, pagination, detail-page traversal, downloadable files (Excel/CSV/PDF/ZIP), embedded page state, internal JSON/XHR APIs, bulk collection, reusable crawler generation, and delivery in any format (text / Excel / CSV / JSON / JSONL / SQLite / Parquet). Return small results directly; write large or record-set data to disk and return only progress, samples, and paths."
---

# Web Crawl

Get the user's data by the cheapest reliable path, and never let bulk data flow back into your own context.

## Context discipline (the one rule everything else serves)

Your context window is small and shared with page state and screenshots. Bulk data must go to disk, not into your context.

Route by the size of the result you are about to handle:

- **> 30000 characters, OR > 500 rows/records → collection mode.** The data must be written to disk (via a tool or a generated script). Into your context return ONLY: progress, counts, a sample of at most 20 records, and file paths. Never read the full dataset back.
- **Otherwise → observation mode.** The result is small enough to hold and present directly.

A stream of records you are accumulating is a dataset, not an answer: once it will cross either threshold, switch to collection mode before it grows — do not first gather everything in context and then decide.

## Q1 · Mode

- **Observation mode** — small result. Read it in the current session and answer directly. Do not create files or write code.
- **Collection mode** — large result or a growing record set. Locate the data source, write data to disk incrementally, and return only progress / samples / paths.

## Q2 · Source (collection mode) — take the cheapest that is complete

Prefer sources in this order; earlier ones are cheaper, more reliable, and scale better:

1. **A ready file** — a page download/export link (Excel, CSV, JSON, PDF, ZIP). Fetch the file directly instead of scraping the DOM; it is already structured and one request wide. If the user needs fields from inside it, download then parse it with a script.
2. **An internal JSON / XHR API** — the request the page itself makes to render data. You are in a real authenticated session: reuse the session's existing cookies, headers, and tokens as observed — copy them into an HTTP client (e.g. `curl_cffi`). Do not reconstruct auth from scratch.
3. **Embedded page state** — inline JSON the page ships (e.g. `__NEXT_DATA__`, `window.__INITIAL_STATE__`, a `<script type="application/json">`). Read it once; it often contains the whole list without pagination.
4. **Rendered DOM / browser-driven traversal** — last resort. Paginate and write each batch to disk as you go. For tens of thousands of records this is the worst path — exhaust 1–3 first.

## Q3 · Delivery format (independent of size)

Produce what the user asked for, whatever the mode:

- a direct text answer (small results only);
- a specific format — Excel, CSV, JSON, JSONL, SQLite, Parquet;
- reusable crawler source code (only when the user asked for code);
- a page's own downloaded file, delivered as-is.

Format is a separate decision from size — a short result can still be requested as an Excel file. See `references/data-output-validation.md`.

## Observation mode

1. Identify the requested fields, scope, filters, and desired presentation.
2. Reach the data (navigate, filter/search, scroll, or read an embedded/JSON source).
3. Verify the visible values against the page; state any material omission.
4. Answer directly, or save the one requested file if the user asked for a file.

Keep it to a few actions. Do not build a crawler for a single value, a short list, or one visible table unless reuse was requested.

## Collection mode

1. Sample first: inspect one page / cursor / segment and confirm the source and shape before scaling.
2. Locate the source by the Q2 order; capture the request shape and the session's existing credentials from observation — do not guess hidden inputs.
3. Read `references/crawler-engineering.md`, then generate a resumable collector with `write_artifact` and pilot it with `run_shell`. The collector writes records to disk; its stdout is progress only.
4. Read `references/data-output-validation.md`, validate the pilot against source evidence, then run the full collection.
5. Throughout, return to your context only: progress, counts, a sample of ≤20 records, and paths. Never return or read back the full dataset.
6. Deliver the output file(s) and a concise validation summary; the final reply summarizes, it does not contain the dataset.

## When you are blocked

Browser blockers are operational, not cryptographic — you already hold the real session, so reuse its cookies/tokens rather than reconstructing anything.

- **Login wall** — authenticate if credentials are available; otherwise stop and report.
- **CAPTCHA / human check** — request human help if that path exists; otherwise stop and report.
- **Rate limiting / IP block (403/429)** — back off; do not hammer the same URL. If it persists, stop and report what was collected.

Report the blocker with the endpoint/page, what you observed, and the data collected so far. Deliver partial results rather than fabricating.

## Reference map

- `references/source-selection.md`: How to identify and fetch each source — download links, internal JSON/API with reused session credentials, embedded state, and parsing downloaded files for fields.
- `references/crawler-engineering.md`: Read before generating or running a collector — HTTP, browser-driven, pagination, checkpointing, retries, and disk-only data flow.
- `references/data-output-validation.md`: Read when producing files or validating output — format selection (CSV / JSONL / SQLite / XLSX / Parquet), delivering a downloaded file as-is, and validation.
