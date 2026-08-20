# ADR 0003: Visuals - images now, MCP Apps next

- Status: Accepted (phase 1), Proposed (phases 2-4)
- Date: 2026-08-20
- Deciders: Rutger
- Scope: `tools/images.py` (new), later `apps/` (new), `tools/query.py`,
  packaging in `pyproject.toml`, and the admin package's usage tracking.

## Context

Everything this server returns today is text or JSON. Odoo is full of things
that are easier to look at than to read: product photos, partner avatars,
company logos, pipelines, revenue per month, an invoice PDF. The MCP
specification has grown two separate ways to send those, and they are worth
keeping apart because they cost us very different amounts of work.

**Core content types.** `ImageContent` (base64 plus a mime type), `AudioContent`,
resource links and embedded resources have been part of the protocol since the
first revision. Any client that speaks MCP renders them. We simply never used
them: binary and image fields are filtered out of every read path
(`tools/formatting.py`, `formatters.py`, `resources/retrieval.py`) and
`tools/binary.py` is upload-only.

**MCP Apps** (extension identifier `io.modelcontextprotocol/ui`, SEP-1865,
extension spec revision 2026-01-26). A tool carries `_meta.ui.resourceUri`
pointing at a `ui://` resource whose mime type is `text/html;profile=mcp-app`.
The host renders that HTML in a sandboxed iframe, pushes the tool result to it
(`ui/notifications/tool-result`, where `structuredContent` is meant for the UI
and `content` for the model), and the iframe may call back with `tools/call`,
`resources/read`, `ui/open-link`, `ui/message`, `ui/request-display-mode` and
`ui/update-model-context`. Hosts that render apps today: Claude web and
desktop, ChatGPT, VS Code Copilot, Microsoft 365 Copilot, Cursor, Goose,
Postman, MCPJam.

Two findings shaped this ADR:

1. **No SDK upgrade is required.** `FastMCP.tool(..., meta=...)` and
   `FastMCP.resource(..., mime_type=..., meta=...)` exist in mcp 1.27.2, which
   is our current floor pin. The official `ext-apps` repository ships a Python
   example (`qr-server`) built on `mcp.server.fastmcp.FastMCP` that does exactly
   this. The mcp 2.0 / 2026-07-28 migration (stateless core, `server/discover`,
   MRTR) is a separate track and does not block visuals.
2. **Structured output has to be switched off for image tools.** FastMCP derives
   an output schema from the return annotation, and would then send the same
   base64 twice: once as content, once as `structuredContent`. Passing
   `structured_output=False` returns unstructured content only.

## Decision

Ship visuals in four phases, smallest first, each independently useful.

### Phase 1 - images out of Odoo (this ADR's accepted part)

A read-only `get_image` tool that reads an image field and returns
`ImageContent`, mirroring `set_binary_field` on the write side.

- Sizes come from Odoo's own variants (`image_128` ... `image_1920`), so we
  never resize server-side and need no imaging dependency. Default `size=512`,
  which is the useful/cheap trade-off: an image costs model tokens on every
  turn it stays in context.
- Hard cap of 5 MB per image, with an error that names the fix (ask for a
  smaller `size`). This matches what hosts accept per image.
- The mime type is sniffed from the bytes, not guessed from the field name.
  Only PNG, JPEG, GIF and WebP are returned; anything else is refused with a
  message that says what was found, because a client cannot render it anyway.
- Non-image binaries (PDF, spreadsheets) are explicitly out of scope for this
  phase. They need a resource link rather than inline bytes.

### Phase 2 - one MCP App pilot

A single `ui://` view plus the `_meta.ui.resourceUri` wiring, behind an env
flag so self-hosters and clients without the extension are unaffected. The
first view is a record/list viewer over the result of `search_records`, not a
dashboard: our tools already return Pydantic models, so the server-side change
is a `meta=` argument plus one HTML resource. Every app tool must keep
returning meaningful text as well, since graceful degradation is required by
the extension spec.

Rules for that phase, decided now:

- No npm build step and no new Python dependency. The view is a single HTML
  file with inline JS talking the postMessage JSON-RPC dialect directly, or a
  vendored bundle; it ships in the wheel through
  `[tool.hatch.build.targets.wheel.force-include]`, the same mechanism the
  `skills` directory already uses.
- Views live in the public package. The admin package pins a tag, so anything
  we want hosted has to be released here first.
- App-initiated `tools/call` traffic re-enters our normal tool path, so
  `_get_user_context` and `_track_usage` keep working unchanged. Note for
  billing: clicks inside a view are real tool calls and count against the daily
  limit. That is correct but it needs to be said out loud in the admin repo
  before the pilot goes live.

### Phase 3 - aggregation and charts

Charts need aggregated data. We have no `read_group` tool (only
`execute_method`), so a small read-only aggregation tool comes first, and the
chart view renders its `structuredContent`.

### Phase 4 - write preview and documents

A confirm-before-write view (show the diff, then confirm) fits "Odoo is the
boss" and reduces blind writes. Document rendering (invoice or quotation PDF)
is deliberately last: Odoo's report endpoints expect session authentication,
which is its own investigation.

## Consequences

- Images cost tokens. Defaults stay small and the tool docstring says so.
- One more tool in the list for every user, including self-hosters. Acceptable:
  it is read-only, annotated as such, and mirrors an existing write tool.
- Phase 2 introduces the first HTML asset in a Python package. The packaging
  precedent exists (`skills`), and CI must fail if the asset is missing from
  the wheel.
- Nothing here depends on the stateless 2026-07-28 core, so the two tracks can
  proceed in either order.

## Sources

- MCP specification 2026-07-28 changelog and extensions overview
  (`io.modelcontextprotocol/ui` negotiation).
- MCP Apps extension specification, revision 2026-01-26 (`ui://` resources,
  `text/html;profile=mcp-app`, `_meta.ui.*`, the `ui/` method dialect).
- Extension client support matrix.
- `modelcontextprotocol/ext-apps`, `examples/qr-server` (Python server on
  `mcp.server.fastmcp`).
- `modelcontextprotocol/python-sdk` v1.27.2 source: `FastMCP.tool`,
  `FastMCP.resource`, `func_metadata.convert_result`.
