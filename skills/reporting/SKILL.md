---
name: reporting
description: Build board-room style dashboards, KPIs, and trend summaries over any Odoo data. Use when the user wants a report, dashboard, KPI, trend analysis, or executive summary.
triggers: [report, dashboard, kpi, analytics, insight, trend, summary, exec, board, samenvatting]
---

# Reporting

Turn Odoo data into numbers a leader can act on — not a data dump.

## Building blocks

- `search_records` with `fields` — for line-level detail
- `aggregate_records` — for aggregations (sum/avg/count per dimension), e.g. `groupby=["product_id", "date:month"]`, `aggregates=["price_subtotal:sum"]`, `order="price_subtotal:sum desc"`. Omit `groupby` for one grand total. Never page through raw lines to add them up.
- `export_records` — for a row-level appendix or a spreadsheet: compact CSV with relation paths, e.g. `fields=["move_id/name", "account_id/code", "balance"]`. Write it to a file instead of pasting thousands of rows into the answer.
- Multiple queries composed into one narrative — not a single giant result

## Typical report shapes

1. **KPI card** — one number with comparison (vs prev period, vs target)
2. **Trend** — daily/weekly/monthly series, show direction + anomalies
3. **Leaderboard** — top/bottom N by dimension (salesperson, product, customer)
4. **Breakdown** — total split by one dimension (% of total per category)
5. **Cohort/funnel** — stage-to-stage conversion

## Output principles (board-room style)

- Lead with the headline number and its movement
- Then the 2-3 drivers (what moved)
- Then the outlier / surprise
- Keep tables under 15 rows; use "and 47 more" rather than dumping everything
- Always include the period + filter used

## Gotchas

- Date filters: use `invoice_date` vs `create_date` vs `date_done` deliberately — they answer different questions.
- Currency: if multi-company, always aggregate in company or group currency — never mix.
- Exclude drafts by default (`state != 'draft'` for invoices, `stage_id != 'New'` for leads).
- Beware double-counting: an SO with multiple lines × a join to pickings can 4× revenue.
