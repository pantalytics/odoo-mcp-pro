# ADR 0003: One error contract for failed tool calls

- Status: Accepted
- Date: 2026-09-01
- Deciders: Rutger
- Scope: `error_sanitizer.py`, `error_handling.py`, `tools/*.py`, both
  connection backends, and the admin repo's quota/connect errors.

## Context

A user ran a `sale.order` tool call, it failed, and the message they got back
told them nothing. That is not a one-off. Today every failure mode -- a
business rule, a missing permission, a bad field name, a daily cap, a dead
Odoo -- arrives at the AI client as one shape: a raised `ValidationError`
whose text has usually been stripped of the only useful part.

### What actually happens now

Trace of an Odoo business error over XML-RPC (Odoo 14-18):

| Step | Value |
|------|-------|
| Odoo raises | `UserError("You cannot confirm a sale order with no lines.")` |
| `sanitize_xmlrpc_fault` | `"Operation failed due to business rule violation"` |
| `execute_kw` wraps | `OdooConnectionError("Operation failed: Operation failed due to business rule violation")` |
| `tools/methods.py` wraps | `ValidationError("Connection error: Operation failed: Operation failed due to business rule violation")` |
| **User sees** | **`Connection error: Operation failed: Operation failed due to business rule violation`** |

Odoo told us exactly what was wrong. We deleted it, then labelled it a
connection error, which invites the agent to retry a write that will never
succeed.

### Five defects, verified against the code

1. **`sanitize_xmlrpc_fault` replaces Odoo's own message with boilerplate.**
   `UserError` -> "Operation failed due to business rule violation".
   `ValidationError` -> "Validation error: Please check your input". The
   `UserError` extraction regex expects `UserError('...')`; a real Odoo
   faultString is a traceback ending in `odoo.exceptions.UserError: <message>`,
   so it never matches. Odoo's message is the most actionable string in the
   whole system and it is the one we throw away.

2. **`ERROR_MAPPINGS` ships a literal `{}` to the user.** The catch-all
   `r"Failed to execute .+ on .+: .+"` -> `"Operation failed: {}"` has no
   capture group, and `_extract_relevant_info` returns `None` for it, so the
   user gets the string `Operation failed: {}`. Reproduced:

   ```
   in : "Failed to execute create on sale.order: Some required fields are missing: Customer"
   out: "Operation failed: {}"
   ```

3. **Every failure is flattened to `ValidationError`.** 95 raise sites across
   `tools/`. `AccessControlError`, `OdooConnectionError`, quota refusals and
   genuine bugs all land in the same class with a per-tool English prefix
   ("Search failed: ", "Connection error: "). The categories in
   `error_handling.py` (`PERMISSION`, `RATE_LIMIT`, `CONNECTION`, ...) exist
   and are never used on the tool path.

4. **Nothing tells the agent whether to retry.** A daily cap (retry after
   midnight), a timeout (retry now), a business rule (never retry) and a bad
   field name (fix the argument) are indistinguishable. The agent guesses, and
   on writes a wrong guess is a duplicate record or a double post.

5. **JSON/2 (Odoo 19+) drops the machine-readable half.** Odoo answers with
   `{"name": "odoo.exceptions.UserError", "message": ..., "arguments": [...]}`.
   `_parse_error_response` keeps `message`, discards `name` and `arguments`,
   then hands the text to the same lossy sanitizer.

### Why the sanitizer went wrong

It conflates two jobs: **redacting** secrets and internals (right, keep) and
**rewriting** messages into friendlier prose (wrong, drop). Rewriting is done
by regex on English error text, which is both fragile and destructive. The
transport layer already knows the error class from the HTTP status or the
fault, so classification never needed to guess from prose.

## Decision

One error envelope, one taxonomy, classified where the type is known.

### 1. Taxonomy

`error_kinds.py` (new, small). Every failure carries exactly one kind:

