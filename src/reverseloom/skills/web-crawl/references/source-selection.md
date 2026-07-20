# Source Selection Reference

How to identify and take each data source, cheapest first. In an authenticated
browser session you already hold real cookies, headers, and tokens — reuse them;
never reconstruct authentication.

## Contents

- [1. Ready file (download / export)](#1-ready-file-download--export)
- [2. Internal JSON / XHR API](#2-internal-json--xhr-api)
- [3. Embedded page state](#3-embedded-page-state)
- [4. Rendered DOM](#4-rendered-dom)
- [Parsing a downloaded file for fields](#parsing-a-downloaded-file-for-fields)
- [Reusing the session's credentials](#reusing-the-sessions-credentials)

## 1. Ready file (download / export)

The best source: one request, already structured, no pagination.

Identify:
- an explicit "Export / Download / 导出 / Excel / CSV" control;
- a link ending in `.xlsx`, `.csv`, `.json`, `.pdf`, `.zip`;
- a report/print endpoint that returns a file.

Take:
- fetch the file URL directly with the session cookies (see below);
- save it to the artifact directory;
- if the user only wants the file, deliver it as-is;
- if the user wants fields from inside it, parse it (below) — do not re-scrape the page for data the file already contains.

## 2. Internal JSON / XHR API

The request the page makes to render its data. Best for scale: paginates cleanly, no DOM.

Identify:
- inspect network activity for XHR/Fetch responses whose body carries the target records (a list array, a `data`/`items`/`results` field, a `total`/`page`/`cursor`);
- trigger the page action (search, scroll, next page) and watch which request returns the new rows.

Take:
- copy the request exactly as observed — URL, method, query/body, and the request headers the session already sends (Cookie, Authorization, X-CSRF-Token, Referer, UA);
- replay it from an HTTP client (`curl_cffi`) with those same headers/cookies;
- walk pagination by the API's own mechanism (page / offset / cursor / range);
- these values are observable session state — take them as-is, do not derive them.

If a required value is NOT observable anywhere in the session (not in a cookie, header, response, or embedded state) and is clearly computed, that is a signing/encryption problem — out of scope for a browser data agent. Stop and report it.

## 3. Embedded page state

Inline JSON the server shipped with the HTML — often the entire first page (or all) of data without any extra request.

Identify:
- `__NEXT_DATA__`, `window.__INITIAL_STATE__`, `window.__NUXT__`, Redux/Apollo state;
- `<script type="application/json">` blocks;
- a large inline JSON literal in a `<script>`.

Take:
- read the embedded JSON directly;
- if it holds everything, you may not need pagination at all;
- if it holds only page 1 plus an API cursor, hand off to source 2.

## 4. Rendered DOM

Last resort — only when 1–3 do not exist. Data lives only in the rendered page.

Take:
- extract structured values in-page and write each batch to disk immediately;
- never accumulate rows in context;
- paginate deterministically; stop on repeat/empty.

For tens of thousands of records this path is slow and failure-prone. Re-check for a file or an internal API before committing to DOM traversal at scale.

## Parsing a downloaded file for fields

When the user wants specific fields out of a downloaded `.xlsx` / `.csv` / `.json`:

- download the file first (source 1);
- parse with a script via `run_shell` — `openpyxl` for xlsx, the stdlib `csv`/`json` for the rest;
- extract only the requested fields;
- if the extracted result is small (within the size thresholds) answer directly; if large, write it out and return only a sample + path.

Do not load a large workbook's full contents into context; read rows in the script and emit only what is needed.

## Reusing the session's credentials

The observed request already carries everything needed to authenticate the replay:

- **Cookies** — send the same cookie jar the browser session holds.
- **Headers** — copy `Authorization`, `X-CSRF-Token`, `X-Requested-With`, `Referer`, `Origin`, and the User-Agent exactly as the page sends them.
- **TLS profile** — use `curl_cffi` with a browser impersonation only if the target checks it.

These are all present in the live session — copy them. A browser data agent reuses credentials; it does not compute them.
