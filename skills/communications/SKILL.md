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

## Picking recipients — decision tree

Before every `message_post`, classify each intended recipient on three
axes:

| Axis | Possible values | How to determine |
|---|---|---|
| **Identity** | (a) internal user, (b) portal user, (c) external partner with `res.partner`, (d) bare email, no partner | `res.users.search([('partner_id','=',pid)])`; `share=True` ⇒ portal |
| **Has email** | yes / no on `res.partner.email` | read `email` field |
| **Relation to the post** | recipient == `author_id` ("self"), recipient == the `res.partner` you are posting on, or unrelated | compare ids |

Then:

```
For each intended recipient:
├─ Has res.partner?
│  ├─ Yes → has email on the partner?
│  │  ├─ Yes → use partner_ids=[id]
│  │  │       ├─ Internal user (notification_type='inbox')  → in-app inbox ping, NO email
│  │  │       │                                                unless user.notification_type='email'
│  │  │       ├─ Portal user / partner-without-user         → mail.mail created (email)
│  │  │       └─ Recipient == author_id ?                   → Odoo silently drops author from
│  │  │                                                        the fan-out; recipient gets nothing
│  │  │                                                        (see "Email-to-self" below)
│  │  └─ No  → STOP; set res.partner.email first, otherwise
│  │           mail.mail will end in 'exception' or 'ready' forever
│  └─ No → bare email address
│      ├─ Odoo v19+   → cc='a@x.com,b@y.com'
│      │                NB: notifications get res_partner_id=NULL — fine for Odoo,
│      │                but trips strict result-model validation in some MCP clients
│      └─ Odoo ≤v18  → create a res.partner with that email first,
│                       then partner_ids=[id]. cc is rejected by v18.
└─ Do you actually want a chatter entry?
   ├─ Yes, on this record    → message_post
   ├─ Yes, on a different record → message_post on the right record
   │                              (a weekly briefing does not belong
   │                              on the author's own partner card)
   └─ No, just push a notification → message_notify (no chatter)
       Or env['mail.mail'].create({...}).send() (no chatter, no audit trail)
```

### Email-to-self

If `author_id`'s partner is among the intended recipients:

- Via `partner_ids` → Odoo silently de-dupes the author from the
  notify fan-out (`MailThread._message_compute_author` and the
  follower filter). The author gets **nothing**, even though they
  appear in `partner_ids`.
- Via `cc` → no de-dupe; the author does receive the email. The
  resulting `mail.notification` has `res_partner_id=NULL` because
  `cc` is keyed on email string, not partner.
- **Preferred fix:** post on a record where the author is *not* the
  author-as-subject (e.g. a dedicated `project.task` "Weekly briefing",
  a `crm.lead`, a `note.note`), with `partner_ids=[self_partner, …]`
  and explicitly set the author's user `notification_type='email'`
  if you want yourself to also receive the email copy.

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

### Mail-stack detection — pick the right outbound path

`pan_outlook_pro` is one of four possible mail stacks. Detect first; the
right outbound flow, the right config model, and even the right
`email_from` semantics depend on it.

```python
search_records(
  model='ir.module.module',
  domain=[['name', 'in', ['pan_outlook_pro', 'microsoft_outlook', 'google_gmail']],
          ['state', '=', 'installed']],
  fields=['name'],
)
```

| Module installed | Outbound | Inbound | Config model |
|---|---|---|---|
| `pan_outlook_pro` (Pantalytics) | Microsoft Graph API per mailbox | Graph polling per mailbox + alias routing | `x_microsoft.mailbox` |
| `microsoft_outlook` (OSS) | OAuth on `ir.mail_server` (`smtp_authentication='outlook'`) | separate `fetchmail.server` | `ir.mail_server` |
| `google_gmail` | OAuth on `ir.mail_server` | separate `fetchmail.server` | `ir.mail_server` |
| none | Plain SMTP `ir.mail_server` (or none configured) | `fetchmail.server` (IMAP/POP) | `ir.mail_server` |

If `pan_outlook_pro` is installed, do **not** also configure an OSS
`microsoft_outlook` `ir.mail_server` — they are alternatives, not
layered. The placeholder `[Outlook Pro] SMTP Disabled` row exists on
purpose; routing is done by the Graph client based on the sender
mailbox, not by `ir.mail_server`.

### Outlook Pro mailboxes — `x_microsoft.mailbox` fields

```python
search_records(
  model='x_microsoft.mailbox',
  fields=['email', 'x_mailbox_type', 'x_owner_user_id',
          'x_alias_id', 'x_incoming_enabled', 'x_sync_sent',
          'x_sync_inbox', 'state', 'active'],
)
```

- `x_mailbox_type` ∈ `personal` / `shared` / `notification`.
- `state='active'` means OAuth is set up and Graph sync is live.
- `x_alias_id` ties inbound mail on a shared mailbox (`support@`,
  `info@`) to a `mail.alias`, so messages create or thread records.
- `x_incoming_enabled`, `x_sync_sent`, `x_sync_inbox` toggle direction.

