---
name: cleaning
description: Deduplicate, enrich, and fix data quality — especially contacts, products, leads. Use for dedupe, merging duplicates, fixing formatting, enrichment, or data-quality audits.
triggers: [clean, cleaning, cleanup, dedupe, duplicate, dubbel, merge, enrich, verrijken, data quality, kwaliteit]
---

# Cleaning

Find bad data, propose fixes, execute with confirmation. Destructive — always confirm before writing.

## Common problems

- **Duplicates** — same entity in multiple records (res.partner, product.product, mailing.contact)
- **Formatting drift** — inconsistent caps/punctuation (`ACME` vs `Acme` vs `acme inc`)
- **Missing fields** — partners without country/VAT, products without category
- **Stale data** — inactive that should be archived; unused tags/categories
- **Orphans** — m2o references to deleted records (rare but possible in imports)

## Typical workflows

1. **Dedupe contacts** — fuzzy-match on `email`, `vat`, `name + country`, `phone`; cluster; propose master + merge list
2. **Merge partners** — use `base.partner.merge.automatic.wizard` or manual: move refs, archive losers
3. **Normalize** — batch update via `records_update`, e.g. title-case all partner names
4. **Enrich** — pull external data (web scrape, VIES VAT lookup, Clearbit) and fill empty fields
5. **Archive stale** — find records with no activity in N months, set `active = False`

## Gotchas

- **Merging loses history** — archived losers keep messages but aren't easily findable. Always confirm before merging.
- Duplicates aren't always duplicates — same name, different country = different company.
- VIES lookup is rate-limited; batch with backoff.
- Don't mass-update without a dry-run: `search_count` first, show sample, then write.
- Archive (`active = False`) is reversible; `unlink` is not — prefer archive.
