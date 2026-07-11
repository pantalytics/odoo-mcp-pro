# SPDX-License-Identifier: MPL-2.0
# SPDX-FileCopyrightText: 2025 Andrey Ivanov <ivnv.xd@gmail.com>
# SPDX-FileCopyrightText: 2025-2026 Pantalytics B.V.
#
# Derived from mcp-server-odoo (https://github.com/ivnvxd/mcp-server-odoo).
# This file stays under the Mozilla Public License 2.0; see LICENSE.MPL-2.0.
"""Error handling and monitoring for Odoo MCP Server.

This module provides a centralized error handling system with:
- Error categorization and classification
- User-friendly error message generation
- Structured logging and monitoring
- MCP-compliant error response formatting
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Dict, Optional, Union

from mcp.types import ErrorData

from .error_sanitizer import ErrorSanitizer


class ErrorCategory(Enum):
    """Categories of errors that can occur in the MCP server."""

    AUTHENTICATION = auto()  # Authentication failures
    PERMISSION = auto()  # Permission/access denied
    NOT_FOUND = auto()  # Resource/model/record not found
    VALIDATION = auto()  # Input validation errors
    CONNECTION = auto()  # Connection/network errors
    SYSTEM = auto()  # System/unexpected errors
    CONFIGURATION = auto()  # Configuration errors
    RATE_LIMIT = auto()  # Rate limiting errors


class ErrorSeverity(Enum):
    """Severity levels for errors."""

    LOW = "low"  # Informational, non-critical
    MEDIUM = "medium"  # User error, recoverable
    HIGH = "high"  # System error, may need intervention
    CRITICAL = "critical"  # Critical failure, immediate attention


@dataclass
class ErrorContext:
    """Context information for an error."""

    model: Optional[str] = None
    operation: Optional[str] = None
    record_id: Optional[Union[int, str]] = None
    user_id: Optional[int] = None
    request_id: Optional[str] = None
    additional_info: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ErrorMetrics:
    """Metrics for error tracking and monitoring."""

    total_errors: int = 0
    errors_by_category: Dict[ErrorCategory, int] = field(default_factory=dict)
    errors_by_severity: Dict[ErrorSeverity, int] = field(default_factory=dict)
    last_error_time: Optional[datetime] = None
    error_rate_per_minute: float = 0.0

    def record_error(self, category: ErrorCategory, severity: ErrorSeverity):
        """Record an error occurrence."""
        self.total_errors += 1
        self.errors_by_category[category] = self.errors_by_category.get(category, 0) + 1
        self.errors_by_severity[severity] = self.errors_by_severity.get(severity, 0) + 1
        self.last_error_time = datetime.now()


class MCPError(Exception):
    """Base exception for MCP-related errors with enhanced tracking."""

    def __init__(
        self,
        message: str,
        category: ErrorCategory,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        context: Optional[ErrorContext] = None,
    ):
        """Initialize MCP error with tracking information.

        Args:
            message: Human-readable error message
            category: Error category for classification
            severity: Error severity level
            code: Optional error code for specific error types
            details: Additional error details
            context: Error context information
        """
        super().__init__(message)
        self.message = message
        self.category = category
        self.severity = severity
        self.code = code or self._generate_code(category)
        self.details = details or {}
        self.context = context or ErrorContext()
        self.timestamp = datetime.now()

    def _generate_code(self, category: ErrorCategory) -> str:
        """Generate error code based on category."""
        codes = {
            ErrorCategory.AUTHENTICATION: "AUTH_ERROR",
            ErrorCategory.PERMISSION: "PERMISSION_DENIED",
            ErrorCategory.NOT_FOUND: "NOT_FOUND",
            ErrorCategory.VALIDATION: "VALIDATION_ERROR",
            ErrorCategory.CONNECTION: "CONNECTION_ERROR",
            ErrorCategory.SYSTEM: "SYSTEM_ERROR",
            ErrorCategory.CONFIGURATION: "CONFIG_ERROR",
            ErrorCategory.RATE_LIMIT: "RATE_LIMIT_EXCEEDED",
        }
        return codes.get(category, "UNKNOWN_ERROR")

    def to_dict(self) -> Dict[str, Any]:
        """Convert error to dictionary for logging/API responses."""
        # Sanitize message and details for external consumption
        sanitized_message = ErrorSanitizer.sanitize_message(self.message)
        sanitized_details = ErrorSanitizer.sanitize_error_details(self.details)

        return {
            "error": {
                "code": self.code,
                "message": sanitized_message,
                "category": self.category.name,
                "severity": self.severity.value,
                "details": sanitized_details,
                "context": {
                    "model": self.context.model,
                    "operation": self.context.operation,
                    "record_id": self.context.record_id,
                    "request_id": self.context.request_id,
                },
                "timestamp": self.timestamp.isoformat(),
            }
        }

    def to_mcp_error(self) -> ErrorData:
        """Convert to MCP-compliant error format."""
        # Sanitize message and details for external consumption
        sanitized_message = ErrorSanitizer.sanitize_message(self.message)
        sanitized_details = ErrorSanitizer.sanitize_error_details(self.details)

        return ErrorData(
            code=-32000,  # Application error
            message=sanitized_message,
            data={"code": self.code, "details": sanitized_details},
        )


# Specific error classes for each category
class AuthenticationError(MCPError):
    """Authentication-related errors."""

    def __init__(self, message: str, **kwargs):
        super().__init__(
            message=message,
            category=ErrorCategory.AUTHENTICATION,
            severity=ErrorSeverity.HIGH,
            **kwargs,
        )


class PermissionError(MCPError):
    """Permission/access denied errors."""

    def __init__(self, message: str, **kwargs):
        super().__init__(
            message=message,
            category=ErrorCategory.PERMISSION,
            severity=ErrorSeverity.MEDIUM,
            **kwargs,
        )


class NotFoundError(MCPError):
    """Resource not found errors."""

    def __init__(self, message: str, **kwargs):
        super().__init__(
            message=message,
            category=ErrorCategory.NOT_FOUND,
            severity=ErrorSeverity.LOW,
            **kwargs,
        )


class ValidationError(MCPError):
    """Input validation errors."""

    def __init__(self, message: str, **kwargs):
        super().__init__(
            message=message,
            category=ErrorCategory.VALIDATION,
            severity=ErrorSeverity.LOW,
            **kwargs,
        )


class ConnectionError(MCPError):
    """Connection/network errors."""

    def __init__(self, message: str, **kwargs):
        super().__init__(
            message=message,
            category=ErrorCategory.CONNECTION,
            severity=ErrorSeverity.HIGH,
            **kwargs,
        )


class SystemError(MCPError):
    """System/unexpected errors."""

    def __init__(self, message: str, **kwargs):
        super().__init__(
            message=message,
            category=ErrorCategory.SYSTEM,
            severity=ErrorSeverity.CRITICAL,
            **kwargs,
        )


class ConfigurationError(MCPError):
    """Configuration errors."""

    def __init__(self, message: str, **kwargs):
        super().__init__(
            message=message,
            category=ErrorCategory.CONFIGURATION,
            severity=ErrorSeverity.HIGH,
            **kwargs,
        )


class RateLimitError(MCPError):
    """Rate limiting errors."""

    def __init__(self, message: str, **kwargs):
        super().__init__(
            message=message,
            category=ErrorCategory.RATE_LIMIT,
            severity=ErrorSeverity.MEDIUM,
            **kwargs,
        )


# Compatibility re-exports: `ErrorHandler`, the global `error_handler`
# instance, `handle_odoo_error`, and `format_user_error` live in
# `error_handler.py` but remain importable from this module.
#
# Lazy module-level __getattr__ (PEP 562) instead of an eager bottom-of-module
# import: `error_handler.py` imports the exception types defined above, so an
# eager `from .error_handler import ...` here would break when
# `mcp_server_odoo.error_handler` is imported before this module (circular
# import on a partially initialized module). The lazy lookup works in both
# import orders.
_ERROR_HANDLER_EXPORTS = frozenset(
    {"ErrorHandler", "error_handler", "handle_odoo_error", "format_user_error"}
)


def __getattr__(name: str) -> Any:
    if name in _ERROR_HANDLER_EXPORTS:
        from . import error_handler as _error_handler_module

        return getattr(_error_handler_module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
