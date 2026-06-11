"""Tests for error handling system.

Logging configuration and global-instance tests live in tests/test_error_logging.py.
"""

import pytest

from mcp_server_odoo.error_handling import (
    AuthenticationError,
    ConfigurationError,
    ConnectionError,
    ErrorCategory,
    ErrorContext,
    ErrorHandler,
    ErrorSeverity,
    MCPError,
    NotFoundError,
    PermissionError,
    RateLimitError,
    SystemError,
    ValidationError,
    format_user_error,
    handle_odoo_error,
)


class TestMCPError:
    """Test the MCPError base class."""

    def test_error_creation(self):
        """Test creating an MCPError with all parameters."""
        context = ErrorContext(
            model="res.partner",
            operation="search",
            record_id=42,
            user_id=1,
            request_id="test-123",
        )

        error = MCPError(
            message="Test error",
            category=ErrorCategory.VALIDATION,
            severity=ErrorSeverity.MEDIUM,
            code="TEST_ERROR",
            details={"field": "email", "value": "invalid"},
            context=context,
        )

        assert error.message == "Test error"
        assert error.category == ErrorCategory.VALIDATION
        assert error.severity == ErrorSeverity.MEDIUM
        assert error.code == "TEST_ERROR"
        assert error.details == {"field": "email", "value": "invalid"}
        assert error.context.model == "res.partner"
        assert error.context.operation == "search"

    def test_error_code_generation(self):
        """Test automatic error code generation."""
        error = AuthenticationError("Invalid credentials")
        assert error.code == "AUTH_ERROR"

        error = PermissionError("Access denied")
        assert error.code == "PERMISSION_DENIED"

        error = NotFoundError("Record not found")
        assert error.code == "NOT_FOUND"

        error = ValidationError("Invalid input")
        assert error.code == "VALIDATION_ERROR"

        error = ConnectionError("Connection failed")
        assert error.code == "CONNECTION_ERROR"

        error = SystemError("System failure")
        assert error.code == "SYSTEM_ERROR"

        error = ConfigurationError("Bad config")
        assert error.code == "CONFIG_ERROR"

        error = RateLimitError("Too many requests")
        assert error.code == "RATE_LIMIT_EXCEEDED"

    def test_error_to_dict(self):
        """Test converting error to dictionary."""
        context = ErrorContext(model="res.partner", operation="create")
        error = ValidationError(
            "Invalid email format",
            details={"field": "email"},
            context=context,
        )

        error_dict = error.to_dict()

        assert "error" in error_dict
        assert error_dict["error"]["code"] == "VALIDATION_ERROR"
        assert error_dict["error"]["message"] == "Invalid email format"
        assert error_dict["error"]["category"] == "VALIDATION"
        assert error_dict["error"]["severity"] == "low"
        # Details are sanitized to only include safe fields
        assert error_dict["error"]["details"] == {"field": "email"}
        assert error_dict["error"]["context"]["model"] == "res.partner"
        assert error_dict["error"]["context"]["operation"] == "create"
        assert "timestamp" in error_dict["error"]

    def test_error_to_mcp_error(self):
        """Test converting to MCP-compliant error format."""
        error = ValidationError(
            "Invalid input",
            details={"field": "name", "issue": "too_short"},
        )

        mcp_error = error.to_mcp_error()

        assert mcp_error.code == -32000  # Application error code
        assert mcp_error.message == "Invalid input"
        assert mcp_error.data["code"] == "VALIDATION_ERROR"
        # Details are now sanitized - only safe fields are included
        assert mcp_error.data["details"] == {"field": "name"}


