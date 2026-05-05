# Odoo MCP Skills

Markdown workflow guides that teach Claude (or any MCP client) how to
accomplish common Odoo tasks. Each skill is a self-contained directory
with a `SKILL.md` entry point.

The MCP server exposes every skill as a resource at `skill://{name}` —
clients can list them, decide which is relevant, and fetch the body on
demand. No Odoo connection required to read them.

## Layout

```
skills/
  <skill-name>/
    SKILL.md          # entry point, loaded on resource fetch
    REFERENCE.md      # optional deep-dive, linked from SKILL.md
    workflows/        # optional: specific task guides
    examples/         # optional: sample payloads
```

Every `SKILL.md` starts with YAML frontmatter:

```yaml
---
name: selling
description: Work with customers, quotes, sales orders...
triggers: [sell, quote, offerte, sale.order, ...]
odoo_modules_any: [sale_management, crm]
---
```

`name` and `description` are required; everything else is convention.

## Skill index

### Domain skills — "what are you doing"

| Skill | For | Odoo apps |
|---|---|---|
| `selling` | Customers, quotes, orders, invoices | CRM + Sales + AR |
| `buying` | Vendors, POs, bills, payments | Purchase + AP |
| `inventory` | Stock, transfers, warehouse ops | Inventory |
| `making` | MOs, BOMs, work orders | Manufacturing |
| `finance` | GL, reconciliation, VAT, reports | Accounting |
| `projects` | Projects, tasks, timesheets | Project + Timesheets |
| `support` | Tickets, SLAs, field service | Helpdesk + FSM |
| `people` | Employees, leave, expenses, hiring | HR + Time Off + Expenses + Recruitment |
| `marketing` | Campaigns, mailings, events | Email Marketing + Events |
| `retail` | POS sessions, cash control | Point of Sale |

### Cross-cutting skills — "what kind of work"

| Skill | For |
|---|---|
| `importing` | Load CSV/Excel with idempotent external IDs |
| `reporting` | Dashboards, KPIs, exec summaries |
| `configuring` | Custom fields, views, Studio-style changes |
| `automating` | Automated actions, cron jobs, triggers |
| `cleaning` | Deduplicate, enrich, fix data quality |
| `permissions` | Groups, access rights, record rules |
| `communications` | Chatter messages, notes, activities, attachments, followers (mail.thread API) |

### Flagship skills

| Skill | For |
|---|---|
| `import-pro` | Full consultant-led data migration workflow (companion to Import Pro module) |
| `odoo-advisor` | Pick the right Odoo app/model for a business process |

## Authoring guidelines

1. **Start minimal.** Name + description + a short body (~30-50 lines).
   Add detail later, based on observed failure modes.
2. **Progressive disclosure.** Keep `SKILL.md` short enough to always load.
   Put detail in `REFERENCE.md`, `workflows/*.md`, `examples/*` — Claude
   follows markdown links on demand.
3. **Iterate on observation.** Don't preemptively add rules "just in case".
   Add them when you see Claude get it wrong.
4. **`description` is the #1 lever.** It's what clients read to decide
   whether to load the skill. Be specific about when it applies.

See [Anthropic's skill-authoring guidance](https://docs.claude.com/en/docs/claude-code/skills)
for the canonical version of these principles.
