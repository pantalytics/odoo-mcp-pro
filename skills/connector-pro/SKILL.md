---
name: connector-pro
description: See how a Connector Pro sync (Shopify, Exact Online, Picqer) is doing in Odoo and what to do next. Whether Connector Pro is installed, what failed, what to fix, the next step of setting it up. Use when someone asks about a connector or a sync, or about orders or invoices that didn't come in.
triggers: [connector pro, connector, pan_connector, sync, synchroniseren, shopify, exact online, picqer, is it syncing, what failed, come in]
odoo_modules_any: [pan_connector_pro]
---

# Connector Pro

Connector Pro syncs Odoo with Shopify, Exact Online and Picqer. The module says what to call next, so start from it.

1. Check `model_list` for `pan.connector.sync`. If it isn't there, Connector Pro isn't installed in this Odoo: say so and stop.
2. Call `connectors()` on `pan.connector.sync` with `method_execute`, with no ids.
3. Make the calls it hands you, starting with `status()`, and follow the `call` each answer gives. Where a call is null, tell the person what `instead` (or `next`) says.

Changes happen through the calls `status()` hands you, never by writing records directly.

A null call means a person acts. Tell them what `instead` or `next` says.

Setting Odoo up for a sync (Prepare: `odoo_setup` on the app, for VAT, OSS and product categories) goes the same way. Do it on Odoo Staging, give every choice a reason, and leave Odoo Production alone until the admin has signed the setup off.
