---
name: connector-pro-prepare
description: Set Odoo up for a Connector Pro sync (Shopify, Exact Online) with the admin, in phase 2, Prepare. Use when the Set up Odoo page of a Connector Pro app is open, when odoo_setup on a pan.connector.connection points here, or when someone asks to prepare Odoo's VAT, OSS or product categories for a shop or accounting sync.
triggers: [connector pro, pan_connector, set up odoo, setup odoo, odoo setup, prepare, voorbereiden, inrichten, oss, btw-tarieven, vat rates, product categories, productcategorieën, shopify sync, exact sync, recipe, recept]
odoo_modules_any: [pan_connector_pro]
---

# Prepare Odoo for a Connector Pro sync

You are helping the customer's own admin, not a consultant, set Odoo up for a sync. Connector Pro's Set up Odoo page does this in a loop: read what the app has, read what Odoo has, propose, choose, apply, check again. Every step has a button on the page, so the admin can do it by hand. You are faster because you can read every side at once and say where they differ. Lead, one step at a time, and say what you are about to change before you change it.

## What you need connected

| Connector | What you read with it | Needed |
|---|---|---|
| Odoo MCP Pro, on the **staging** Odoo | the setup lines, Odoo's taxes, fiscal positions, categories, accounts; the loop's methods | yes |
| the app's own MCP (Shopify) | orders with their tax lines, products with their types, the shop's settings; to check what Connector Pro read | recommended |
| the previous integration's MCP (Connecta or another) | what it booked for the same orders, to compare | when there is one |

Exact Online has no MCP of its own. Connector Pro reads its VAT codes and item groups into the setup lines; you read those through Odoo MCP Pro.

## The loop, as calls

All through `method_execute`. The app is a `pan.connector.connection`; the setup is a `pan.connector.setup`.

1. `odoo_setup` on the connection: `{setup, state, lines, open, version}`. `setup` is the id the rest is called on. Each line has `id`, `topic` (vat, categories), `key`, `label`, what the app has, what Odoo has, our advice (`proposal`: take, adapt, skip, with `advice` saying why) and the admin's `choice`, `value`, `reason`, `state`.
2. `propose` on the setup: reads both sides again and puts our advice on every line. Keeps every choice somebody made. Call it first, and again after anything changed in the app or in Odoo.
3. `choose(line, choice, value, reason)` on the setup: `take` our advice, `adapt` with the value to use (a tax name, a category path, an account code), or `skip`. Always give a `reason`; it goes into History. A value this Odoo does not have is refused.
4. `apply` on the setup, on staging: makes what is chosen, skips what Odoo has already and says so, opens a line again when it cannot place the value. Installs an app (EU OSS) last. `undo` takes the records out again; an installed app stays.
5. `publish(note)` on the setup: freezes the choices as a setup version. Refuses while a line is open or not applied. The admin signs the version off on the page (Sign off) and takes it to production as a file or through the Pantalytics account.

## The walk

1. **Propose.** Open the app's setup and run Propose. List the lines per topic: what the app has, what Odoo has, our advice, what is open. Change nothing yet.
2. **Explain the advice.** For every VAT line, say which fiscal position Odoo would pick for that country and which tax it maps to. Where our advice says install EU OSS, say in one sentence what that adds. Where it says adapt, name the existing tax or category you would pick and why.
3. **Decide with the admin.** Ask one question at a time. The accountant's choices win (which categories exist, which income account). Record every choice with its reason, dated and attributed ("accountant, 27-10"). Show the lines with their choices before applying.
4. **Apply on staging.** Then read Odoo's taxes, fiscal positions and product categories and confirm what was made. If EU OSS went in, check the EU taxes are mapped to the OSS fiscal positions.
5. **Test a scenario end to end.** Read one consumer order and one business order from another EU country from the shop. For each, say which fiscal position Odoo picks for that customer, which VAT a quotation would carry, and whether the amount matches the shop's. If the old integration booked the same order, compare. Do the same for one product of every category: which category, which income account.
6. **Check again.** Run the readiness checks on the sync. Anything red: say which setup line it points to, and propose again.
7. **Publish and hand over.** Publish with a note that says what was decided ("OSS, categories per accountant"). The admin signs it off and uploads the file on production, or it arrives there through the account. On production you may walk steps 4 to 6 once more, with the production Odoo connected, only after the sign-off.

## What you must not do

- Apply on production without a signed-off setup version from staging.
- Make fiscal positions or taxes by hand. The setup installs the localisation and EU OSS and maps what is missing; a value Odoo does not have becomes an open line, never a guess.
- Choose an income or VAT account without a reason on the line.
- Turn a sync on. That is phase 4, after the checks, by the admin.
