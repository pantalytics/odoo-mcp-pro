# Design: Odoo MCP Agent UX Improvements

**Date:** 2026-05-26
**Repo:** `si0nDE/odoo-mcp-pro`
**Status:** Approved

Ported from `si0nDE/mcp-server-odoo` (ivnvxd-based, XML-RPC).
Target is `si0nDE/odoo-mcp-pro` (pantalytics-based, JSON/2 + XML-RPC auto-detect).

---

## Problem

Agents interacting with Odoo via MCP hit three friction points:

1. **Opaque errors** — `sanitize_xmlrpc_fault` converts Odoo application messages to generic strings. `"ValidationError: Field 'vat' is required"` becomes `"Validation error: Please check your input"`. Agents cannot act on this.
2. **Unknown field structure** — No tool to inspect field types before writing. Agents discover Many2many syntax through repeated failures.
3. **Silent M2M failures** — Agents commonly pass `[15, 3]` (flat list), `[4, 15]` (bare command tuple), or `{"add": [15]}` (invented dict syntax) to M2M fields. Odoo either silently ignores them or errors without a useful message.

---

## Design

### Task 1: Error Sanitizer Refactor

**File:** `mcp_server_odoo/error_sanitizer.py`

Replace the `sanitize_xmlrpc_fault` if/elif chain with two-case logic:

- **Access errors** (early exit): `"Access Denied"`, `"AccessDenied"`, `r"\bAccessError\s*:"` → always normalized to `"Access denied: Invalid credentials or insufficient permissions"`. Prevents leaking model names, user IDs, group XML IDs.
- **Legacy `except_orm` tuple** (`"('ValidationError', 'msg')"`) → extract inner message.
- **Repr format** (`"UserError('msg')"`) → extract inner message.
- **Traceback faults** → `re.findall` for all `odoo.exceptions.*` messages, take last (handles chained exceptions). Strip `"During handling of..."` suffix.
- **Bare `XxxError: message`** → strip prefix, return message intact.
- **Unknown** → fall through to `sanitize_message()`.

Three compiled class attributes:
- `_ODOO_EXC_PREFIX_RE` — matches `ValidationError|UserError|MissingError|RedirectWarning|Warning:` prefix (AccessError excluded)
- `_EXCEPT_ORM_RE` — matches `"('ExcType', 'msg')"` legacy format
- `_EXC_REPR_RE` — matches `XxxError('msg')` repr format

One existing test assertion updated:
```python
# Before
assert sanitized == "Validation error: Please check your input"
# After
assert sanitized == "Field 'vat' is required"
```

New test class `TestSanitizeXmlrpcFaultRefactored` with 13 cases covering all branches.

### Task 2: `describe_model` Tool

**Files:** `mcp_server_odoo/schemas.py`, `mcp_server_odoo/tools.py`, `tests/test_describe_model.py`

New read-only MCP tool: `describe_model(model, attributes?)`.

Calls `connection.fields_get(model, attributes)` and returns field metadata with a derived `is_m2m` flag. Agents use this before writing to identify field types and M2M fields.

**Schemas added to `schemas.py`:**
```python
class FieldInfo(BaseModel):
    type: str
    string: str
    required: bool = False
    readonly: bool = False
    relation: Optional[str] = None
    help: Optional[str] = None
    is_m2m: bool = False  # True when type == "many2many"

class DescribeModelResult(BaseModel):
    model: str
    fields: Dict[str, FieldInfo]
    total_fields: int
```

**Tool registration:** After `list_models` (line ~580), before `list_resource_templates`.
- `readOnlyHint=True`, `destructiveHint=False`, `idempotentHint=True`
- Calls `_handle_describe_model_tool`, then `_track_usage(_current_sub.get(), "describe_model")`