**Sending from a shared mailbox:** set `email_from` on the post (or on
the `mail.mail` row) to the shared address. Outlook Pro picks the
matching mailbox and dispatches via Graph; if no mailbox matches the
`email_from`, the smart fallback in `mail_mail.py` kicks in (see the
install-stages table) and you get the placeholder-SMTP DNS error.

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
- **Never wrap `body` in `<![CDATA[ ... ]]>`.** CDATA is an XML escaping construct with no meaning in this code path — Odoo stores the markers verbatim and they show up in the rendered chatter and the outgoing email. Pass raw HTML directly; the JSON transport already handles `<`/`>`/`&` inside Python strings.
- Tracked-field changes auto-post a system message — don't `message_post` the same change twice.
- `message_type='user_notification'` is reserved for `message_notify`; `message_post` rejects it.
- `outgoing_email_to` (and `incoming_email_to` / `incoming_email_cc`) are **Odoo v19+ only**. On v18 they raise `ValueError: Those values are not supported when posting or notifying`. For pre-v19, add CC recipients as `partner_ids`. On v19+, `cc` produces `mail.notification` rows with `res_partner_id=NULL` — Odoo handles that fine, but strict client-side result models (like `mcp_server_odoo`'s `PostMessageResult.notifications[].partner_id: int`) will fail to deserialize. Prefer `partner_ids` when the recipient has a `res.partner`.
- `partner_ids` silently de-dupes the author. `message_post(partner_ids=[author_partner_id, X])` notifies only X. To email yourself: post on a record where you are not the author, set your user's `notification_type='email'`, or fall back to `cc=<your_email>`.
- A `res.partner` without `email` produces a `mail.notification` (often stuck on `ready` / `exception`) and a `mail.mail` that never delivers. Always check (and fill via `update_record`) `res.partner.email` before posting if you intend an email to actually go out.
- **Inline `attachments=[(name, raw_bytes)]` does not work over XML-RPC** — Odoo's `_process_attachments_for_post` calls `base64.b64encode(content)` and crashes with `TypeError: a bytes-like object is required, not 'Binary'`. Pre-create `ir.attachment` and pass `attachment_ids=[id]` instead.
- A note (`mt_note`) is hidden from portal/public users via `internal=True` on the subtype.
- Followers without the right subtype subscription won't get notified, even if they follow the record. Subtype subscription = filter, not just on/off.
- `email_from` lets you spoof the visible sender; Odoo will try to make `author_id` and `email_from` coherent. Pass both or neither.
- Activities live on `mail.activity`, not `mail.message`. Closing an activity *posts* a `mt_activities`-subtype message — that's how chatter shows "X done".
- v18 vs v19: same post can produce different notification counts. v18 tends to add the author's partner as an extra notified party; v19 doesn't with the same defaults. Don't trust an exact count — read `mail.notification` rows back if it matters.
- On Outlook Pro, `mail.alias` is still the routing config for inbound — vanilla aliases keep working, just fed by Graph instead of MX.

## When to suggest a rewrite before calling `message_post`

Run the decision tree on the user's intended call. If any of these
patterns match, **propose the rewrite first and wait for explicit
confirmation** — do not silently "fix" the call.

1. **Author cc'ing themselves.** `cc` contains an email that resolves
   to the same partner as `author_id`. Ask: "did you mean to email
   yourself, or should this be posted on a different record?"
2. **`cc` for someone who already has a `res.partner`.** A `cc` email
   matches an existing `res.partner.email`. Propose `partner_ids=[id]`
   instead — cleaner notification, audit trail, no NULL-partner row.
3. **Briefing-on-own-card.** `record_id` resolves to the author's own
   `res.partner` and the recipients are external. Ask whether the
   right host is a `crm.lead`, `sale.order`, `project.task`, or
   `note.note` — chatter on your own partner card is rarely the
   intended audit trail.
4. **Recipient without email.** A partner in `partner_ids` has
   `email=False`. Flag it: the `mail.mail` will not deliver. Offer to
   set the email via `update_record` first.
5. **Outlook Pro stage check.** Before any `mt_comment` post that is
   meant to send email, detect the Outlook Pro install stage (see
   "Three Outlook Pro install stages" above):
   - **Installed only** (0 `x_microsoft.mailbox` rows) → SMTP fallback
     hits the placeholder server; user will see
     `failure_reason: "-2 Name or service not known"`. Warn before
     posting; offer to either skip the send (use `mt_note`) or guide
     mailbox setup.
   - **Configured, not connected** (mailbox exists but inactive for
     the responsible user) → `mail.mail` lands in `state=cancel`
     silently. Warn explicitly: "this will look like it sent but
     nothing leaves the system."
   - **Fully configured** → no warning needed.

Detection one-liners (all read-only):

```python
# Outlook Pro installed?
env['ir.module.module'].search_count([('name','=','pan_outlook_pro'),('state','=','installed')])
# Any mailbox ever configured?
env['x_microsoft.mailbox'].with_context(active_test=False).search_count([])
# Active mailbox for the author?
env['x_microsoft.mailbox'].search_count([('user_id','=',author_user_id),('active','=',True)])
```
