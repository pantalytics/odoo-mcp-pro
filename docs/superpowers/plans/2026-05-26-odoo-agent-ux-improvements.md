# Odoo MCP Agent UX Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port four agent UX improvements from `si0nDE/mcp-server-odoo` to `si0nDE/odoo-mcp-pro`, improving error clarity, field discoverability, and M2M write-path safety.

**Architecture:** Four independent tasks, each committed separately. All code changes land in `mcp_server_odoo/` — no new top-level modules. Tests follow existing patterns: class-based with `@pytest.mark.asyncio` for async handlers.

**Tech Stack:** Python 3.10+, pytest, pytest-asyncio, uv, pydantic v2, FastMCP

---

## File Map

| File | Task | Change |
|------|------|--------|
| `mcp_server_odoo/error_sanitizer.py` | 1 | Add 3 compiled regexes; replace `sanitize_xmlrpc_fault` |
| `tests/test_error_sanitizer.py` | 1 | Update 1 assertion; add `TestSanitizeXmlrpcFaultRefactored` |
| `mcp_server_odoo/schemas.py` | 2 | Append `FieldInfo` + `DescribeModelResult` |
| `mcp_server_odoo/tools.py` | 2, 3, 4 | Import; constant; tool registration; handler; validation method; docstrings |
| `tests/test_describe_model.py` | 2 | New file — 11 test cases |
| `tests/test_tools.py` | 2 | Add `"describe_model"` to `test_tools_registered` |
| `tests/test_write_tools.py` | 3 | Move 4 fixtures to module scope; add `TestValidateM2mValues` |

---

## Task 1: Error Sanitizer Refactor

**Goal:** Replace `sanitize_xmlrpc_fault` if/elif chain with two-case logic that passes Odoo application messages through intact instead of mangling them into generic strings.

**Files:**
- Modify: `mcp_server_odoo/error_sanitizer.py`
- Modify: `tests/test_error_sanitizer.py`

**Acceptance Criteria:**
- [ ] `"ValidationError: Field 'vat' is required"` → `"Field 'vat' is required"` (prefix stripped, content intact)
- [ ] Traceback faults extract the last `odoo.exceptions.*` message
- [ ] Access errors always normalize to `"Access denied: Invalid credentials or insufficient permissions"`
- [ ] `None` input → `"An error occurred"`
- [ ] Non-Odoo tracebacks fall through to `sanitize_message`
- [ ] All 13 new tests pass; existing `test_xmlrpc_fault_sanitization` passes with updated assertion

**Verify:** `uv run pytest tests/test_error_sanitizer.py -v` → all green

---

**Steps:**

- [ ] **Step 1: Update the existing failing assertion**

In `tests/test_error_sanitizer.py` line 84, change:

```python
# BEFORE (line 84)
assert sanitized == "Validation error: Please check your input"

# AFTER
assert sanitized == "Field 'vat' is required"
```

- [ ] **Step 2: Add `TestSanitizeXmlrpcFaultRefactored` at end of `tests/test_error_sanitizer.py`**

Append this entire class after the last line of the file:

