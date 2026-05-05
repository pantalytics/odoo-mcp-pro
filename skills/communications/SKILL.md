---
name: communications
description: Post messages and notes in Odoo chatter, schedule activities, attach files, and manage followers via the external API. Use when the user wants to log a call, leave an internal note, send an email through Odoo, schedule a follow-up, or add an attachment to any record.
triggers: [chatter, message, note, log note, message_post, activity, follow-up, follower, subscribe, attachment, email, mail, send, schedule activity]
odoo_modules_any: [mail]
---

# Communications

How to talk to people through Odoo: post in chatter, send email, schedule
activities, manage followers and attachments — via the external API
(XML-RPC / `mcp_server_odoo`).

For exact field lists, full method signatures, stock activity-type
xmlids and the outbound-flow trace, see [REFERENCE.md](REFERENCE.md).

Almost every business model in Odoo (`res.partner`, `crm.lead`,
`sale.order`, `purchase.order`, `account.move`, `helpdesk.ticket`,
`project.task`, …) inherits the `mail.thread` mixin. If a model has a
chatter in the UI, it has chatter via the API.

## Note vs message — the one decision that matters

| | Internal note | Message |
|---|---|---|
| Subtype xmlid | `mail.mt_note` | `mail.mt_comment` |
| Visible to | Internal users only | Followers (incl. the customer) |
| Notifies followers? | **No** — internal subtype, followers' subtype filter drops it | **Yes** — followers with the subtype get notified |
| Use for | "Called customer, will follow up Friday" | Replying to a customer thread |

If you call `message_post` without `subtype_xmlid`, Odoo defaults to
`mail.mt_note` — silent internal note as far as *followers* are
concerned.

**Email-vs-note is not just the subtype.** Whether email actually goes
out depends on three things, in order:

1. **Subtype** — `mt_comment` fans out to followers whose subscription
   matches; `mt_note` does not.
2. **Explicit `partner_ids`** — anything in `partner_ids` gets a
   `mail.notification` regardless of subtype. *This includes notes.*
   `message_post(subtype_xmlid='mail.mt_note', partner_ids=[X])`
   creates a `mail.notification` + `mail.mail` for X. Treat
   `partner_ids` on a note as "explicit recipients", not "FYI".
3. **Recipient's `notification_type`** — `inbox` (default for internal
   users) puts the notif on the in-app inbox only; `email` (default for
   portal users and partners-without-a-user) creates a `mail.mail` and
   sends. Set per `res.users`.

## Key models

- `mail.thread` — mixin; gives `message_post`, `message_subscribe`, `message_unsubscribe`
- `mail.message` — one chatter entry. Fields: `body` (HTML), `subject`, `message_type`, `subtype_id`, `model`, `res_id`, `author_id`, `partner_ids`, `attachment_ids`, `parent_id`, `email_from`
- `mail.message.subtype` — the `mt_note` / `mt_comment` / `mt_activities` distinction
- `mail.followers` — who follows what. `partner_id` (always partner, never user) + `subtype_ids`
- `mail.notification` — per-recipient delivery state (`sent` / `bounce` / `exception` / `read`) and `notification_type` (`inbox` / `email`)
- `mail.activity` + `mail.activity.type` — to-dos with deadlines (separate from messages)
- `mail.activity.mixin` — gives `activity_schedule`
- `ir.attachment` — files
- `mail.template` — QWeb-rendered email templates
- `ir.mail_server` — outbound SMTP config (vanilla)
- `mail.alias` — inbound email routing

## Recipes

### 1. Internal note (no email sent)

```python
# Default subtype is mt_note when subtype_xmlid is omitted
env['res.partner'].browse(partner_id).message_post(
    body="<p>Called customer. Wants a quote by Friday.</p>",
)
```

### 2. Send a real message to followers (triggers email)

```python
env['sale.order'].browse(order_id).message_post(
    body="<p>Quote v2 attached, please review.</p>",
    subject="Quote update",
    subtype_xmlid='mail.mt_comment',     # required to send email
    message_type='comment',              # user-typed comment
    partner_ids=[customer_partner_id],   # extra recipients on top of followers
    attachment_ids=[attachment_id],
)
```

