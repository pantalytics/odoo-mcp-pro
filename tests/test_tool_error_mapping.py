"""Tests for mapping connection-layer errors to user-facing tool errors.

A fault Odoo returns (bad field, missing method, denied permission) reaches the
tools as an OdooExecutionError. It must surface as its own message, not behind
the misleading "Connection error" label reserved for real transport failures.
"""

from mcp_server_odoo.error_handling import ValidationError
from mcp_server_odoo.exceptions import OdooConnectionError, OdooExecutionError
from mcp_server_odoo.tools._common import odoo_error_as_validation


def test_execution_error_keeps_odoo_message():
    exc = OdooExecutionError("Operation failed: Invalid field 'url' in request")
    result = odoo_error_as_validation(exc)

    assert isinstance(result, ValidationError)
    assert "Connection error" not in result.message
    assert "Invalid field 'url' in request" in result.message


def test_transport_error_keeps_connection_label():
    exc = OdooConnectionError("Connection failed: could not connect to host")
    result = odoo_error_as_validation(exc)

    assert isinstance(result, ValidationError)
    assert result.message.startswith("Connection error:")