```python


class TestSanitizeXmlrpcFaultRefactored:
    """Tests for the refactored sanitize_xmlrpc_fault behavior."""

    def test_bare_message_passes_through(self):
        fault = "Invalid field 'mobile' in 'crm.lead'"
        assert ErrorSanitizer.sanitize_xmlrpc_fault(fault) == "Invalid field 'mobile' in 'crm.lead'"

    def test_validation_error_prefix_stripped(self):
        fault = "ValidationError: Field 'vat' is required"
        assert ErrorSanitizer.sanitize_xmlrpc_fault(fault) == "Field 'vat' is required"

    def test_missing_error_prefix_stripped(self):
        fault = "MissingError: Record does not exist or has been deleted."
        assert ErrorSanitizer.sanitize_xmlrpc_fault(fault) == "Record does not exist or has been deleted."

    def test_traceback_fault_extracts_message(self):
        fault = (
            "Traceback (most recent call last):\n"
            '  File "/opt/odoo/addons/crm/models/crm_lead.py", line 42, in write\n'
            "    super().write(values)\n"
            "odoo.exceptions.ValidationError: Mandatory field 'Partner' is missing\n"
        )
        assert ErrorSanitizer.sanitize_xmlrpc_fault(fault) == "Mandatory field 'Partner' is missing"

    def test_chained_exceptions_takes_last(self):
        fault = (
            "Traceback (most recent call last):\n"
            "  ...\n"
            "odoo.exceptions.ValidationError: First error\n"
            "  ...\n"
            "odoo.exceptions.UserError: Final user error\n"
        )
        assert ErrorSanitizer.sanitize_xmlrpc_fault(fault) == "Final user error"

    def test_except_orm_legacy_format(self):
        fault = "('ValidationError', 'Cannot process this record')"
        assert ErrorSanitizer.sanitize_xmlrpc_fault(fault) == "Cannot process this record"

    def test_user_error_repr_format_preserved(self):
        fault = "UserError('Cannot delete record that has dependencies')"
        assert ErrorSanitizer.sanitize_xmlrpc_fault(fault) == "Cannot delete record that has dependencies"

    def test_access_denied_normalized_strips_internals(self):
        fault = "Access Denied\nModel: res.users\nUID: 42\nGroup: base.group_system"
        result = ErrorSanitizer.sanitize_xmlrpc_fault(fault)
        assert result == "Access denied: Invalid credentials or insufficient permissions"
        assert "res.users" not in result
        assert "UID" not in result

    def test_access_error_normalized(self):
        fault = "AccessError: You don't have access to 'res.users' (uid=1)"
        result = ErrorSanitizer.sanitize_xmlrpc_fault(fault)
        assert result == "Access denied: Invalid credentials or insufficient permissions"
        assert "res.users" not in result

    def test_empty_string_input(self):
        assert ErrorSanitizer.sanitize_xmlrpc_fault("") == "An error occurred"

    def test_none_input(self):
        assert ErrorSanitizer.sanitize_xmlrpc_fault(None) == "An error occurred"

    def test_non_odoo_traceback_falls_to_sanitize_message(self):
        fault = (
            "Traceback (most recent call last):\n"
            '  File "/opt/odoo/server/odoo/sql_db.py", line 302, in execute\n'
            '    cr.execute(query, params)\n'
            "psycopg2.errors.UniqueViolation: duplicate key value violates unique constraint\n"
        )
        result = ErrorSanitizer.sanitize_xmlrpc_fault(fault)
        assert "/opt/odoo" not in result
        assert "line 302" not in result
        assert len(result) > 5

    def test_access_error_substring_in_message_does_not_suppress(self):
        """'AccessError' appearing in application message must not trigger normalization."""
        fault = "UserError('Resolve the AccessError before retrying')"
        result = ErrorSanitizer.sanitize_xmlrpc_fault(fault)
        assert "Resolve" in result
```

- [ ] **Step 3: Run tests to confirm failures**

```bash
uv run pytest tests/test_error_sanitizer.py -v 2>&1 | tail -20
```

Expected: `test_xmlrpc_fault_sanitization` fails (assertion mismatch), `TestSanitizeXmlrpcFaultRefactored` fails (old implementation returns wrong values).

- [ ] **Step 4: Add three compiled regexes inside `ErrorSanitizer` class**

In `mcp_server_odoo/error_sanitizer.py`, insert these three attributes immediately before the line `PATTERNS_TO_REMOVE = [` (currently line 18):

```python
    # Matches "XxxError: msg" or "odoo.exceptions.XxxError: msg" bare prefixes.
    # AccessError is intentionally excluded — access errors are always normalised
    # in the early-exit block to avoid leaking model names, user IDs, etc.
    _ODOO_EXC_PREFIX_RE = re.compile(
        r"^(?:odoo\.exceptions\.)?(?:ValidationError|UserError|"
        r"MissingError|RedirectWarning|Warning):\s*(.+)",  # AccessError removed
        re.DOTALL,
    )

    # Matches legacy except_orm tuple: "('ExcType', 'user message')"
    # Emitted by some community modules on older Odoo versions.
    _EXCEPT_ORM_RE = re.compile(
        r"^\(['\"][\w ]+['\"],\s*['\"](.+?)['\"]\)\s*$", re.DOTALL
    )

    # Matches XxxError('msg') or XxxError('title', 'detail') repr formats.
    # Restricted to the same allowlist as _ODOO_EXC_PREFIX_RE so stdlib exceptions
    # are not treated as Odoo application messages.
    _EXC_REPR_RE = re.compile(
        r"^(?:odoo\.exceptions\.)?(?:ValidationError|UserError|"
        r"MissingError|RedirectWarning|Warning)\(['\"](.+?)['\"]"
        r"(?:,\s*['\"][^'\"]*['\"])?\)\s*$",
        re.DOTALL,
    )

```

- [ ] **Step 5: Replace `sanitize_xmlrpc_fault` entirely**

Find the entire `sanitize_xmlrpc_fault` classmethod (lines 209–243) and replace it with:

