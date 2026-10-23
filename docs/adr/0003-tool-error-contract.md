# ADR 0003: One error contract for failed tool calls

- Status: Accepted, amended 2026-10-19 (see the end: Odoo's own exception
  names replace the custom taxonomy)
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
    message: str  # Odoo's own words, redacted, never rewritten
    model: str | None
    method: str | None
    hint: str | None  # what to do next, ours
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

## Amendment 2026-10-19: use the names MCP and Odoo already define

Decided by Rutger after the error audit for Element X (ticket #237, PR #178):
follow the official MCP SDK and Odoo error conventions as closely as
possible, instead of a taxonomy of our own. This replaces sections 1-3 and
phases 2-3 above. Sections 4 (redact, never rewrite) and "explicitly not
doing" stand.

**MCP side: the spec already has the split.** The tools spec (Error Handling)
separates protocol errors (unknown tool, malformed request: JSON-RPC error,
`-32602`) from tool execution errors (API failures, input validation,
business logic: a result with `isError: true` the model can act on). The SDK
does both: an unknown tool or arguments that fail the input schema are
handled by the SDK itself, and a `ToolError` becomes `isError: true` with our
text. `as_tool_error` turns every handler failure into a `ToolError`, so
nothing reaches the client as a bare "Error executing tool X".

**Odoo side: the kind is the exception class Odoo raised.** Odoo already
names every failure: `UserError`, `ValidationError`, `AccessError`,
`MissingError`, `AccessDenied` (`odoo.exceptions`), werkzeug's `NotFound` and
`UnprocessableEntity` for an unknown model, method or argument on JSON/2, and
plain Python (`TypeError`, `ValueError`) for a bad call. The message reads
the way Odoo says it, `<ExceptionClass>: <Odoo's message>`, on both
transports, and the class is on `OdooExecutionError.error_type`:

| Transport | Where the class comes from |
|-----------|----------------------------|
| JSON/2 (19+) | the envelope's `name` (`odoo.exceptions.ValidationError` -> `ValidationError`); without one, the status (403 AccessError, 404 NotFound, 422 UserError) |
| XML-RPC (14-18) | the fault code (2 UserError and its subclasses, 3 AccessDenied, 4 AccessError); code 1 is a traceback whose last line names the class |

A custom kind such as `business_rule` would be one more vocabulary for the
model to learn, a lossy one (`ValidationError` and `UserError` both became
`business_rule`), and ours alone. Odoo's names are documented, appear in
every Odoo forum answer the model was trained on, and are what the user sees
in Odoo's own dialog.

**What stays ours** (no Odoo class exists for it): pre-flight checks before
Odoo is called (plain sentences that say how to fix the call), `AccessError:`
for a refusal by our access check, `Authentication failed:` on 401 (the admin
layer's connect checks match on it), `Connection error:` for a transport
failure on a read, and `Outcome unknown: ... check before retrying` for one on
a write, which replaces the `retry` line of the envelope where it matters.

| # | Change | Status |
|---|--------|--------|
| 1 | Keep Odoo's text, stop "Connection error" on business errors | done (PR #178) |
| 2 | Odoo's exception class on both transports, `error_type` attribute | done (PR #178) |
| 3 | Custom envelope and taxonomy | dropped for the above |
| 4 | Admin quota and connect errors | open |
| 5 | Fingerprint failures on `error_type` | open |

