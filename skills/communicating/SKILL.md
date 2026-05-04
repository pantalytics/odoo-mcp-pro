---
name: communicating
description: Post to the chatter and send email from Odoo records. Use when the user wants to log a note, send an email from a record (lead, sale, invoice, partner...), follow/unfollow a record, render a `mail.template`, or check which mail integration is active (Pantalytics "Outlook Pro" Graph API, OSS Outlook, Gmail, plain SMTP).
triggers: [chatter, message, post, log note, follower, subscribe, email, mail, outlook, gmail, fetchmail, mail server, mail template, smtp, graph api, mailbox]
odoo_modules_any: [mail]
---

# Communicating

Post to a record's chatter, send email, manage followers, route inbound mail.

## Detection: which mail stack is active?

Always check first — outbound flow and shared-mailbox semantics depend on this.

```
search_records(
  model='ir.module.module',
  domain=[['name', 'in', ['pan_outlook_pro', 'microsoft_outlook', 'google_gmail']],
          ['state', '=', 'installed']],
  fields=['name', 'shortdesc']
)
```

| Module installed | Outbound | Inbound | Config model |
|---|---|---|---|
| `pan_outlook_pro` (Pantalytics) | Microsoft Graph API per mailbox | Graph polling per mailbox + alias routing | `x_microsoft.mailbox` |
| `microsoft_outlook` (OSS) | OAuth on `ir.mail_server` (`smtp_authentication='outlook'`) | separate `fetchmail.server` | `ir.mail_server` |
| `google_gmail` | OAuth on `ir.mail_server` | separate `fetchmail.server` | `ir.mail_server` |
| none | Plain SMTP `ir.mail_server` (or none configured) | `fetchmail.server` (IMAP/POP) | `ir.mail_server` |

If `pan_outlook_pro` is installed, do NOT configure `ir.mail_server` for Outlook — the SMTP record is intentionally disabled (`smtp_host='invalid.outlook-pro.disabled'`); routing happens via the Graph client based on the sender mailbox.

## Key models

- `mail.message` — a chatter entry (note, email, system log) on any record
- `mail.mail` — outgoing email queued for the mail-queue cron
- `mail.template` — reusable email body/subject with QWeb rendering
- `mail.followers` — followers of a record (receive notifications)
- `mail.alias` — inbound address that creates/threads records (e.g. `support@`)
- `ir.mail_server` — outbound SMTP / OAuth (NOT used for Outlook Pro)
- `fetchmail.server` — inbound IMAP/POP (NOT used for Outlook Pro)
- `x_microsoft.mailbox` — Outlook Pro mailbox (one per `personal` / `shared` / `notification` mailbox)

## Outlook Pro: mailboxes

When `pan_outlook_pro` is installed:

```
search_records('x_microsoft.mailbox',
  fields=['email', 'x_mailbox_type', 'x_owner_user_id', 'x_alias_id', 'state'])
```

- `x_mailbox_type` ∈ `personal` / `shared` / `notification`
- `state='active'` means OAuth is set up and Graph sync is live
- `x_alias_id` ties inbound mail on a shared mailbox (`support@`, `info@`) to a `mail.alias` so messages create/thread records
- `x_incoming_enabled` / `x_sync_sent` / `x_sync_inbox` toggle direction

Sending from a shared mailbox: set `email_from` on the `mail.mail` to the shared address — Outlook Pro picks the matching mailbox and dispatches via Graph.

## Posting to the chatter (via MCP)

The MCP exposes only CRUD, not `message_post()`. Create `mail.message` directly:

```
create_record(
  model='mail.message',
  values={
    'model': 'crm.lead',
    'res_id': 42,
    'body': '<p>Called the customer.</p>',
    'message_type': 'comment',
    'subtype_id': <id>,         # see below
    'author_id': <res.partner id>,
  }
)
```

Resolve subtype: `search_records('ir.model.data', [['module','=','mail'],['name','in',['mt_note','mt_comment']]], ['name','res_id'])`.
- `mail.mt_note` → internal log, no email to followers
- `mail.mt_comment` → public message, emails followers

## Sending an email

`mail.template.send_mail()` is a server method, not reachable through the MCP. Practical paths:

1. **Direct `mail.mail`** — `create_record('mail.mail', {email_from, email_to, subject, body_html, model, res_id})`. Set `model`+`res_id` so it lands on the record's chatter once sent. The mail-queue cron sends within ~1 min.
2. **From a template** — read `mail.template` `subject` / `body_html`, substitute placeholders client-side, then create `mail.mail` as above. For per-recipient language, see the `translating` skill (rendering uses `partner_id.lang`).

On Outlook Pro: pick `email_from` to match an active `x_microsoft.mailbox`. On other stacks: pick a `mail_server_id` whose `from_filter` allows the sender (or leave empty for default).

## Followers

```
create_record('mail.followers', {
  'res_model': 'sale.order',
  'res_id': 17,
  'partner_id': <partner_id>,
})
```

## Gotchas

- **Subtypes silently change behavior** — `mt_note` hides from customers and skips email; `mt_comment` emails followers. Wrong choice = silent spam or silent miss.
- **`message_type='comment'` vs `'notification'`** — `comment` renders as a user post, `notification` as a system entry.
- **Outlook Pro vs OSS Outlook** — they're alternatives, not layered. If `pan_outlook_pro` is installed, the OSS `microsoft_outlook` `ir.mail_server` config should not be created.
- **Mail queue is async** — `mail.mail` sits in `state='outgoing'` until the cron runs. There is no MCP-reachable way to flush immediately.
- **Threading** — replies thread on `Message-ID` / `In-Reply-To`. Outlook Pro preserves these via Graph; SMTP+IMAP often breaks threading on shared mailboxes.
- **`email_from` must match an allowed sender** — Outlook Pro validates against `x_microsoft.mailbox.email`; SMTP usually doesn't, so spoofs slip through.
- **Aliases** — `mail.alias.alias_model_id` controls what model inbound mail creates (e.g. `crm.lead`); `alias_defaults` is a literal-Python dict, not JSON.