```python
    @classmethod
    def sanitize_xmlrpc_fault(cls, fault_string: str) -> str:
        """Sanitize XML-RPC fault messages from Odoo.

        Strips the Python runtime layer (tracebacks, file paths, exception
        class name prefixes) while passing Odoo application messages through
        intact. The only content replacement is Access Denied: Odoo's messages
        sometimes leak internal model names, user IDs, and group XML IDs.
        """
        if not fault_string:
            return "An error occurred"

        # Always normalize access errors — content may leak internal Odoo details
        # (model names, user IDs, group XML IDs)
        if (
            "Access Denied" in fault_string
            or "AccessDenied" in fault_string
            or bool(re.search(r"\bAccessError\s*:", fault_string))
        ):
            return "Access denied: Invalid credentials or insufficient permissions"

        stripped = fault_string.strip()

        # Legacy except_orm tuple: "('ValidationError', 'user message')"
        # Emitted by some community modules on older Odoo versions.
        m = cls._EXCEPT_ORM_RE.match(stripped)
        if m:
            return m.group(1).strip()

        # XxxError('msg') repr format, e.g. UserError('Cannot delete ...')
        m = cls._EXC_REPR_RE.match(stripped)
        if m:
            return m.group(1).strip()

        # Case 1: traceback fault — extract the last odoo.exceptions.* message.
        # re.findall + [-1] handles chained exceptions; re.DOTALL captures
        # multi-line messages (field lists etc.). Non-greedy to stop at the
        # next exception line so chained exceptions resolve to the last one.
        if "Traceback (most recent call last)" in fault_string:
            matches = re.findall(
                r"odoo\.exceptions\.\w+:\s*(.+?)(?=\nodoo\.exceptions\.|$)",
                fault_string,
                re.DOTALL,
            )
            if matches:
                message = matches[-1].strip()
                # Trim any "During handling of the above exception..." suffix
                message = re.sub(
                    r"\n+\s*During handling.*", "", message, flags=re.DOTALL
                ).strip()
                return message or "An error occurred while processing your request"
            # Traceback but no odoo.exceptions line — strip Python layer via generic sanitisation
            return cls.sanitize_message(fault_string)

        # Case 2: bare "XxxError: message" without traceback
        m = cls._ODOO_EXC_PREFIX_RE.match(stripped)
        if m:
            return m.group(1).strip()

        # Case 3: no recognisable pattern — apply generic sanitisation
        # (strips file paths, module paths, class names, memory addresses)
        return cls.sanitize_message(fault_string)
```

- [ ] **Step 6: Run tests to confirm all pass**

```bash
uv run pytest tests/test_error_sanitizer.py -v
```

Expected: all tests green, including the 13 new cases and the updated assertion.

- [ ] **Step 7: Commit**

```bash
git add mcp_server_odoo/error_sanitizer.py tests/test_error_sanitizer.py
git commit -m "fix: refactor sanitize_xmlrpc_fault to pass Odoo messages intact"
```

---

## Task 2: `describe_model` Tool

**Goal:** Add a new read-only `describe_model` MCP tool that calls `fields_get` and returns field metadata with a derived `is_m2m` flag, enabling agents to inspect models before writing.

**Files:**
- Modify: `mcp_server_odoo/schemas.py` — append `FieldInfo` + `DescribeModelResult`
- Modify: `mcp_server_odoo/tools.py` — import, constant, tool registration, handler method
- Create: `tests/test_describe_model.py` — 11 test cases
- Modify: `tests/test_tools.py` — add `"describe_model"` to `test_tools_registered`

**Acceptance Criteria:**
- [ ] `describe_model("crm.lead")` returns `DescribeModelResult` with `model`, `fields`, `total_fields`
- [ ] `is_m2m` is `True` for `many2many` fields, `False` for all others
- [ ] Empty `help` string from Odoo becomes `None` in output
- [ ] `validate_model_access(model, "read")` is called for every request
- [ ] `AccessControlError` → `ValidationError("Access denied: ...")`
- [ ] `OdooConnectionError` → `ValidationError("Connection error: ...")`
- [ ] `is_authenticated = False` → `ValidationError("Not authenticated with Odoo")`
- [ ] `"describe_model"` appears in `test_tools_registered`

**Verify:** `uv run pytest tests/test_describe_model.py tests/test_tools.py -v` → all green

---

**Steps:**

- [ ] **Step 1: Create `tests/test_describe_model.py`**