| kind | Source | Retry? | Agent should |
|------|--------|--------|--------------|
| `input_invalid` | our own arg checks | no | fix the arguments |
| `unknown_model_or_field` | Odoo `KeyError` / invalid leaf | no | call `list_models` / read `odoo://<model>/fields` |
| `business_rule` | `UserError`, `ValidationError` | no | show the message; fix the data or ask the user |
| `permission_denied` | `AccessError`, HTTP 403 | no | tell the user which group is missing |
| `record_missing` | `MissingError`, HTTP 404 | no | re-search, do not re-create blindly |
| `auth_failed` | `AccessDenied`, HTTP 401 | no | send the user to setup |
| `odoo_unreachable` | connect/DNS failure | yes, backoff | wait, then one retry |
| `odoo_timeout` | socket / request timeout | yes, once | narrow the query (lower `limit`) |
| `odoo_server_error` | HTTP 5xx, psycopg | yes, once | report if it repeats |
| `quota_exceeded` | admin daily cap | at `retry_after` | show the upgrade line, stop calling |
| `not_supported` | unvetted wizard | no | surface the support CTA (already correct today) |
| `server_bug` | anything unclassified | no | report it |

### 2. Envelope

```python
@dataclass
class OdooToolError(Exception):
    kind: ErrorKind
    message: str          # Odoo's own words, redacted, never rewritten
    model: str | None
    method: str | None
    hint: str | None      # what to do next, ours
    retry_after: int | None
```

Rendered to the one string the client shows:

```
[business_rule] You cannot confirm a sale order with no lines.
where: sale.order.action_confirm
retry: no -- add at least one order line, then confirm again.
```

Machine-readable enough for an agent to branch on, readable enough for the
human watching the chat. Same shape for every tool, so the client never sees a
per-tool prefix again.

### 3. Classify at the transport, not at the tool

- XML-RPC: parse the fault's terminal `odoo.exceptions.<Class>: <message>` line
  -> kind + message. Keep the message verbatim.
- JSON/2: map `name` -> kind, keep `message`, append `arguments` when present.
- The tool layer stops re-wrapping. One decorator on the handlers converts
  anything unclassified into `server_bug` and renders the envelope.

### 4. Sanitizer keeps redaction only

`ErrorSanitizer` loses `ERROR_MAPPINGS` and the "friendly rewrite" path. It
keeps: file paths, line numbers, tracebacks, memory addresses, module paths,
and gains an explicit redaction pass for API keys, session ids and passwords.
Redact, never replace. `sanitize_xmlrpc_fault` becomes a classifier that
returns `(kind, message)` instead of a rewritten string.

### 5. Admin repo follows the same envelope

`RateLimitExceeded` becomes `quota_exceeded` with `retry_after` = seconds to
midnight UTC; its conversion copy stays exactly as written. The registry's
connect errors already carry good copy and an `error_type` token -- map those
tokens onto the same kinds so `usage_events.error_type` and what the user
reads finally agree, and the existing PostHog dashboards keep working.

## Consequences

- **Positive:** the agent gets Odoo's real message plus a retry decision. Most
  "the AI got stuck" cases become self-service. Failure telemetry
  (`learning/fingerprint.py`) fingerprints a stable `kind` instead of drifting
  English. Support load drops because the message names the fix.
- **Negative:** Odoo's messages are longer and less polished than our
  boilerplate. Accepted -- true and long beats short and useless.
- Tests that assert the boilerplate change:
  `test_error_sanitizer.py:84`, `test_error_sanitization_integration.py:55,89`,
  `test_error_handling.py:302`, `test_access_control.py:391`.
- Public repo change means a tag + a pin bump in the admin repo.

## Explicitly not doing

- **JSON-RPC protocol errors** (`ErrorData`, code -32000). Clients render those
  as transport failures and often hide the text. Tool failures stay tool
  results with `isError`.
- **Retry/backoff inside the server.** We say `retry_after`; the client
  decides. A server that retries writes on its own is a duplicate-record bug.
- **Structured content alongside the text.** Worth doing later; the text
  contract above carries the same information and works on every client today.
- **Translating error messages.** Odoo already returns them in the user's
  language.

## Phases

| # | Change | Repo | Size |
|---|--------|------|------|
| 1 | Delete `ERROR_MAPPINGS` catch-all; keep Odoo's `UserError` / `ValidationError` text; stop prefixing business errors with "Connection error" | public | small |
| 2 | `error_kinds.py` + classify in both transports | public | medium |
| 3 | Envelope + one decorator; remove the 95 ad-hoc re-wraps | public | medium |
| 4 | Admin: quota + connect errors adopt the envelope | admin | small |
| 5 | Fingerprint on `kind` in the failure corpus | admin | small |

Phase 1 alone removes the reported symptom and is worth shipping on its own.