class TestErrorHandler:
    """Test the ErrorHandler class."""

    def test_error_handler_initialization(self):
        """Test error handler initialization."""
        handler = ErrorHandler()

        assert handler.metrics.total_errors == 0
        assert len(handler.metrics.errors_by_category) == 0
        assert len(handler.metrics.errors_by_severity) == 0
        assert handler.metrics.last_error_time is None
        assert handler._max_history_size == 1000

    def test_handle_mcp_error(self):
        """Test handling an MCPError."""
        handler = ErrorHandler()
        handler.clear_metrics()

        error = ValidationError("Test validation error")

        with pytest.raises(ValidationError):
            handler.handle_error(error)

        # Check metrics
        assert handler.metrics.total_errors == 1
        assert handler.metrics.errors_by_category[ErrorCategory.VALIDATION] == 1
        assert handler.metrics.errors_by_severity[ErrorSeverity.LOW] == 1
        assert handler.metrics.last_error_time is not None

        # Check history
        recent = handler.get_recent_errors(limit=1)
        assert len(recent) == 1
        assert recent[0]["error"]["message"] == "Test validation error"

    def test_handle_standard_exception(self):
        """Test converting standard exceptions to MCPError."""
        handler = ErrorHandler()
        handler.clear_metrics()

        # Test ValueError conversion
        with pytest.raises(ValidationError) as exc_info:
            handler.handle_error(ValueError("Invalid value"))
        assert "Invalid input: Invalid value" in str(exc_info.value)

        # Test ConnectionRefusedError conversion
        with pytest.raises(ConnectionError) as exc_info:
            handler.handle_error(ConnectionRefusedError("Connection refused"))
        assert "Connection failed:" in str(exc_info.value)

        # Test KeyError conversion
        with pytest.raises(NotFoundError) as exc_info:
            handler.handle_error(KeyError("missing_key"))
        assert "Resource not found:" in str(exc_info.value)

        # Test generic exception conversion
        with pytest.raises(SystemError) as exc_info:
            handler.handle_error(RuntimeError("Something went wrong"))
        assert "Unexpected error:" in str(exc_info.value)

    def test_handle_error_no_reraise(self):
        """Test handling error without re-raising."""
        handler = ErrorHandler()
        error = ValidationError("Test error")

        result = handler.handle_error(error, reraise=False)

        assert isinstance(result, MCPError)
        assert result.message == "Test error"

    def test_error_context_manager(self):
        """Test error context manager."""
        handler = ErrorHandler()
        handler.clear_metrics()

        with pytest.raises(ValidationError) as exc_info:
            with handler.error_context(model="res.partner", operation="create"):
                raise ValueError("Invalid field")

        error = exc_info.value
        assert error.context.model == "res.partner"
        assert error.context.operation == "create"

    def test_get_metrics(self):
        """Test getting error metrics."""
        handler = ErrorHandler()
        handler.clear_metrics()

        # Generate some errors
        handler.handle_error(ValidationError("Error 1"), reraise=False)
        handler.handle_error(PermissionError("Error 2"), reraise=False)
        handler.handle_error(ValidationError("Error 3"), reraise=False)

        metrics = handler.get_metrics()

        assert metrics["total_errors"] == 3
        assert metrics["errors_by_category"]["VALIDATION"] == 2
        assert metrics["errors_by_category"]["PERMISSION"] == 1
        assert metrics["errors_by_severity"]["low"] == 2
        assert metrics["errors_by_severity"]["medium"] == 1
        assert metrics["last_error_time"] is not None
        assert "error_rate_per_minute" in metrics
        assert "uptime_seconds" in metrics

    def test_error_history_limit(self):
        """Test that error history respects size limit."""
        handler = ErrorHandler()
        handler._max_history_size = 5
        handler.clear_metrics()

        # Add more errors than the limit
        for i in range(10):
            handler.handle_error(
                ValidationError(f"Error {i}"),
                reraise=False,
            )

        # Check that only the last 5 are kept
        recent = handler.get_recent_errors(limit=10)
        assert len(recent) == 5
        # Messages are sanitized, but we can verify the history is properly limited


class TestOdooErrorHandling:
    """Test Odoo-specific error handling."""

    def test_handle_odoo_access_denied(self):
        """Test handling Odoo access denied errors."""
        error = Exception("Access Denied for model res.partner")
        result = handle_odoo_error(error, model="res.partner", operation="read")

        assert isinstance(result, PermissionError)
        assert "Access denied for read on res.partner" in result.message
        assert result.context.model == "res.partner"
        assert result.context.operation == "read"

    def test_handle_odoo_not_found(self):
        """Test handling Odoo not found errors."""
        error = Exception("Record does not exist")
        result = handle_odoo_error(error, model="res.partner")

        assert isinstance(result, NotFoundError)
        assert "Resource not found: res.partner" in result.message

    def test_handle_odoo_validation(self):
        """Test handling Odoo validation errors."""
        error = Exception("Invalid field value")
        result = handle_odoo_error(error, operation="create")

        assert isinstance(result, ValidationError)
        assert "Validation failed for create" in result.message

    def test_handle_odoo_connection(self):
        """Test handling Odoo connection errors."""
        error = Exception("Connection timeout")
        result = handle_odoo_error(error)

        assert isinstance(result, ConnectionError)
        assert "Connection to Odoo failed" in result.message

    def test_handle_odoo_generic(self):
        """Test handling generic Odoo errors."""
        error = Exception("Some other error")
        result = handle_odoo_error(error, operation="search")

        assert isinstance(result, SystemError)
        assert "Odoo error during search" in result.message


class TestUserErrorFormatting:
    """Test user-friendly error formatting."""

    def test_format_validation_error(self):
        """Test formatting validation errors."""
        error = ValidationError(
            "Email format is invalid",
            context=ErrorContext(model="res.partner"),
        )

        formatted = format_user_error(error)

        assert "Email format is invalid (Model: res.partner)" in formatted
        assert "Please check your input and try again" in formatted

    def test_format_permission_error(self):
        """Test formatting permission errors."""
        error = PermissionError("Cannot create records")

        formatted = format_user_error(error)

        assert "Cannot create records" in formatted
        assert "You don't have permission" in formatted
        assert "Contact your administrator" in formatted

    def test_format_not_found_error(self):
        """Test formatting not found errors."""
        error = NotFoundError("Partner not found")

        formatted = format_user_error(error)

        assert "Partner not found" in formatted
        assert "doesn't exist or has been deleted" in formatted

    def test_format_connection_error(self):
        """Test formatting connection errors."""
        error = ConnectionError("Cannot connect to server")

        formatted = format_user_error(error)

        assert "Cannot connect to server" in formatted
        assert "Unable to connect to Odoo" in formatted
        assert "check your connection settings" in formatted