```python
"""Tests for the describe_model tool."""

from unittest.mock import Mock

import pytest

from mcp_server_odoo.access_control import AccessControlError
from mcp_server_odoo.error_handling import ValidationError
from mcp_server_odoo.odoo_connection import OdooConnectionError
from mcp_server_odoo.tools import OdooToolHandler


FAKE_FIELDS_GET = {
    "name": {
        "type": "char",
        "string": "Contact Name",
        "required": False,
        "readonly": False,
        "help": "",
    },
    "tag_ids": {
        "type": "many2many",
        "string": "Tags",
        "required": False,
        "readonly": False,
        "relation": "crm.tag",
        "help": "",
    },
    "stage_id": {
        "type": "many2one",
        "string": "Stage",
        "required": True,
        "readonly": False,
        "relation": "crm.stage",
        "help": "Current pipeline stage.",
    },
    "probability": {
        "type": "float",
        "string": "Probability",
        "required": False,
        "readonly": True,
        "help": "Automatically updated based on stage.",
    },
}


@pytest.fixture
def handler():
    app = Mock()
    app.tool = Mock(side_effect=lambda **kwargs: lambda f: f)
    connection = Mock()
    connection.is_authenticated = True
    connection.fields_get = Mock(return_value=FAKE_FIELDS_GET)
    access_controller = Mock()
    access_controller.validate_model_access = Mock()
    config = Mock()
    return OdooToolHandler(app, connection, access_controller, config)


class TestDescribeModelHandler:

    @pytest.mark.asyncio
    async def test_returns_model_and_fields(self, handler):
        result = await handler._handle_describe_model_tool("crm.lead")
        assert result["model"] == "crm.lead"
        assert "name" in result["fields"]
        assert result["fields"]["name"]["type"] == "char"
        assert result["fields"]["name"]["string"] == "Contact Name"
        assert result["fields"]["name"]["help"] is None

    @pytest.mark.asyncio
    async def test_m2m_has_is_m2m_true(self, handler):
        result = await handler._handle_describe_model_tool("crm.lead")
        assert result["fields"]["tag_ids"]["is_m2m"] is True

    @pytest.mark.asyncio
    async def test_non_m2m_has_is_m2m_false(self, handler):
        result = await handler._handle_describe_model_tool("crm.lead")
        assert result["fields"]["name"]["is_m2m"] is False

    @pytest.mark.asyncio
    async def test_readonly_field_included(self, handler):
        result = await handler._handle_describe_model_tool("crm.lead")
        assert "probability" in result["fields"]
        assert result["fields"]["probability"]["readonly"] is True

    @pytest.mark.asyncio
    async def test_relation_passed_through(self, handler):
        result = await handler._handle_describe_model_tool("crm.lead")
        assert result["fields"]["stage_id"]["relation"] == "crm.stage"

    @pytest.mark.asyncio
    async def test_total_fields_count(self, handler):
        result = await handler._handle_describe_model_tool("crm.lead")
        assert result["total_fields"] == 4

    @pytest.mark.asyncio
    async def test_access_control_called(self, handler):
        await handler._handle_describe_model_tool("crm.lead")
        handler.access_controller.validate_model_access.assert_called_once_with(
            "crm.lead", "read"
        )

    @pytest.mark.asyncio
    async def test_custom_attributes_passed_to_fields_get(self, handler):
        await handler._handle_describe_model_tool("crm.lead", attributes=["string", "type"])
        handler.connection.fields_get.assert_called_once_with("crm.lead", ["string", "type"])

    @pytest.mark.asyncio
    async def test_access_control_error_raises_validation_error(self, handler):
        handler.access_controller.validate_model_access.side_effect = AccessControlError(
            "No access to crm.lead"
        )
        with pytest.raises(ValidationError, match="Access denied:"):
            await handler._handle_describe_model_tool("crm.lead")

    @pytest.mark.asyncio
    async def test_connection_error_raises_validation_error(self, handler):
        handler.connection.fields_get.side_effect = OdooConnectionError("Connection failed")
        with pytest.raises(ValidationError, match="Connection error:"):
            await handler._handle_describe_model_tool("crm.lead")

    @pytest.mark.asyncio
    async def test_not_authenticated_raises_validation_error(self, handler):
        handler.connection.is_authenticated = False
        with pytest.raises(ValidationError, match="Not authenticated with Odoo"):
            await handler._handle_describe_model_tool("crm.lead")
```

- [ ] **Step 2: Run tests to confirm import fails**

```bash
uv run pytest tests/test_describe_model.py -v 2>&1 | head -20
```

Expected: `AttributeError` — `_handle_describe_model_tool` does not exist yet.

- [ ] **Step 3: Append `FieldInfo` and `DescribeModelResult` to `mcp_server_odoo/schemas.py`**

Add at the very end of the file (after line 277):

```python


# --- Describe Model ---


class FieldInfo(BaseModel):
    """Metadata for a single field on an Odoo model."""

    type: str = Field(description="Odoo field type (char, integer, many2one, many2many, html, ...)")
    string: str = Field(description="Human-readable field label")
    required: bool = Field(default=False, description="Whether the field is required")
    readonly: bool = Field(default=False, description="Whether the field is read-only on write")
    relation: Optional[str] = Field(
        default=None, description="Related model name (many2one/many2many/one2many only)"
    )
    help: Optional[str] = Field(default=None, description="Odoo field tooltip / documentation")
    is_m2m: bool = Field(
        default=False,
        description=(
            "True for many2many fields — these require [[4,id]] command syntax on write"
        ),
    )


class DescribeModelResult(BaseModel):
    """Result of describing an Odoo model's fields."""

    model: str = Field(description="Odoo model name")
    fields: Dict[str, FieldInfo] = Field(description="Field metadata keyed by field name")
    total_fields: int = Field(description="Total number of fields returned")
```

