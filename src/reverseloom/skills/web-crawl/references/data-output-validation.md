# Data Output and Validation Reference

## Contents

- [Pilot sampling](#pilot-sampling)
- [Runtime validation](#runtime-validation)
- [Final validation](#final-validation)
- [Format selection](#format-selection)
- [CSV](#csv)
- [JSONL](#jsonl)
- [SQLite](#sqlite)
- [XLSX](#xlsx)
- [Parquet and DuckDB](#parquet-and-duckdb)
- [Validation report](#validation-report)

## Pilot sampling

Validate before full collection:

- sample more than one page, cursor, date range, or result segment;
- include boundary cases and records with missing or unusual values;
- compare selected records with the visible page or raw response field by field;
- verify units, currency, dates, time zones, identifiers, ordering, and nested values;
- confirm that pagination changes the record set and does not repeat the same cursor;
- reopen the pilot output using the intended consumer library.

For a tiny direct-answer task, validation can be a visual cross-check. Do not create a formal report unless the result or risk justifies it.

## Runtime validation

For crawler runs, monitor each committed batch:

- response status and content type;
- schema and required fields;
- sudden null-rate changes;
- duplicate primary keys;
- repeated cursors or pages;
- login, challenge, and error content;
- rows read versus rows written;
- checkpoint progression.

Stop and retain the checkpoint when a material invariant fails.

## Final validation

At completion:

- reconcile source-reported totals when available;
- report rows received, written, rejected, and deduplicated;
- inspect first, middle, last, random, and retry-adjacent samples;
- reopen every output part;
- verify column names and types;
- report null and duplicate counts for important fields;
- list uncovered ranges or failed URLs;
- include file sizes and a manifest.

## Format selection

Format is independent of size: a small result can still be requested as an Excel
file, and a large collection can still be asked for as JSON. Decide the format
from the user's request, and the size only decides whether data flows through
disk (see the SKILL's context discipline).

| Need | Preferred format |
|---|---|
| Small human-readable table | XLSX or direct answer |
| Flat interoperable data | CSV |
| Nested or variable records | JSONL |
| Large resumable local dataset | SQLite |
| Large analytical dataset | Parquet, inspected with DuckDB |

Use the format requested by the user when practical. If the requested format cannot represent the volume safely, explain the constraint and provide a compatible split or companion format.

## Delivering a downloaded file as-is

When the source was a ready file (Excel/CSV/PDF/ZIP the page offered) and the
user wants that file — not fields from inside it — deliver the downloaded file
unchanged. Do not re-serialize it through model context or rebuild it from
scraped rows; the original export is authoritative. Save it to the artifact
directory and hand off the path. Only when the user needs specific fields do you
parse it (see `references/source-selection.md`) and then choose an output format
above.

## CSV

Use Python's `csv` module and `newline=""`. Prefer UTF-8 with BOM when the primary consumer is Windows Excel. Keep a fixed header and column order. Split very large exports into predictable parts and include a manifest.

Do not pass bulk CSV content through model context or `write_artifact`; let the crawler stream directly to disk.

## JSONL

Write one valid JSON object per line with UTF-8 encoding. JSONL is the safest default for nested records and incremental recovery. Keep rejected records in a separate JSONL file with an error reason.

## SQLite

Use SQLite for large resumable runs, uniqueness constraints, local queries, and incremental updates. Define a primary key or unique index. Commit in batches and store checkpoint state separately from business records.

## XLSX

Use `openpyxl` write-only mode for large exports and reopen the workbook after saving. A worksheet is limited to 1,048,576 rows, including the header. Split data across worksheets or workbooks before reaching the limit.

For very large datasets, prefer a summary workbook plus CSV, SQLite, or Parquet data files. Include field definitions, counts, validation results, and samples in the workbook.

## Parquet and DuckDB

Use DuckDB to validate large CSV, JSONL, SQLite-derived, or Parquet datasets without loading everything into model context. Use it for row counts, type inspection, null counts, duplicate checks, sampling, and conversion to Parquet.

Avoid adding pandas or PyArrow unless a concrete requirement cannot be met by DuckDB and the standard library.

## Validation report

Write a concise machine-readable report, for example:

```json
{
  "status": "passed",
  "rows_received": 3021456,
  "rows_written": 3021456,
  "rows_rejected": 0,
  "duplicates": 0,
  "parts": 31,
  "required_field_nulls": {"record_id": 0},
  "uncovered_ranges": []
}
```

The final user response should summarize this report rather than reproducing the dataset.
