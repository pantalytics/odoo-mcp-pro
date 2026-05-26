"""Tests for error message sanitization."""

from mcp_server_odoo.error_sanitizer import ErrorSanitizer


class TestErrorSanitizer:
    """Test error message sanitization functionality."""

    def test_sanitize_file_paths(self):
        """Test that file paths are removed."""
        message = 'File "/home/user/odoo/models.py", line 123, in execute'
        sanitized = ErrorSanitizer.sanitize_message(message)
        assert "/home/user" not in sanitized
        assert "line 123" not in sanitized
        assert ".py" not in sanitized

    def test_sanitize_module_paths(self):
        """Test that module paths are removed."""
        message = "mcp_server_odoo.odoo_connection: Connection failed"
        sanitized = ErrorSanitizer.sanitize_message(message)
        assert "mcp_server_odoo." not in sanitized
        assert "Connection failed" in sanitized

    def test_sanitize_class_names(self):
        """Test that class names are removed."""
        message = "Error: <class 'xmlrpc.client.Fault'> occurred"
        sanitized = ErrorSanitizer.sanitize_message(message)
        assert "<class" not in sanitized
        assert "xmlrpc.client" not in sanitized

    def test_sanitize_memory_addresses(self):
        """Test that memory addresses are removed."""
        message = "Object at 0x7f8b8c0d5f40 not found"
        sanitized = ErrorSanitizer.sanitize_message(message)
        assert "0x7f8b8c0d5f40" not in sanitized
        assert "Object at" not in sanitized

    def test_sanitize_traceback(self):
        """Test that traceback information is removed."""
        message = """Traceback (most recent call last):
          File "test.py", line 10, in <module>
            raise ValueError("Test error")
        ValueError: Test error"""
        sanitized = ErrorSanitizer.sanitize_message(message)
        assert "Traceback" not in sanitized
        assert 'File "test.py"' not in sanitized
        assert "Test error" in sanitized

    def test_field_error_mapping(self):
        """Test specific field error mappings."""
        message = "Invalid field res.partner.invalid_field in leaf ('invalid_field', '=', True)"
        sanitized = ErrorSanitizer.sanitize_message(message)
        # The sanitizer extracts just the field name, not the full model.field path
        assert sanitized == "Invalid field 'invalid_field' in search criteria"

        message = "Field bogus_field does not exist"
        sanitized = ErrorSanitizer.sanitize_message(message)
        assert sanitized == "Field 'bogus_field' does not exist on this model"

    def test_model_error_mapping(self):
        """Test model error mappings."""
        message = "Model sale.order does not exist"
        sanitized = ErrorSanitizer.sanitize_message(message)
        assert sanitized == "Model 'sale.order' is not available"

    def test_connection_error_mapping(self):
        """Test connection error mappings."""
        message = "Connection refused"
        sanitized = ErrorSanitizer.sanitize_message(message)
        assert sanitized == "Cannot connect to Odoo server"

        message = "Operation timeout after 30 seconds"
        sanitized = ErrorSanitizer.sanitize_message(message)
        assert sanitized == "Request timed out"

    def test_xmlrpc_fault_sanitization(self):
        """Test XML-RPC fault message sanitization."""
        fault = "Access Denied: Invalid API key or insufficient permissions"
        sanitized = ErrorSanitizer.sanitize_xmlrpc_fault(fault)
        assert sanitized == "Access denied: Invalid credentials or insufficient permissions"

        fault = "ValidationError: Field 'vat' is required"
        sanitized = ErrorSanitizer.sanitize_xmlrpc_fault(fault)
        assert sanitized == "Field 'vat' is required"

        fault = "UserError('Cannot delete record that has dependencies')"
        sanitized = ErrorSanitizer.sanitize_xmlrpc_fault(fault)
        assert sanitized == "Cannot delete record that has dependencies"

    def test_sanitize_error_details(self):
        """Test error details sanitization."""
        details = {
            "error_type": "ValidationError",
            "traceback": "Full traceback here...",
            "model": "res.partner",
            "operation": "create",
            "internal_path": "/opt/odoo/addons",
        }

        sanitized = ErrorSanitizer.sanitize_error_details(details)

        assert "traceback" not in sanitized
        assert "internal_path" not in sanitized
        assert sanitized["model"] == "res.partner"
        assert sanitized["operation"] == "create"
        assert sanitized["category"] == "validation_error"

    def test_error_type_mapping(self):
        """Test internal error type mapping."""
        assert ErrorSanitizer._map_error_type("ValidationError") == "validation_error"
        assert ErrorSanitizer._map_error_type("OdooConnectionError") == "connection_error"
        assert ErrorSanitizer._map_error_type("NotFoundError") == "not_found"
        assert ErrorSanitizer._map_error_type("UnknownError") == "error"

    def test_empty_message_handling(self):
        """Test handling of empty messages."""
        assert ErrorSanitizer.sanitize_message("") == "An error occurred"
        assert ErrorSanitizer.sanitize_message(None) == "An error occurred"

    def test_preserve_useful_information(self):
        """Test that useful information is preserved."""
        message = "Cannot find partner with email test@example.com"
        sanitized = ErrorSanitizer.sanitize_message(message)
        assert "test@example.com" in sanitized

        message = "Invalid value 'abc' for integer field"
        sanitized = ErrorSanitizer.sanitize_message(message)
        assert "'abc'" in sanitized
        assert "integer" in sanitized

    def test_capitalization(self):
        """Test that messages are properly capitalized."""
        message = "connection failed"
        sanitized = ErrorSanitizer.sanitize_message(message)
        assert sanitized[0].isupper()

    def test_internal_details_removal(self):
        """Test removal of internal implementation details."""
        message = "MCPObjectController: Invalid field res.partner.test_field"
        sanitized = ErrorSanitizer.sanitize_message(message)
        assert "MCPObjectController:" not in sanitized
        assert "Invalid field" in sanitized

    def test_complex_error_message(self):
        """Test sanitization of complex real-world error."""
        message = """Error executing tool search_records: Connection error: Failed to execute search_count on res.partner: Internal Server Error in MCPObjectController: Invalid field res.partner.invalid_field in leaf ('invalid_field', '=', True)
        File "/opt/odoo/addons/mcp_server/controllers/xmlrpc.py", line 123"""

        sanitized = ErrorSanitizer.sanitize_message(message)

        # Should not contain internal details
        assert "MCPObjectController" not in sanitized
        assert "/opt/odoo" not in sanitized
        assert "line 123" not in sanitized
        assert "search_count" not in sanitized

        # Should contain useful information
        assert "Invalid field" in sanitized or "error" in sanitized.lower()


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

    def test_validation_error_whitespace_only_body(self):
        fault = "ValidationError:   "
        result = ErrorSanitizer.sanitize_xmlrpc_fault(fault)
        assert result == "An error occurred while processing your request"

    def test_exc_repr_two_arg_with_apostrophe_in_second_arg(self):
        fault = "ValidationError('Required field', \"It's mandatory\")"
        result = ErrorSanitizer.sanitize_xmlrpc_fault(fault)
        assert result == "Required field"