- [ ] **Step 4: Add `DescribeModelResult` to the schemas import in `mcp_server_odoo/tools.py`**

Find the `from .schemas import (` block (lines 33–49) and add `DescribeModelResult` in alphabetical order:

```python
from .schemas import (
    BinaryFieldResult,
    BulkCreateResult,
    BulkDeleteResult,
    BulkUpdateResult,
    CreateResult,
    DeleteResult,
    DescribeModelResult,
    FieldSelectionMetadata,
    ImportResult,
    ModelsResult,
    PostMessageResult,
    RecordResult,
    ResourceTemplatesResult,
    SearchResult,
    ServerInfoResult,
    UpdateResult,
)
```

- [ ] **Step 5: Add `_DESCRIBE_MODEL_DEFAULT_ATTRIBUTES` constant in `mcp_server_odoo/tools.py`**

After line 64 (`_current_sub: contextvars.ContextVar[str] = ...`), add:

```python
# Default field attributes fetched by describe_model.
# Excluded: store, domain, context, selection_ids, groups, depends (noisy).
_DESCRIBE_MODEL_DEFAULT_ATTRIBUTES = [
    "string", "type", "required", "readonly", "relation", "help"
]
```

- [ ] **Step 6: Register `describe_model` tool in `_register_tools()`**

Insert the following block between line 589 (`return ModelsResult(**result)`) and line 591 (`@self.app.tool(`):

```python
        @self.app.tool(
            title="Describe Model",
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )
        async def describe_model(
            model: str,
            attributes: Optional[List[str]] = None,
        ) -> DescribeModelResult:
            """Returns field metadata for an Odoo model.

            Use this before writing to an unfamiliar model to identify field
            names, types, and many2many fields (which require [[4,id]] command
            syntax on write).

            is_m2m is true for many2many fields — these require Odoo command
            tuple syntax on write. HTML fields have type "html".

            readonly: true fields are returned for reference but must not be
            passed to create_record or update_record — Odoo silently ignores
            them and the sanitizer will not surface an error.

            Args:
                model: The Odoo model name (e.g., 'crm.lead')
                attributes: Field attributes to include. Defaults to
                    ["string", "type", "required", "readonly", "relation", "help"].
                    Pass additional Odoo field attributes (e.g. "selection") if needed.

            Returns:
                DescribeModelResult with field metadata keyed by field name
                and a total_fields count.
            """
            result = await self._handle_describe_model_tool(model, attributes)
            self._track_usage(_current_sub.get(), "describe_model")
            return DescribeModelResult(**result)

```

- [ ] **Step 7: Add `_handle_describe_model_tool` method**

Insert this method between line 1244 (`raise ValidationError(f"Failed to list models: ...")`) and line 1246 (`async def _handle_list_resource_templates_tool`):

```python
    async def _handle_describe_model_tool(
        self,
        model: str,
        attributes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Handle describe_model tool request."""
        try:
            connection, access_controller, sub = await self._get_user_context()
            with perf_logger.track_operation("tool_describe_model", model=model):
                # Connector-level guard — Odoo's fields_get does not enforce
                # record-level access; it returns metadata even for models the
                # caller cannot read records from.
                access_controller.validate_model_access(model, "read")

                if not connection.is_authenticated:
                    raise ValidationError("Not authenticated with Odoo")

                fetch_attributes = (
                    attributes if attributes is not None else _DESCRIBE_MODEL_DEFAULT_ATTRIBUTES
                )
                raw_fields = connection.fields_get(model, fetch_attributes)

                fields_out: Dict[str, Any] = {}
                for field_name, field_data in raw_fields.items():
                    ftype = field_data.get("type", "")
                    fields_out[field_name] = {
                        "type": ftype,
                        "string": field_data.get("string", field_name),
                        "required": bool(field_data.get("required", False)),
                        "readonly": bool(field_data.get("readonly", False)),
                        "relation": field_data.get("relation") or None,
                        "help": field_data.get("help") or None,
                        "is_m2m": ftype == "many2many",
                    }

                return {
                    "model": model,
                    "fields": fields_out,
                    "total_fields": len(fields_out),
                }

        except ValidationError:
            raise
        except AccessControlError as e:
            raise ValidationError(f"Access denied: {e}") from e
        except OdooConnectionError as e:
            raise ValidationError(f"Connection error: {e}") from e
        except Exception as e:
            logger.error(f"Error in describe_model tool: {e}")
            sanitized_msg = ErrorSanitizer.sanitize_message(str(e))
            raise ValidationError(f"Failed to describe model: {sanitized_msg}") from e

```

