# SPDX-License-Identifier: MPL-2.0
# SPDX-FileCopyrightText: 2025 Andrey Ivanov <ivnv.xd@gmail.com>
# SPDX-FileCopyrightText: 2025-2026 Pantalytics B.V.
#
# Derived from mcp-server-odoo (https://github.com/ivnvxd/mcp-server-odoo).
# This file stays under the Mozilla Public License 2.0; see LICENSE.MPL-2.0.
"""Error message sanitizer for Odoo MCP Server.

This module provides utilities to sanitize error messages before they are
returned to users, removing internal implementation details while maintaining
useful information for debugging.

"""

import re
from typing import Any, Dict


class ErrorSanitizer:
    """Sanitizes error messages to remove internal implementation details.

    The rule: strip what describes our process or Odoo's (file paths, line
    numbers of code, tracebacks, memory addresses, module paths) and keep what
    Odoo said about the data. Odoo's message is how the caller learns which
    field is missing or which rule refused, so it is never replaced by a
    generic sentence.
    """

    # The final exception line of a traceback: ``odoo.exceptions.UserError: ...``
    _EXCEPTION_LINE = re.compile(
        r"^[ \t]*(?:[A-Za-z_]\w*\.)*\w*(?:Error|Exception|Warning|Denied|Fault)[ \t]*:.*$",
        re.MULTILINE,
    )
    _TRACEBACK_MARKERS = re.compile(
        r'Traceback \(most recent call last\)|^[ \t]*File "[^"]*", line \d+', re.MULTILINE
    )

    # Patterns to detect and remove
    PATTERNS_TO_REMOVE = [
        # Traceback frames and file paths (code line numbers go with them;
        # "line 3" of an invoice is Odoo talking about data and stays)
        (r'(?:File|file)\s*"[^"]*"(?:,\s*line\s+\d+)?(?:,\s*in\s+\S+)?', ""),
        (r"(/[^/\s]+)+/[^/\s]+\.py", ""),
        (r"Traceback \(most recent call last\):", ""),
        # Module paths: keep the exception class, drop where it lives
        (r"\b(?:[a-z_][a-z0-9_]*\.)+([A-Z]\w*(?:Error|Exception|Warning|Denied|Fault))\b", r"\1"),
        (r"mcp_server_odoo\.[a-zA-Z_\.]+:\s*", ""),
        # Class names
        (r"<class \'[^\']+\'>", ""),
        (r"MCPObjectController:", ""),
        (r"OdooConnectionError:", ""),
        # Memory addresses and object references
        (r"\s+at\s+0x[0-9a-fA-F]+", ""),
        (r"Object at\s+0x[0-9a-fA-F]+", "Object"),
        (r"\bin\s+<[^>]+>", ""),
    ]

    @classmethod
    def _final_exception(cls, message: str) -> str:
        """Reduce a traceback to the exception it ended in.

        Everything above the last exception line is our stack or Odoo's; the
        line itself (and any continuation of a multi-line message) is what
        went wrong.
        """
        if not cls._TRACEBACK_MARKERS.search(message):
            return message
        matches = list(cls._EXCEPTION_LINE.finditer(message))
        if not matches:
            return message
        return message[matches[-1].start() :]

    @classmethod
    def sanitize_message(cls, message: str) -> str:
        """Sanitize an error message by removing internal details.

        Args:
            message: The original error message

        Returns:
            Sanitized error message safe for user consumption
        """
        if not message:
            return "An error occurred"

        sanitized = cls._final_exception(message)

        # Remove patterns that expose internals
        for pattern, replacement in cls.PATTERNS_TO_REMOVE:
            sanitized = re.sub(pattern, replacement, sanitized, flags=re.MULTILINE)

        # Clean up multiple spaces and newlines, and the tail of a Fault repr
        sanitized = re.sub(r"\s+", " ", sanitized).strip()
        sanitized = re.sub(r"'>$", "", sanitized).strip()

        if not sanitized:
            return "An error occurred while processing your request"

        # Ensure the message starts with a capital letter
        if sanitized[0].islower():
            sanitized = sanitized[0].upper() + sanitized[1:]

        return sanitized

    @classmethod
    def sanitize_error_details(cls, details: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitize error details dictionary.

        Args:
            details: Original error details

        Returns:
            Sanitized error details
        """
        if not details:
            return {}

        sanitized = {}

        # Only include safe fields
        safe_fields = {"model", "operation", "record_id", "field", "domain"}

        for key, value in details.items():
            if key in safe_fields:
                sanitized[key] = value
            elif key == "error_type":
                # Map internal error types to user-friendly categories
                sanitized["category"] = cls._map_error_type(value)

        # Remove any traceback information
        sanitized.pop("traceback", None)

        return sanitized

    @classmethod
    def _map_error_type(cls, error_type: str) -> str:
        """Map internal error type to user-friendly category.

        Args:
            error_type: Internal Python error type name

        Returns:
            User-friendly error category
        """
        mappings = {
            "ValidationError": "validation_error",
            "ValueError": "invalid_input",
            "TypeError": "invalid_type",
            "KeyError": "not_found",
            "NotFoundError": "not_found",
            "PermissionError": "permission_denied",
            "AccessControlError": "access_denied",
            "AuthenticationError": "authentication_failed",
            "ConnectionError": "connection_error",
            "OdooConnectionError": "connection_error",
            "TimeoutError": "timeout",
            "SystemError": "internal_error",
        }

        return mappings.get(error_type, "error")

    @classmethod
    def sanitize_xmlrpc_fault(cls, fault_string: str) -> str:
        """Sanitize XML-RPC fault messages from Odoo.

        Args:
            fault_string: Raw fault string from XML-RPC

        Returns:
            Sanitized error message: Odoo's own text, without the traceback
        """
        # The repr form some versions put in the fault: UserError('...')
        user_msg_match = re.search(r'UserError\(["\']([^"\']+)["\']', fault_string or "")
        if user_msg_match:
            return user_msg_match.group(1)
        return cls.sanitize_message(fault_string)
