# Communications — Reference

Deep-dive companion to [SKILL.md](SKILL.md). Load this when you need
exact field names, signatures, or stock xmlids.

## `mail.thread.message_post` signature

```python
record.message_post(
    body='',                    # str | Markup — HTML, plain str gets escaped
    subject=None,
    message_type='notification',# email | comment | email_outgoing | notification | auto_comment | out_of_office
                                # NOT 'user_notification' (use message_notify)
    email_from=None,            # override visible sender
    author_id=None,             # res.partner id of author
    parent_id=False,            # mail.message id to thread under
    subtype_xmlid=None,         # 'mail.mt_note' (default) | 'mail.mt_comment' | 'mail.mt_activities'
    subtype_id=False,           # numeric alternative to subtype_xmlid
    partner_ids=None,           # [partner_id, …] — extra recipients
    outgoing_email_to=False,    # comma-separated emails (Odoo v19)
    incoming_email_to=False,    # comma-separated; already-notified emails
    incoming_email_cc=False,
    attachments=None,           # [(name, raw_bytes), …] OR [(name, raw_bytes, info_dict), …]
    attachment_ids=None,        # [ir.attachment id, …] — alternative to attachments=
    body_is_html=False,         # only for RPC: forces str body to HTML
    **kwargs                    # extra mail.message fields, or notify kwargs
)
```

Returns the created `mail.message` record.

`subject` defaults to record `display_name` for non-note message types.

## `mail.thread.message_notify` signature

Same as `message_post` minus `message_type` (always `user_notification`),
`parent_id`, `outgoing_email_to`. Plus `model` and `res_id` so you can
notify without a record. **Notification only** — does not appear in the
record's chatter.

## `mail.activity.mixin.activity_schedule` signature

```python
record.activity_schedule(
    act_type_xmlid='',          # 'mail.mail_activity_data_call', etc.
    date_deadline=None,         # date (not datetime); defaults to today
    summary='',
    note='',
    user_id=...,                # responsible user (default: activity_type.default_user_id)
    **act_values                # any other mail.activity field
)
```

Stock activity types (defined in `mail/data/mail_activity_type_data.xml`):

| xmlid | Name | Category | Default delay |
|---|---|---|---|
| `mail.mail_activity_data_email` | Email | default | 0 |
| `mail.mail_activity_data_call` | Call | phonecall | 2 days |
| `mail.mail_activity_data_meeting` | Meeting | default | 0 |
| `mail.mail_activity_data_todo` | To-Do | default | 5 days |
| `mail.mail_activity_data_upload_document` | Document | upload_file | 5 days |
| `mail.mail_activity_data_warning` | Exception | default | 0 (inactive by default) |

`category='upload_file'` triggers a file uploader in the UI and
auto-marks done on upload. `category='phonecall'` opens click-to-dial
where applicable.

Mark done: `record.activity_feedback(act_type_xmlids, feedback='...')`
or `activity.action_done()` on the `mail.activity` record.

## `mail.message` — full field cheat sheet

Content
- `subject` (Char), `body` (Html, sanitized), `preview` (Char, computed)
- `attachment_ids` (M2M `ir.attachment`)
- `parent_id` / `child_ids` (threading)

Related document
- `model` (Char), `res_id` (Many2oneReference), `record_name` (computed)
- `record_alias_domain_id`, `record_company_id`

Characteristics
- `message_type` (Selection — see above)
- `subtype_id` (M2O `mail.message.subtype`)
- `mail_activity_type_id` (when posted by activity completion)
- `is_internal` (Bool — hide from portal/public independent of subtype)

Origin
- `author_id` (M2O `res.partner`), `author_guest_id` (M2O `mail.guest`)
- `email_from` (Char — used when no matching partner)

Recipients
- `partner_ids` (M2M `res.partner`)
- `incoming_email_to` / `incoming_email_cc` / `outgoing_email_to`
- `notified_partner_ids` (computed from notifications, may decay via gc)
- `notification_ids` (O2M `mail.notification`)

UI
- `starred_partner_ids`, `pinned_at`, `starred`

Tracking
- `tracking_value_ids` (O2M `mail.tracking.value`) — auto-populated on tracked-field writes