- [ ] **Step 8: Add `"describe_model"` to `test_tools_registered` in `tests/test_tools.py`**

Find `test_tools_registered` (line 74) and add the fourth assertion:

```python
    def test_tools_registered(self, handler, mock_app):
        """Test that tools are registered with FastMCP."""
        # Check that all three tools are registered
        assert "search_records" in mock_app._tools
        assert "get_record" in mock_app._tools
        assert "list_models" in mock_app._tools
        assert "describe_model" in mock_app._tools
```

- [ ] **Step 9: Run tests to confirm all pass**

```bash
uv run pytest tests/test_describe_model.py tests/test_tools.py -v
```

Expected: 11 + existing tools tests all green.

- [ ] **Step 10: Commit**

```bash
git add mcp_server_odoo/schemas.py mcp_server_odoo/tools.py tests/test_describe_model.py tests/test_tools.py
git commit -m "feat: add describe_model tool returning field metadata"
```

---

## Task 3: M2M Heuristic Validation

**Goal:** Add `_validate_m2m_values()` to `OdooToolHandler` and call it from `_handle_create_record_tool` and `_handle_update_record_tool`, catching the three most common M2M write mistakes before they reach Odoo.

**Files:**
- Modify: `mcp_server_odoo/tools.py` — add `_validate_m2m_values` method + two call sites
- Modify: `tests/test_write_tools.py` — move 4 fixtures to module scope; add `TestValidateM2mValues`

**Acceptance Criteria:**
- [ ] Flat int list `[15, 3]` → `ValidationError` with `"[[4, id]"` in message
- [ ] Bare command tuple `[4, 15]` → `ValidationError` with `"Wrap in a list"` in message
- [ ] Dict syntax `{"add": [15]}` → `ValidationError` with `"[[4, id]]"` in message
- [ ] Valid `[[4, 15], [4, 3]]` passes without error
- [ ] Valid `[[6, 0, [15, 3]]]` passes without error
- [ ] `lead_properties` (list-of-dicts) does not trigger validation
- [ ] Error messages include the model name for `describe_model` hint
- [ ] Validation fires for both `_handle_create_record_tool` and `_handle_update_record_tool`

**Verify:** `uv run pytest tests/test_write_tools.py -v` → all green

---

**Steps:**

- [ ] **Step 1: Move 4 fixtures to module scope in `tests/test_write_tools.py`**

Remove the four fixture methods from inside `class TestWriteTools` (lines 16–51) and place them before the class definition, removing `self` from their signatures:

```python
@pytest.fixture
def mock_app():
    """Create mock FastMCP app."""
    app = Mock()
    app.tool = Mock(side_effect=lambda **kwargs: lambda func: func)
    return app


@pytest.fixture
def mock_connection():
    """Create mock OdooConnection."""
    conn = Mock()
    conn.is_authenticated = True
    conn._base_url = "http://localhost:8069"
    # Default fields_get returns common fields so essential field filtering works
    conn.fields_get.return_value = {
        "id": {"string": "ID", "type": "integer"},
        "name": {"string": "Name", "type": "char"},
        "display_name": {"string": "Display Name", "type": "char"},
    }
    return conn


@pytest.fixture
def mock_access_controller():
    """Create mock AccessController."""
    controller = Mock()
    controller.validate_model_access = Mock()
    return controller


@pytest.fixture
def mock_config():
    """Create mock OdooConfig."""
    config = Mock()
    config.default_limit = 10
    config.max_limit = 100
    config.url = "http://localhost:8069"
    return config


class TestWriteTools:
    # tool_handler fixture stays here (depends on above 4 fixtures)
    ...
```

- [ ] **Step 2: Append `TestValidateM2mValues` at end of `tests/test_write_tools.py`**

