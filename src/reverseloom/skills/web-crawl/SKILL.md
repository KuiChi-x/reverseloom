---
name: web-crawl
description: "Collect data from websites and deliver the result with the least necessary complexity. Use for quick lookups, visible-page extraction, search results, tables, pagination, detail-page traversal, downloadable data, reusable crawler generation, bulk collection, and CSV/JSONL/SQLite/XLSX/Parquet output. Return small results directly; generate and run crawler code only when the task requires multiple requests, repeatability, files, validation, or substantial volume."
---

# Web Crawl

Satisfy the user's data need with the cheapest reliable path. Do not turn a small lookup into a crawler project.

## Effort ladder

Choose the first level that can completely satisfy the request:

1. **Direct answer** → If the requested data is already visible in the current browser state or can be obtained with a few browser actions and is small enough to present clearly, return it directly. Do not create files or code unless requested.
2. **Interactive extraction** → Use browser navigation, DOM state, embedded page data, downloads, or existing network responses. Return modest results directly or save the requested file.
3. **Crawler execution** → Generate a crawler only when the task spans many pages or records, needs reproducibility, requires structured files, must resume after failure, or the user explicitly asks for code.
4. **Deep reverse engineering** → Load `deep-reverse` only when ordinary browser observation and crawler construction are blocked by unresolved signatures, encryption, dynamic tokens, or browser-independent replay requirements.

Never skip to a higher level merely because it is more general or technically interesting.

## Workflow

1. Identify the requested fields, scope, freshness, filters, and desired presentation. Ask only for information that materially changes execution.
2. Inspect the page and obtain a representative sample before choosing an implementation.
3. Select the simplest complete source in this order:
   - existing download or export;
   - stable JSON/data response;
   - embedded page state;
   - rendered DOM;
   - browser-driven traversal;
   - deep reverse engineering.
4. For a direct answer, verify the visible values, state any material omissions, and stop.
5. For a crawler, read `references/crawler-engineering.md`, create the files in the session artifact directory, and run a pilot through `run_shell`.
6. For structured output or more than a trivial number of records, read `references/data-output-validation.md` and validate the pilot before full execution.
7. Run the crawler with explicit `cwd` and an appropriate `timeout_seconds`. Keep data on disk; return only progress, samples, counts, and paths to the model.
8. Deliver the user-facing result, crawler source when generated, output files, and a concise validation report.

## Direct-answer rules

- Prefer a clear answer over an artifact when the result is small and the user did not request a file.
- Preserve source labels, units, currencies, dates, time zones, and ranking order.
- Do not claim completeness beyond what was observed.
- Do not generate a crawler for a single value, a short list, or one visible table unless reuse or automation was requested.

## Crawler rules

- Write crawler code before running it; do not assemble large programs inside shell commands.
- Use `curl_cffi` for HTTP collection and Patchright for browser-driven collection.
- Include timeouts, bounded retries, rate limiting, deterministic pagination, checkpointing when useful, deduplication, and structured logging.
- Never accumulate bulk output in model context or shell stdout.
- Write output incrementally and make reruns idempotent when the task is non-trivial.
- Keep credentials and secrets out of source files and output data.

## Reference map

- `references/crawler-engineering.md`: Read before generating or running crawler code, including HTTP, browser-driven, pagination, checkpoint, retry, and execution patterns.
- `references/data-output-validation.md`: Read when producing files, collecting multiple pages, handling substantial volume, or validating CSV, JSONL, SQLite, XLSX, or Parquet output.
