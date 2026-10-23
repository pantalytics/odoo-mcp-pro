---
name: importing
description: Load data into Odoo from CSV, Excel, or external systems — idempotently via external IDs. Use when the user wants to bulk-import contacts, products, leads, transactions, or any other records.
triggers: [import, load, bulk, csv, excel, xlsx, upload, migrate, seed]
---

# Importing

Bring external data in without creating duplicates on re-runs.

## Core pattern: idempotent import

Use `record_import` with `__import__.external_id` on every row:

- First run → records get created
- Second run with same external_id → updates existing records
- No duplicates, no manual cleanup

External ID format: `__import__.your_stable_key` (e.g. `__import__.contact_12345`)

## Typical workflows

1. **Map fields first** — list source columns → Odoo model fields via `model_list` then introspect
2. **Resolve references upfront** — m2o fields (e.g. `country_id`) need IDs or external IDs, not names
3. **Dry-run small batch** — import 5 rows, inspect, then scale
4. **Batch size** — 500-1000 rows per call is the sweet spot
5. **Retry on fail** — fix source rows, re-run with same external_ids to patch

## Choosing the right tool

- `record_import` — best for CSV-shaped data, handles external IDs natively
- `records_create` — direct writes, no external ID mapping
- `records_update` — when you already have internal IDs

## Gotchas

- m2o by name is fuzzy and unsafe for non-unique names — always resolve to ID or external ID first.
- Boolean fields accept `1/0` or `True/False` — be consistent per column.
- Dates must be ISO `YYYY-MM-DD`; datetimes `YYYY-MM-DD HH:MM:SS` UTC.
- `product.product` vs `product.template`: if you import variants, hit `product.product` and set `product_tmpl_id`.
- Currency / decimal separators: Odoo expects `.` as decimal, regardless of locale.