Mail gateway
- `message_id` (Char — the RFC `Message-ID:` header)
- `reply_to`, `reply_to_force_new`
- `mail_server_id` (M2O `ir.mail_server`)
- `email_layout_xmlid`, `email_add_signature`
- `mail_ids` (O2M `mail.mail`)

## `mail.message.subtype` — the three stock subtypes

Defined in `mail/data/mail_message_subtype_data.xml`:

| xmlid | name | internal | default | Use |
|---|---|---|---|---|
| `mail.mt_comment` | Discussions | False | True | Real message to followers (sends email) |
| `mail.mt_note` | Note | True | False | Internal note (no email, hidden from portal) |
| `mail.mt_activities` | Activities | True | False | Auto-posted when activities are completed |

Apps add their own model-scoped subtypes (e.g. `sale.mt_order_confirmed`,
`crm.mt_lead_won`). Followers subscribe to subtypes; matching subtypes
on a posted message determine who gets notified.

## `mail.followers`

| Field | Notes |
|---|---|
| `res_model`, `res_id` | What is being followed |
| `partner_id` | Always a partner — never a user |
| `subtype_ids` | M2M of subscribed `mail.message.subtype` |

To make a user follow: subscribe their `partner_id`.

## `mail.notification`

Per-recipient delivery row created for each follower notified by a message.

| Field | Values |
|---|---|
| `notification_type` | `inbox`, `email`, `sms`, `snail`, `web_push` |
| `notification_status` | `ready`, `process`, `pending`, `sent`, `bounce`, `exception`, `canceled` |
| `failure_type` | populated on bounce/exception |
| `is_read` | inbox read state |

User preference at `res.users.notification_type` decides inbox vs email.

## `ir.attachment` — relevant fields

