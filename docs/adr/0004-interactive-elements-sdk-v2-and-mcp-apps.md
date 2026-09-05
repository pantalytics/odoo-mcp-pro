# ADR 0004: Interactive elements -- SDK v2 first, then one MCP App

- Status: Proposed
- Date: 2026-09-05
- Deciders: Rutger
- Relationship: refines [ADR 0002](0002-stateful-sessions-and-elicitation.md).
  Replaces its step 4 ("reshape `followup` to `InputRequiredResult` when the SDK
  exposes it") and promotes its "evaluate MCP Apps" line into a decision.
- Scope: `pyproject.toml` (the `mcp` pin), `server.py`, `tools/methods.py`,
  `tools/wizards.py`, and the `create_fastmcp_app` seam the admin repo imports.

## Context

ADR 0002 was written against the 2026-07-28 **release candidate**. Three things
have changed since, and two of them move the work.

1. **The spec is final** (2026-07-28). Multi Round-Trip Requests are the
   mechanism: a tool returns `resultType: "input_required"` with `inputRequests`
   and an opaque `requestState`, and the client re-issues the original call with
   `inputResponses`. Sessions and the initialize handshake are gone. Roots,
   sampling and legacy elicitation are deprecated with a twelve-month window.
   Nothing here contradicts ADR 0002 -- we are already stateless, and we already
   dropped `ctx.elicit`, which now raises `NoBackChannelError` anyway.

2. **The Python SDK v2 shipped and v1 is done.** `mcp` 2.0.0 landed with the
   spec on 2026-07-28; 2.1.1 is current (2026-08-25). MRTR is native there, with
   `requestState` sealed by authenticated encryption by default. The 1.x line is
   in maintenance mode and receives security fixes only. **We pin
   `mcp>=1.27.2,<2`**, so we are on the dead line.

3. **MCP Apps is Final and Claude renders it.** SEP-1865, extension id
   `io.modelcontextprotocol/ui`, stable since 2026-01-26 and shipped as an
   official extension in the 2026-07-28 release. A tool declares
   `_meta.ui.resourceUri`, the server serves a `ui://` resource holding bundled
   HTML, and the host renders it in a sandboxed iframe that can call tools back.
   Claude, Claude Desktop, VS Code Copilot and Goose support it. This, not
   elicitation, is what "interactive elements" means today.

### Why ADR 0002's step 4 was wrong as written

It assumed a rename inside `methods.py`: `followup` -> `inputRequests`,
`decision` -> `inputResponses`. That is not the work, for two reasons.

- The types live in SDK v2 only. On our pin they do not exist.
- Hand-rolling the JSON on v1 buys nothing. A client honours `input_required`
  because the SDK negotiates it, not because the payload has the right key
  names. We would carry the cost and get none of the UX.

So step 4 *is* the v2 migration, and nothing smaller.

### What the v2 migration costs

`FastMCP` becomes `MCPServer`, `mcp.types` moves to a standalone `mcp-types`
package, `McpError` becomes `MCPError`, and every field goes camelCase ->
snake_case (`inputSchema` -> `input_schema`, `isError` -> `is_error`), with
`by_alias=True` needed on serialization. `server.create_fastmcp_app` is a
documented open-core seam that `odoo-mcp-pro-admin` imports, so this is a
two-repo change coordinated through a version tag, not a single PR.

## Decision

Three steps, in this order, one at a time.

1. **Migrate to `mcp` 2.x.** Justified on its own: the line we are pinned to
   gets security fixes only. Keep `create_fastmcp_app` as the seam name behind
   the renamed class so the admin import survives; ship it as a tagged release
   and bump the admin pin in the same session.
2. **Then take MRTR for free.** The existing two-step becomes the native
   `input_required` / `inputResponses` round trip. Keep accepting the current
   `decision` / `followup` shape for the twelve-month deprecation window, so
   clients that have not moved keep working.
3. **Then exactly one MCP App**, as a spike: **register payment** as a real form
   (journal, amount, date) instead of the two-step text dance. One wizard, one
   customer, on Odoo 19. If Claude renders it and the payment posts, widen to
   the other four wizards. If it does not, stop and stay on MRTR.

**Dropped on purpose:** MCP Apps for the read side -- search results as sortable
tables, dashboards over `search_records`. It demos well and adds nothing an ERP
user cannot already get as text. Odoo's own UI is one click away and better at
it. Interactive elements earn their place where the alternative is a
back-and-forth the user cannot see, which is wizards.

## Open question, answered by half a day

Whether a **Python** server can declare `_meta.ui.resourceUri` on a tool and
serve a `ui://` resource at all. The official build guide and the
`@modelcontextprotocol/ext-apps` helpers are TypeScript. SDK v2 says "MCP Apps
built in" through its extensions API, and a community `mcp-ui-server` package
exists for Python, but there is no first-party Python example. The HTML itself
is language-agnostic, so the risk is the declaration, not the UI. Answer this
before step 3 is scheduled, not during it.

## Consequences

- One migration unblocks both interactive paths. Doing MCP Apps first would
  mean doing it twice, since the extension plumbing lives in v2.
- The admin repo is blocked on our tag for as long as the migration takes. Ship
  it as its own release rather than folding other changes in.
- We stay stateless throughout. Nothing here reopens ADR 0001 or 0002 on that.

## Plan

1. Migrate `server.py` and the tool/resource handlers to `mcp` 2.x; keep the
   seam names. Tag a release. Bump the admin pin.
2. Reshape `methods.py`'s follow-up onto MRTR, backward compatible.
3. Spike the Python `ui://` question (above).
4. Ship register-payment as one MCP App; decide on the other four from what it
   does with a real customer.

## Sources

- The 2026-07-28 Specification (final):
  https://blog.modelcontextprotocol.io/posts/2026-07-28/
- SEP-2322, Multi Round-Trip Requests:
  https://github.com/modelcontextprotocol/modelcontextprotocol/pull/2322
- Python SDK v2.0.0 release notes:
  https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0
- Python SDK v1 -> v2 migration guide:
  https://py.sdk.modelcontextprotocol.io/migration/
- MCP Apps overview and build guide:
  https://modelcontextprotocol.io/extensions/apps/overview
- MCP Apps announcement (SEP-1865 final):
  https://blog.modelcontextprotocol.io/posts/2026-01-26-mcp-apps/