```python


class TestValidateM2mValues:
    """Tests for _validate_m2m_values pre-write heuristic."""

    @pytest.fixture
    def handler(self, mock_app, mock_connection, mock_access_controller, mock_config):
        return OdooToolHandler(
            mock_app, mock_connection, mock_access_controller, mock_config
        )

    def test_flat_int_list_rejected(self, handler):
        with pytest.raises(ValidationError) as exc:
            handler._validate_m2m_values("crm.lead", {"tag_ids": [15, 3]})
        msg = str(exc.value)
        assert "tag_ids" in msg
        assert "[[4, id]" in msg

    def test_single_int_list_rejected(self, handler):
        with pytest.raises(ValidationError) as exc:
            handler._validate_m2m_values("crm.lead", {"tag_ids": [15]})
        assert "tag_ids" in str(exc.value)

    def test_bare_command_tuple_rejected(self, handler):
        with pytest.raises(ValidationError) as exc:
            handler._validate_m2m_values("crm.lead", {"tag_ids": [4, 15]})
        assert "Wrap in a list" in str(exc.value)

    def test_dict_syntax_rejected(self, handler):
        with pytest.raises(ValidationError) as exc:
            handler._validate_m2m_values("crm.lead", {"tag_ids": {"add": [15], "remove": []}})
        msg = str(exc.value)
        assert "tag_ids" in msg
        assert "[[4, id]]" in msg

    def test_valid_m2m_command_list_passes(self, handler):
        handler._validate_m2m_values("crm.lead", {"tag_ids": [[4, 15], [4, 3]]})

    def test_valid_set_command_passes(self, handler):
        handler._validate_m2m_values("crm.lead", {"tag_ids": [[6, 0, [15, 3]]]})

    def test_lead_properties_not_flagged(self, handler):
        """lead_properties holds list-of-dicts — must not trigger M2M detection."""
        props = [{"name": "x", "type": "char", "value": "hello"}]
        handler._validate_m2m_values("crm.lead", {"lead_properties": props})

    def test_string_field_not_flagged(self, handler):
        handler._validate_m2m_values("crm.lead", {"name": "Acme Corp"})

    def test_error_message_includes_model_hint(self, handler):
        with pytest.raises(ValidationError) as exc:
            handler._validate_m2m_values("crm.lead", {"tag_ids": [99]})
        assert "crm.lead" in str(exc.value)

    def test_dict_syntax_error_includes_model_hint(self, handler):
        with pytest.raises(ValidationError) as exc:
            handler._validate_m2m_values("crm.lead", {"tag_ids": {"add": [15]}})
        assert "crm.lead" in str(exc.value)
```

- [ ] **Step 3: Run tests to confirm failures**

```bash
uv run pytest tests/test_write_tools.py::TestValidateM2mValues -v 2>&1 | tail -15
```

Expected: `AttributeError: 'OdooToolHandler' object has no attribute '_validate_m2m_values'`

- [ ] **Step 4: Add `_validate_m2m_values` method to `OdooToolHandler`**

Insert after `_track_usage` (line 139) and before `_format_datetime` (line 141):

```python
    def _validate_m2m_values(self, model: str, values: Dict[str, Any]) -> None:
        """Detect malformed Many2many field values before they reach Odoo.

        Catches the three most common M2M write mistakes and raises with
        actionable error messages pointing to the correct [[4,id]] syntax.

        Note: lead_properties is out of scope here — its full-array requirement
        is a different failure mode (Odoo property system, not relational field
        syntax). It is documented in the update_record docstring.
        """
        for field, value in values.items():
            # Case A: bare command tuple without outer list, e.g. [4, 15] not [[4, 15]].
            # Checked before Case B because [4, 15] is also "all ints" — the more
            # specific pattern gets the better error message.
            if (
                isinstance(value, list)
                and len(value) == 2
                and isinstance(value[0], int)
                and value[0] in (0, 1, 2, 3, 4, 5, 6)
                and isinstance(value[1], int)
            ):
                raise ValidationError(
                    f"Field '{field}': looks like a single M2M command tuple {value!r}. "
                    f"Wrap in a list: [{value!r}]. "
                    f"Use describe_model('{model}') to confirm '{field}' is many2many."
                )

            # Case B: flat integer list, e.g. [15, 3] or [15].
            if (
                isinstance(value, list)
                and len(value) > 0
                and all(isinstance(el, int) for el in value)
            ):
                raise ValidationError(
                    f"Field '{field}': got a flat integer list {value!r}. "
                    f"Many2many fields require Odoo command syntax: "
                    f"[[4, id], ...] to add, [[3, id], ...] to remove, "
                    f"[[6, 0, [ids]]] to replace all. "
                    f"Use describe_model('{model}') to confirm '{field}' is many2many."
                )

            # Case C: friendly dict syntax some agents invent.
            if isinstance(value, dict) and set(value.keys()) <= {"add", "remove", "set"}:
                raise ValidationError(
                    f"Field '{field}': dict syntax is not supported. "
                    f"Use Odoo command tuples: [[4, id]] to add, "
                    f"[[6, 0, [ids]]] to replace all. "
                    f"Use describe_model('{model}') to confirm '{field}' is many2many."
                )

```

- [ ] **Step 5: Call `_validate_m2m_values` in `_handle_create_record_tool`**

After line 1322 (`raise ValidationError("No values provided for record creation")`), add before `# Create the record`:

```python
                    raise ValidationError("No values provided for record creation")

                self._validate_m2m_values(model, values)

                # Create the record
                record_id = connection.create(model, values)
```

- [ ] **Step 6: Call `_validate_m2m_values` in `_handle_update_record_tool`**

After line 1394 (`raise ValidationError("No values provided for record update")`), add before `# Check if record exists`:

