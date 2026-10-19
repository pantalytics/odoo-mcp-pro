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

    def test_odoo_text_is_kept(self):
        """Odoo's own message is the useful part and is never replaced."""
        for message in [
            "Invalid field res.partner.invalid_field in leaf ('invalid_field', '=', True)",
            "Field bogus_field does not exist",
            "Model sale.order does not exist",
            "Access denied: Operation read not allowed on model x",
            "Failed to execute action_post on account.move: boom",
            "Record res.partner(5,) does not exist",
            "Connection refused",
        ]:
            assert ErrorSanitizer.sanitize_message(message) == message

    def test_data_line_numbers_are_kept(self):
        """ "line 3" of an invoice is data, not a code location."""
        for message in [
            "The field Customer (partner_id) is required on line 3",
            "Invoice line 3 has no account",
        ]:
            assert ErrorSanitizer.sanitize_message(message) == message

    def test_short_messages_are_kept(self):
        """A KeyError's message is just the key, and it is the whole clue."""
        assert ErrorSanitizer.sanitize_message("'qty'") == "'qty'"

    def test_traceback_reduces_to_final_exception(self):
        """An XML-RPC fault carries Odoo's whole traceback; the end is the error."""
        fault = (
            "Traceback (most recent call last):\n"
            '  File "/opt/odoo/odoo/service/model.py", line 12, in execute_kw\n'
            "    return execute(db, uid)\n"
            '  File "/opt/odoo/addons/sale/models/sale_order.py", line 99, in action_confirm\n'
            "    x()\n"
            "TypeError: SaleOrder.action_confirm() takes 1 positional argument but 2 were given\n"
        )
        assert ErrorSanitizer.sanitize_message(fault) == (
            "TypeError: SaleOrder.action_confirm() takes 1 positional argument but 2 were given"
        )

    def test_exception_class_kept_module_path_dropped(self):
        fault = (
            "Traceback (most recent call last):\n"
            '  File "/opt/odoo/odoo/http.py", line 1589, in _serve_db\n'
            "odoo.exceptions.ValidationError: The operation cannot be completed:\n"
            "- Create/update: a mandatory field is not set."
        )
        assert ErrorSanitizer.sanitize_message(fault) == (
            "ValidationError: The operation cannot be completed: "
            "- Create/update: a mandatory field is not set."
        )

    def test_xmlrpc_fault_sanitization(self):
        """Test XML-RPC fault message sanitization."""
        fault = "ValidationError: Field 'vat' is required"
        sanitized = ErrorSanitizer.sanitize_xmlrpc_fault(fault)
        assert sanitized == "ValidationError: Field 'vat' is required"

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

        # Should contain useful information
        assert "Invalid field" in sanitized or "error" in sanitized.lower()