### 3. Notify specific people without polluting the chatter of a record

Use `message_notify` instead of `message_post` — pushes inbox/email
notifications without rendering on the document.

### 4. Attachments

Two ways. **Over XML-RPC, only the first works** — see Gotchas.

```python
# A. Pre-create ir.attachment, then reference (works everywhere)
att = env['ir.attachment'].create({
    'name': 'quote_v2.pdf',
    'datas': base64_content,            # base64-encoded bytes
    'res_model': 'sale.order',
    'res_id': order_id,
})
record.message_post(body="...", attachment_ids=[att.id])

# B. Inline tuples — only works in-process / over JSON-RPC, NOT XML-RPC
record.message_post(
    body="...",
    attachments=[('quote_v2.pdf', raw_bytes)],   # raw bytes, not base64
)
```

### 5. Followers

Followers are **partners**, not users. To follow as a user, pass that
user's `partner_id`.

```python
record.message_subscribe(partner_ids=[partner_id])
record.message_unsubscribe(partner_ids=[partner_id])
```

### 6. Schedule an activity

```python
env['crm.lead'].browse(lead_id).activity_schedule(
    act_type_xmlid='mail.mail_activity_data_call',   # or _todo, _meeting, _email
    date_deadline='2026-05-12',
    summary='Follow-up call',
    note='<p>Confirm budget.</p>',
    user_id=responsible_user_id,
)
```

Activity types are records of `mail.activity.type`; they have
`category` (`default` / `upload_file` / `phonecall`) which drives UI
behavior. `chaining_type` controls whether marking-done suggests or
auto-creates the next activity.

## Sending vs queueing — the cron

`message_post` synchronously creates the `mail.message`,
`mail.notification`, and `mail.mail` rows. By default, Odoo also
attempts the actual SMTP send within the same request (`force_send=True`
inside `_notify_thread_by_email`). What you observe right after the
call:

- `mail.notification.notification_status` → `sent` (success) or `exception` (failure)
- `mail.mail.state` → `sent` (then auto-deleted, see below) or `exception`

If a `mail.mail` ends up in `outgoing` (no synchronous send attempted,
or got skipped), the **`Mail: Email Queue Manager`** cron picks it up.
Default interval: **1 hour**, both v18 and v19 — not 60 seconds.
`mail.mail` rows in `state='exception'` are **not** retried by the
cron; you must `action_send()` them manually after fixing the cause.

`mail.mail` records have `auto_delete=True` by default — they
disappear from the table after a successful send. If you need to know
"what was actually sent", read `mail.notification`, not `mail.mail`.

To trigger the queue from the API:

```python
env.ref('mail.ir_cron_mail_scheduler_action').method_direct_trigger()
# or, send specific mails synchronously:
env['mail.mail'].browse([id1, id2]).send()
```

## Vanilla Odoo vs Odoo + pan_outlook_pro

The chatter API surface (`message_post`, `activity_schedule`,
attachments, followers) is unchanged. Differences are in transport,
detection, and a few extra fields:

| | Vanilla Odoo | + `pan_outlook_pro` |
|---|---|---|
| Outbound transport | SMTP via `ir.mail_server` | Microsoft Graph API per user/mailbox (with SMTP fallback, see below) |
| `From` address | One server-wide sender, often `notifications@…` | Real mailbox of choice (personal / shared / notification) |
| Inbound | `fetchmail.server` (IMAP/POP) or `mail.alias` MX | Cron `Microsoft Graph: Fetch Incoming Mail` every **1 min**; routes via `mail.alias` |
| OAuth state | n/a | Fernet-encrypted tokens on `res.users` |
| Extra `mail.message` fields | none | `x_microsoft_message_id`, `x_microsoft_conversation_id` (both indexed) |
| `ir.mail_server` after install | unchanged | post-init hook deactivates all existing rows; module installs one placeholder `[Outlook Pro] SMTP Disabled` (active=True, host unresolvable) |

### Three Outlook Pro install stages — different failure modes

