# ADR 0002: Stateless elicitation for wizard follow-ups (aligned with MCP 2026-07-28)

- Status: Proposed; step 4 superseded by [ADR 0004](0004-interactive-elements-sdk-v2-and-mcp-apps.md)
- Date: 2026-06-18
- Deciders: Rutger
- Relationship: refines [ADR 0001](0001-stateless-no-elicitation.md) (keeps its
  stateless transport; reverses its "no elicitation, ever" stance).
- Scope: `tools/wizards.py`, `tools/methods.py`, the Streamable HTTP transport
  in `server.py`, OAuth/DCR.

## Context

Some Odoo methods return a wizard (validate delivery -> backorder?; register
payment -> journal/amount/date?). We want the nice interactive UX (the server
asks, the user/agent answers) without making it fragile, and we want a path
that survives blue-green deploys. This is mostly financial data, so it must be
exactly right.

We considered going **stateful** (sessions + `ctx.elicit`) to get that UX. That
was wrong, and online evidence (see Sources) is decisive.

## Findings (source-validated + ecosystem)

1. **The SDK cannot do stateful sessions across replicas, and won't.** Sessions
   live in process memory (`StreamableHTTPSessionManager._server_instances`,
   `streamable_http_manager.py` L90; unknown id -> "session not found" L238/264).
   A Redis `EventStore` only resumes a dropped SSE stream, not the session. The
   maintainers' own issue **#880** (open, **P1**) is exactly "sessions don't
   scale horizontally" — left unfixed because the protocol is removing sessions.

2. **MCP 2026-07-28 RC removes sessions.** The `Mcp-Session-Id` header and the
   initialize handshake are eliminated; every request is self-contained and can
   land on any instance (no sticky routing, no shared session store). Final
   spec ships 2026-07-28 (RC locked 2026-05-21), with a 12-month deprecation
   window. Building sticky-session infra now is building on a deleted feature.

3. **Elicitation survives, restructured to be stateless.** Instead of an open
   SSE stream, the server returns an `InputRequiredResult` (prompts +
   `requestState`); the client collects answers and **resubmits the original
   call** with `inputResponses` + the echoed state, so any instance can process
   the retry. This is the same two-step shape `execute_method` already uses
   (return the wizard's `followup` fields -> client re-calls with `decision`).

4. **OAuth/DCR is hardening** (the other half of "everyone moves to Streamable
   HTTP + OAuth/DCR"): `iss` validation per RFC 9207, `application_type` declared
   at Dynamic Client Registration (fixes web-vs-native misclassification that
   bites localhost/CLI redirect URIs), issuer-bound credentials. We already do
   Streamable HTTP + Zitadel OAuth + DCR, so we are on the right transport/auth;
   these are concrete refinements to adopt.

## Decision

**Stay stateless. Do not add sessions.** Implement wizard follow-ups as a
stateless two-step (the pattern the RC standardizes), and converge its shape
onto the official `InputRequiredResult` / `inputResponses` as clients adopt it.

- Keep `stateless_http=True`. No Caddy affinity, no `mcp_sessions` table, no
  Postgres session/event store. (This also keeps zero-downtime blue-green and
  trivial self-hosting.)
- `execute_method` for plain methods (`action_confirm`, `action_post`, ...)
  ships now — most of the value, no follow-up needed.
- Wizard follow-ups stay the two-step `decision`/`followup` flow, made
  rock-solid (see the `tools/wizards.py` context fix: the wizard context must
  travel with the completion call, not only `create()` — verified on Odoo 19).
- Track the RC: when `InputRequiredResult`/`inputResponses` lands in the SDK and
  in Claude/ChatGPT clients, align our followup payload to it so the UX becomes
  the native "the client shows a form" experience — still stateless.
- Evaluate the **Tasks extension** (stateless long-running work: server returns
  a task handle, client polls `tasks/get`) for genuinely long Odoo operations,
  and **MCP Apps** (sandboxed server-rendered forms) as a future richer wizard
  UX. Both are stateless-native.
- Adopt the OAuth/DCR hardening (RFC 9207 `iss`, `application_type` at DCR) in
  `oauth.py`/`dcr.py` as a separate follow-up.

## Consequences

- We get the interactive UX the stateless way the protocol is standardizing —
  no throwaway session infra, no fighting the 2026-07-28 direction.
- "Fewer methods, but rock-solid": each wizard ships only after source-check +
  live test on the full customer matrix (**v18 and v19, Community and
  Enterprise**) because it is financial data.
- The earlier silent-false-success bug class is handled by real-Odoo tests, not
  mocks (mocks passed while the live backorder validated nothing).

## Plan (incremental, each step version-tested)

1. Keep the `tools/wizards.py` completion-context fix (verified on v19).
2. Add **real-Odoo integration tests** for each wizard (the kind that caught the
   backorder no-op), run on v18 + v19, Community + Enterprise.
3. Ship the safe set: `execute_method` (plain methods) + **register-payment**
   (verified v19) + **backorder** (fixed, verified v19) once matrix-tested.
4. ~~When the SDK exposes `InputRequiredResult`, reshape `methods.py`'s
   `followup` response to it.~~ Superseded by ADR 0004: the types ship in SDK
   v2 only, so this is the v2 migration, not a rename.
5. Separately: OAuth/DCR hardening per the RC.

## Sources

- MCP 2026-07-28 Release Candidate (official):
  https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/
- python-sdk issue #880 (stateful sessions don't scale horizontally, open P1):
  https://github.com/modelcontextprotocol/python-sdk/issues/880
- MCP Authorization (OAuth 2.1 / DCR):
  https://modelcontextprotocol.io/specification/draft/basic/authorization
