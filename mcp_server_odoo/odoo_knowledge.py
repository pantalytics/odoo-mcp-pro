"""Odoo domain knowledge for MCP server instructions.

This knowledge is sent to AI clients during the MCP handshake via the
`instructions` field. It helps the AI make better decisions about which
Odoo models and apps to use when fulfilling user requests.
"""

SERVER_INSTRUCTIONS = """
You are connected to an Odoo ERP instance via MCP. Use this knowledge to make better decisions.

## Core Concepts

- **res.partner**: Contacts (companies and individuals). Used everywhere. NOT the same as CRM leads.
- **product.template**: Product catalog (shared fields). **product.product**: Variants (size, color).
- **ir.model**: List of all models. Use `model_list` to discover available models.
- **External IDs**: Use `record_import` with `__import__.your_id` for idempotent imports. Running twice = no duplicates.

## App Selection Guide

Ask yourself: what is the user trying to do?

### Selling
- **CRM** (`crm.lead`): Sales pipeline, lead scoring, win/loss tracking. B2B multi-stage sales.
- **Sales** (`sale.order`): Quotations, sales orders, invoicing. Any company that sells.
- **Subscriptions** (`sale.order` with recurrence): Recurring billing (SaaS, memberships).
- **Rental** (`sale.order` with rental fields): Rent out products with delivery/return.
- **Point of Sale** (`pos.order`): Retail, restaurant, walk-in transactions.

### Buying
- **Purchase** (`purchase.order`): RFQs, purchase orders, vendor management, reordering rules.

### Inventory & Manufacturing
- **Inventory** (`stock.picking`, `stock.move`, `stock.quant`): Stock tracking, warehouses, shipping, lots/serials.
- **Manufacturing** (`mrp.production`, `mrp.bom`): Produce goods from components using bills of materials.
- **Quality** (`quality.check`): Inspection at receipt, production, or shipping.
- **PLM** (`mrp.eco`): Engineering change orders on products and BOMs.
- **Maintenance** (`maintenance.equipment`, `maintenance.request`): YOUR internal equipment upkeep.
- **Repairs** (`repair.order`): CUSTOMER sends broken product, you fix at YOUR location.
- **Barcode**: Mobile scanning for warehouse operations.

### Services
- **Project** (`project.project`, `project.task`): Internal/client projects with tasks and stages.
- **Timesheets** (`account.analytic.line`): Time tracking for billing or internal use.
- **Field Service** (`project.task` with `is_fsm=True`): Dispatch technicians to CUSTOMER's location.
- **Helpdesk** (`helpdesk.ticket`): Customer support tickets via email/chat/web. Front door for after-sales.
- **Planning** (`planning.slot`): Employee shift scheduling and resource planning.

### "Fix Things" Decision Matrix
| | Your equipment | Customer's equipment |
|---|---|---|
| Your location | **Maintenance** | **Repairs** |
| Customer's location | (rare) | **Field Service** |
| Remote | - | **Helpdesk** |

### Finance
- **Accounting** (`account.move`): General ledger, journal entries, tax, bank reconciliation.
- **Invoicing**: Lighter than Accounting. Just send invoices.
- **Expenses** (`hr.expense`): Employee expense reports and reimbursement.
- **Payroll** (`hr.payslip`): Salary computation and pay slips.

### HR
- **Employees** (`hr.employee`): Employee records, departments, org chart.
- **Recruitment** (`hr.applicant`): Job postings, applicant pipeline.
- **Time Off** (`hr.leave`): Vacation, sick leave requests and balances.
- **Attendances** (`hr.attendance`): Clock in/out tracking.
- **Appraisals** (`hr.appraisal`): Performance reviews.

### Marketing
- **Email Marketing** (`mailing.mailing`): Mass emails, newsletters, campaigns.
- **SMS Marketing** (`mailing.mailing` with SMS): Mass SMS campaigns.
- **Marketing Automation** (`marketing.campaign`): Multi-step drip campaigns.
- **Events** (`event.event`): Conferences, trainings, webinars with registration.
- **Social Marketing** (`social.post`): Manage social media from Odoo.

### Website
- **Website**: CMS, blog, forms, customer portal.
- **eCommerce** (`sale.order` via web): Online shop with cart, checkout, payments.
- **eLearning** (`slide.channel`): Online courses and certifications.

## Common Patterns

### Creating records
- Always check which fields are required: `record_search` with `fields=["__all__"]` on a small set first.
- Use `record_import` for bulk data with external IDs (idempotent).
- Use `record_create`/`records_create` for one-off creates.

### Searching
- Default limit is 100 records. Use `limit` param for more (max 500).
- Domain filters follow Odoo syntax: `[["field", "operator", value]]`.
- Common operators: `=`, `!=`, `ilike` (case-insensitive contains), `in`, `>=`, `<=`.
- Combine with `|` (OR): `["|", ["field1", "=", "a"], ["field2", "=", "b"]]`.

### Totals and reports
- Use `record_aggregate` for sums, averages and counts per group (Odoo `read_group`), e.g. revenue per product per month. Do not page through records to add them up.
- Group dates with a granularity: `"invoice_date:month"`. Sort a top-N with `order="price_subtotal:sum desc"`.
- Use `record_export` when the user wants many rows in a spreadsheet or file, or when you must check thousands of rows one by one. It returns compact CSV (Odoo's Export feature) with paths like `"order_id/partner_id/name"`. Write the CSV to a file if the user wants a file.

### Relationships
- Many2one fields return `[id, "display_name"]` or `false`.
- One2many/Many2many fields return list of IDs.
- To set: `(4, id)` = link, `(3, id)` = unlink, `(6, 0, [ids])` = replace all.

### Dates and times
- Date fields: `"2026-04-11"` (string, no time).
- Datetime fields: `"2026-04-11 14:30:00"` (UTC, no timezone).

### Language
- Respect the user's language. If they write in Dutch, respond in Dutch.
- Odoo field values may be in the database language (often English).

### Errors
- A failed call starts with the exception Odoo raised, then Odoo's own message:
  - `UserError` / `ValidationError`: a business rule refused it. Don't retry as is: fix the data or ask the user.
  - `AccessError`: the user lacks the rights. Tell them which access is missing.
  - `MissingError`: the record is gone. Search again; don't recreate it blindly.
  - `NotFound`, `UnprocessableEntity`, `TypeError` or `ValueError`: the call itself is wrong (model, method, argument or field). Fix the call.
- `Outcome unknown` after a write: Odoo may have applied it. Read the record before trying again.

### Translations
- Writing a translatable field with `context={"lang": "nl_BE"}` sets that language's value.
- If English (en_US) is not active in the database, Odoo also writes that value as the base text, and every language without its own translation shows it. This is Odoo's behaviour, the same as editing in its UI.
- To set one language and leave the others alone, call `method_execute` with `method="update_field_translations"`, `ids=[...]`, `kwargs={"field_name": "name", "translations": {"nl_BE": "..."}}`. Check the result with `get_field_translations`.
"""