| Field | Notes |
|---|---|
| `name` | Filename |
| `datas` | Base64-encoded content |
| `raw` | Binary content (alternative to `datas`) |
| `mimetype` | Auto-detected if omitted |
| `res_model`, `res_id` | Owning record (set so attachment appears in the record's Documents tab) |
| `public` | If True, accessible without auth via URL |

When passed as `attachments=[(name, content)]` to `message_post`, content
is **raw bytes**, not base64. Internally Odoo creates the `ir.attachment`
with `res_model` / `res_id` matching the chatter record.

**XML-RPC caveat**: bytes sent via XML-RPC arrive on the server as
`xmlrpc.client.Binary`, not `bytes`. Odoo's
`mail_thread._process_attachments_for_post` then calls
`base64.b64encode(content)` and crashes with
`TypeError: a bytes-like object is required, not 'Binary'`. **Over
XML-RPC, always pre-create `ir.attachment` and pass `attachment_ids=[id]`.**
JSON-RPC and JSON/2 (Odoo 19) deserialize binary differently and may
accept the inline form — confirm before relying on it.

## Detecting Outlook Pro

Probe in this order:

1. `ir.module.module` search `[('name','=','pan_outlook_pro'),('state','=','installed')]`
2. If `ir.module.module` access is restricted, check field existence:
   `ir.model.fields` search `[('model','=','mail.message'),('name','=','x_microsoft_message_id')]`
3. Or check model existence: `ir.model` search `[('model','=','x_microsoft.mailbox')]`

### Three install stages — pick which one before predicting send behavior

```python
mailbox_count_total  = env['x_microsoft.mailbox'].with_context(active_test=False).search_count([])
mailbox_count_active = env['x_microsoft.mailbox'].search_count([])
```

| Stage | total | active | Outbound `mail.mail.send()` lands at |
|---|---|---|---|
| Installed only | 0 | 0 | Standard SMTP — falls onto the `[Outlook Pro] SMTP Disabled` placeholder server (host unresolvable) → `state=exception`, `failure_reason: "-2\nName or service not known"` |
| Configured, none active | ≥1 | 0 | `state=cancel` (silently dropped) |
| Configured + active mailbox + user has token | ≥1 | ≥1 | Microsoft Graph (`x_microsoft_message_id` populated on success) |

The fallback logic is in `pan_outlook_pro/models/mail_mail.py` `send()`.
A "DNS error" right after a fresh install means stage 1, not a real
SMTP problem.

### Other install effects

- `ir.mail_server`: post-init hook sets `active=False` on every existing
  row, then loads one own placeholder named `[Outlook Pro] SMTP Disabled`
  with `active=True`. **Do not interpret this as "SMTP is configured"** —
  the placeholder is intentionally unusable.
- `mail.message` gains `x_microsoft_message_id` and `x_microsoft_conversation_id`
  (both indexed). Module-managed; don't write from the MCP.
- `x_microsoft.mailbox` lists configured mailboxes. UI "Send From" =
  `mail.compose.message.x_microsoft_send_from_id`; default mailbox per
  user via `res.users.x_microsoft_default_mailbox_id`.
- A new cron `Microsoft Graph: Fetch Incoming Mail` runs every **1 min**
  (inbound only — outbound still uses `Mail: Email Queue Manager`,
  default 1 hour).
- Mass-mailing emails (`mailing.mailing`-driven, e.g. Brevo campaigns)
  are explicitly excluded from the Graph route and continue through
  standard SMTP, even when Outlook Pro is fully configured.

## `ir.mail_server` (vanilla outbound config)

Fields the API can inspect to understand current outbound setup:

| Field | Notes |
|---|---|
| `name`, `from_filter` | Used to pick which server handles which `email_from` |
| `smtp_host`, `smtp_port`, `smtp_encryption` | Connection details |
| `smtp_user`, `smtp_pass` | Credentials |
| `active` | False on Outlook Pro install |
| `sequence` | Server selection priority |

## `mail.alias` (inbound routing)

| Field | Notes |
|---|---|
| `alias_name` + `alias_domain_id` | Local part + domain — together form the address |
| `alias_model_id` | Target model (e.g. `helpdesk.ticket`, `crm.lead`) |
| `alias_defaults` | Python literal dict of field defaults applied to `message_new()` |
| `alias_contact` | `everyone` / `partners` / `followers` — who is allowed to post |
| `alias_force_thread_id` | Always post to this record id, never create new |

Both vanilla (MX → Odoo `mailgateway`) and Outlook Pro (Graph poll →
processor) feed inbound mail through `mail.alias` for routing.

## `mail.template` — sending a templated email

```python
template = env.ref('module.template_xmlid')
template.send_mail(
    record_id,
    force_send=True,           # send synchronously (default: queue)
    email_values={             # override on resulting mail.mail
        'email_to': '...',
        'attachment_ids': [...],
    },
)
```

`send_mail` returns the `mail.mail` id. Templates QWeb-render `body_html`,
`subject`, `email_from`, `email_to`, etc. with the record in context.

## Outbound flow recap

```
message_post(subtype=mt_comment)
  └─ creates mail.message
       └─ _notify_thread()
            ├─ for inbox followers → mail.notification (notification_type='inbox')
            └─ for email followers → mail.notification (notification_type='email')
                 └─ mail.mail row created
                      └─ synchronously sent in same request
                         (force_send=True default in _notify_thread_by_email)
                          ├─ success → state='sent' → row deleted (auto_delete=True)
                          ├─ failure → state='exception' (NOT retried by cron)
                          └─ skipped → state='outgoing'
                                       (picked up by Mail: Email Queue Manager,
                                        default interval 1 hour)
                              ├─ vanilla: ir.mail_server → SMTP
                              └─ Outlook Pro: branches on x_microsoft.mailbox
                                  (see "Three install stages" above)
```

**Key consequences:**

- After `message_post`, the `mail.mail` row may already be gone
  (auto_delete on success). To audit "what was sent", read
  `mail.notification` rows on the message.
- An exception is sticky: re-trigger via `mail.mail.action_send()` or
  recreate the message. The cron will not retry exceptions.
- To force-send queued mails from the API:
  ```python
  env.ref('mail.ir_cron_mail_scheduler_action').method_direct_trigger()
  # or send specific rows synchronously:
  env['mail.mail'].browse([id1, id2]).send()
  ```

## What auto-creates messages without you calling message_post

- **Tracked fields** — writing a tracked field on a `mail.thread` record
  posts a system message with `tracking_value_ids`. Don't post a
  duplicate "I changed X to Y" message.
- **Stage transitions** — many models (CRM, Sales, Project) post
  module-specific subtype messages on stage changes.
- **Activity completion** — `activity.action_done()` posts a
  `mt_activities`-subtype message.
- **Inbound email** — gateway / Outlook Pro processor calls
  `message_new()` or `message_update()` which posts the email body.
- **Order confirmations / invoice posting** — workflow methods often post.

When automating, read the chatter to verify what already exists before
adding your own message.