**Handler `_handle_describe_model_tool`:** After `_handle_list_models_tool` (line ~1246).
- `_get_user_context()` → connection, access_controller, sub
- `access_controller.validate_model_access(model, "read")` guard
- `connection.is_authenticated` check
- Default attributes: `["string", "type", "required", "readonly", "relation", "help"]`
- `help: ""` from Odoo → `None` in output
- Error handling: `AccessControlError` → `ValidationError`, `OdooConnectionError` → `ValidationError`

**Module-level constant:**
```python
_DESCRIBE_MODEL_DEFAULT_ATTRIBUTES = [
    "string", "type", "required", "readonly", "relation", "help"
]
```

### Task 3: M2M Heuristic Validation

**File:** `mcp_server_odoo/tools.py`

New synchronous method `_validate_m2m_values(model, values)` on `OdooToolHandler`.

Called in `_handle_create_record_tool` and `_handle_update_record_tool` immediately after the empty-values check, before the Odoo write.

Three detection cases (checked in order):

**Case A — bare command tuple** (`[4, 15]` not `[[4, 15]]`):
- `isinstance(value, list) and len(value) == 2 and value[0] in (0,1,2,3,4,5,6) and isinstance(value[1], int)`
- Error: `"Wrap in a list: [[4, 15]]. Use describe_model('model') to confirm '...' is many2many."`
- Checked before Case B because `[4, 15]` also matches "all ints"

**Case B — flat integer list** (`[15, 3]` or `[15]`):
- `isinstance(value, list) and len(value) > 0 and all(isinstance(el, int) for el in value)`
- Error: `"Many2many fields require Odoo command syntax: [[4, id], ...] to add, ..."`

**Case C — dict syntax** (`{"add": [15], "remove": []}`):
- `isinstance(value, dict) and set(value.keys()) <= {"add", "remove", "set"}`
- Error: `"Use Odoo command tuples: [[4, id]] to add, [[6, 0, [ids]]] to replace all."`

Out of scope: `lead_properties` (list-of-dicts, not relational field syntax).

Valid M2M values pass without error: `[[4, 15], [4, 3]]`, `[[6, 0, [15, 3]]]`.

### Task 4: Docstring Improvements

**File:** `mcp_server_odoo/tools.py`

Replace `create_record` and `update_record` docstrings with explicit write-path patterns:

- **M2M fields** (`tag_ids`): `[[4, id]]` add, `[[3, id]]` remove, `[[6, 0, [ids]]]` replace. Flat lists raise an error.
- **Many2one fields** (`source_id`, `country_id`): pass integer ID, not display name. Use `search_records` to resolve names.
- **HTML fields** (`description` in `crm.lead`): pass `"<p>text</p>"`, not plain text.
- **`lead_properties`** (update only): always pass the complete array — Odoo replaces entire field on write.

---

## Architecture Constraints (Pantalytics)

All `_handle_*` methods use `_get_user_context()`:
```python
connection, access_controller, sub = await self._get_user_context()
```

Stdio mode fallback at lines 129-132 in tools.py — `self.connection` / `self.access_controller` used when no registry.

`_track_usage` pattern (after handler call):
```python
self._track_usage(_current_sub.get(), "tool_name")
```

`ValidationError` imported from `.error_handling` (already in scope).

---

## Test Strategy

- Task 1: Update 1 existing assertion + add `TestSanitizeXmlrpcFaultRefactored` (13 cases)
- Task 2: New `tests/test_describe_model.py` (11 cases) + `describe_model` to `test_tools_registered`
- Task 3: Move 4 fixtures from `TestWriteTools` to module scope + add `TestValidateM2mValues` (10 cases)
- Task 4: No tests (docstrings only) — verify with `python -c "from mcp_server_odoo.tools import OdooToolHandler"`

Full suite: `uv run pytest tests/ -v --tb=short`

---

## Execution Order

Tasks are independent. One commit per task.

```
fix: refactor sanitize_xmlrpc_fault to pass Odoo messages intact
feat: add describe_model tool returning field metadata
feat: add M2M heuristic validation to create_record and update_record
docs: add write-path patterns to create_record and update_record docstrings
```