What an MCP user sees when posting `mt_comment` depends on which stage
the install is in. Detect via `x_microsoft.mailbox` row count:

| Stage | `x_microsoft.mailbox` (incl. archived) | Mailbox active for current user? | Outbound `mail.mail` ends up | What user observes |
|---|---|---|---|---|
| Installed only | 0 rows | n/a | `state=exception` via SMTP fallback | `failure_reason: "-2\nName or service not known"` (DNS error from placeholder server) |
| Configured, not connected | ≥1 row | no | `state=cancel` | Silently dropped, no notification email, no error |
| Fully configured | ≥1 row | yes | `state=sent` via Graph | `x_microsoft_message_id` populated on the message |

The smart fallback is in `pan_outlook_pro/models/mail_mail.py:80-93`:
no mailbox at all → standard SMTP path. This keeps demo / dev
environments working but produces a confusing DNS-error if SMTP isn't
configured. **If you see `"Name or service not known"` after a fresh
Outlook Pro install, the cause is "no mailbox configured", not "SMTP
broken".**

### Detection

Outlook Pro is installed iff:

- `ir.module.module` row exists with `name='pan_outlook_pro'` and `state='installed'`, **or**
- `ir.model.fields` has `(model='mail.message', name='x_microsoft_message_id')`, **or**
- `ir.model` has a row with `model='x_microsoft.mailbox'`

Stage detection — once installed, count
`x_microsoft.mailbox` (with `active_test=False`) for "any mailbox
ever configured", and search `[('active','=',True)]` on the model
referenced by the mail's responsible user for "configured for sending".

### Other Outlook Pro behavior an MCP user should know

- Inbound emails arriving via Graph can land on any thread-enabled model (routed by `mail.alias`), not only the partner's chatter.
- Sent Items in Outlook are pulled back in as `mail.message` for 2-way sync — don't double-post by also sending a `message_post` for emails the user already sent in Outlook.
- Mass-mailing emails (`mailing.mailing`, e.g. Brevo campaigns) keep going via standard SMTP even when Outlook Pro is fully configured.

## Gotchas

- `partner_ids` everywhere = partners, not users. Convert via `res.users.partner_id`.
- `partner_ids` on a *note* still creates `mail.notification` + `mail.mail` for those partners. The `mt_note` subtype only suppresses the *implicit* follower fan-out, not explicit recipients.
- `body` is HTML. Plain strings are HTML-escaped; pass `markupsafe.Markup(...)` only if you control the source.
- `body_is_html=True` is only for RPC calls where you're forcing string-typed HTML through.
- Tracked-field changes auto-post a system message — don't `message_post` the same change twice.
- `message_type='user_notification'` is reserved for `message_notify`; `message_post` rejects it.
- `outgoing_email_to` (and `incoming_email_to` / `incoming_email_cc`) are **Odoo v19+ only**. On v18 they raise `ValueError: Those values are not supported when posting or notifying`. For pre-v19, add CC recipients as `partner_ids`.
- **Inline `attachments=[(name, raw_bytes)]` does not work over XML-RPC** — Odoo's `_process_attachments_for_post` calls `base64.b64encode(content)` and crashes with `TypeError: a bytes-like object is required, not 'Binary'`. Pre-create `ir.attachment` and pass `attachment_ids=[id]` instead.
- A note (`mt_note`) is hidden from portal/public users via `internal=True` on the subtype.
- Followers without the right subtype subscription won't get notified, even if they follow the record. Subtype subscription = filter, not just on/off.
- `email_from` lets you spoof the visible sender; Odoo will try to make `author_id` and `email_from` coherent. Pass both or neither.
- Activities live on `mail.activity`, not `mail.message`. Closing an activity *posts* a `mt_activities`-subtype message — that's how chatter shows "X done".
- v18 vs v19: same post can produce different notification counts. v18 tends to add the author's partner as an extra notified party; v19 doesn't with the same defaults. Don't trust an exact count — read `mail.notification` rows back if it matters.
- On Outlook Pro, `mail.alias` is still the routing config for inbound — vanilla aliases keep working, just fed by Graph instead of MX.