```python
                    raise ValidationError("No values provided for record update")

                self._validate_m2m_values(model, values)

                # Check if record exists (only fetch ID to verify existence)
                existing = connection.read(model, [record_id], ["id"])
```

- [ ] **Step 7: Run tests to confirm all pass**

```bash
uv run pytest tests/test_write_tools.py -v
```

Expected: all green, including the 10 new `TestValidateM2mValues` cases.

- [ ] **Step 8: Commit**

```bash
git add mcp_server_odoo/tools.py tests/test_write_tools.py
git commit -m "feat: add M2M heuristic validation to create_record and update_record"
```

---

## Task 4: Docstring Improvements

**Goal:** Replace `create_record` and `update_record` docstrings with explicit write-path patterns so agents have inline documentation for M2M, Many2one, HTML, and `lead_properties` fields.

**Files:**
- Modify: `mcp_server_odoo/tools.py` — replace docstrings at lines 687–695 and 714–723

**Acceptance Criteria:**
- [ ] `create_record` docstring contains M2M command syntax, Many2one, HTML patterns
- [ ] `update_record` docstring contains all of the above plus `lead_properties` full-array note
- [ ] Module imports cleanly

**Verify:** `uv run python -c "from mcp_server_odoo.tools import OdooToolHandler; print('OK')"` → `OK`

---

**Steps:**

- [ ] **Step 1: Replace `create_record` docstring (lines 687–695)**

Find:
```python
            """Create a new record in an Odoo model.

            Args:
                model: The Odoo model name (e.g., 'res.partner')
                values: Field values for the new record

            Returns:
                Created record details with ID, URL, and confirmation.
            """
```

Replace with:
```python
            """Create a new record in an Odoo model.

            Args:
                model: The Odoo model name (e.g., 'res.partner')
                values: Field values for the new record

            Returns:
                Created record details with ID, URL, and confirmation.

            Write-path patterns:

            Many2many fields (e.g. tag_ids): use Odoo command tuples, not flat
            ID lists. Passing [15, 3] raises an error — use describe_model() first.
              [[4, id]]         - link existing record
              [[3, id]]         - remove link
              [[6, 0, [ids]]]   - replace all links with this exact set

            Many2one fields (e.g. source_id, country_id): pass the integer ID,
            not the display name. Use search_records('utm.source',
            [['name', '=', 'Newsletter']]) to resolve names to IDs first.

            HTML fields (type "html", e.g. description in crm.lead): store
            HTML markup. Pass strings like "<p>text</p>" for readable output.
            Plain text is stored but renders without formatting. Use
            describe_model() to identify html fields.
            """
```

- [ ] **Step 2: Replace `update_record` docstring (lines 714–723)**

Find:
```python
            """Update an existing record.

            Args:
                model: The Odoo model name (e.g., 'res.partner')
                record_id: The record ID to update
                values: Field values to update

            Returns:
                Updated record details with confirmation.
            """
```

Replace with:
```python
            """Update an existing record.

            Args:
                model: The Odoo model name (e.g., 'res.partner')
                record_id: The record ID to update
                values: Field values to update

            Returns:
                Updated record details with confirmation.

            Write-path patterns:

            Many2many fields (e.g. tag_ids): use Odoo command tuples, not flat
            ID lists. Passing [15, 3] raises an error — use describe_model() first.
              [[4, id]]         - link existing record
              [[3, id]]         - remove link
              [[6, 0, [ids]]]   - replace all links with this exact set

            Many2one fields (e.g. source_id, country_id): pass the integer ID,
            not the display name. Use search_records('utm.source',
            [['name', '=', 'Newsletter']]) to resolve names to IDs first.

            HTML fields (type "html", e.g. description in crm.lead): store
            HTML markup. Pass strings like "<p>text</p>" for readable output.
            Plain text is stored but renders without formatting. Use
            describe_model() to identify html fields.

            lead_properties (custom property fields): always pass the COMPLETE
            array. Odoo replaces the entire field on write — partial updates
            reset omitted properties to their defaults. Read the current value
            first, merge your changes, then write the full array back.
            """
```

- [ ] **Step 3: Verify import**

```bash
uv run python -c "from mcp_server_odoo.tools import OdooToolHandler; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Run full suite**

```bash
uv run pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all tests green.

- [ ] **Step 5: Commit**

```bash
git add mcp_server_odoo/tools.py
git commit -m "docs: add write-path patterns to create_record and update_record docstrings"
```

---

## Final Verification

```bash
uv run pytest tests/ -v --tb=short
```

All existing tests pass plus:
- 1 updated assertion in `test_xmlrpc_fault_sanitization`
- 13 new cases in `TestSanitizeXmlrpcFaultRefactored`
- 11 new cases in `TestDescribeModelHandler`
- 1 new assertion in `test_tools_registered`
- 10 new cases in `TestValidateM2mValues`
