"""Error message sanitizer for Odoo MCP Server.

This module provides utilities to sanitize error messages before they are
returned to users, removing internal implementation details while maintaining
useful information for debugging.

odoo-mcp-pro (c) Pantalytics B.V. -- ref:pnl-4d8b
"""

import re
from typing import Any, Dict, Optional


class ErrorSanitizer:
    """Sanitizes error messages to remove internal implementation details."""

    # Matches "XxxError: msg" or "odoo.exceptions.XxxError: msg" bare prefixes.
    # AccessError is intentionally excluded — access errors are always normalised
    # in the early-exit block to avoid leaking model names, user IDs, etc.
    _ODOO_EXC_PREFIX_RE = re.compile(
        r"^(?:odoo\.exceptions\.)?(?:ValidationError|UserError|"
        r"MissingError|RedirectWarning|Warning):\s*(.*)",  # AccessError removed; .* allows empty body
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
    # Uses quote-specific branches ([^']+ vs [^"]+) to prevent backtracking
    # corruption when the second arg contains an apostrophe or double-quote.
    # Group 1 = single-quoted message, group 2 = double-quoted message.
    _EXC_REPR_RE = re.compile(
        r"^(?:odoo\.exceptions\.)?(?:ValidationError|UserError|"
        r"MissingError|RedirectWarning|Warning)"
        r"\((?:'([^']+)'|\"([^\"]+)\")"
        r"(?:,\s*(?:\"[^\"]*\"|'[^']*'))?\)\s*$",
        re.DOTALL,
    )

    # Patterns to detect and remove
    PATTERNS_TO_REMOVE = [
        # File paths
        (r'(File|file)\s*"[^"]+\.py"', "file"),
        (r"(/[^/\s]+)+/[^/\s]+\.py", ""),
        # Line numbers
        (r",?\s*line\s+\d+", ""),
        # Python internals
        (r"Traceback \(most recent call last\):", ""),
        (r'^\s*File "[^"]+", line \d+.*$', ""),
        # Module paths
        (r"mcp_server_odoo\.[a-zA-Z_\.]+:", ""),
        (r"odoo\.[a-zA-Z_\.]+:", ""),
        # Class names
        (r"<class \'[^\']+\'>", ""),
        (r"MCPObjectController:", ""),
        (r"OdooConnectionError:", ""),
        # Memory addresses and object references
        (r"\s+at\s+0x[0-9a-fA-F]+", ""),
        (r"Object at\s+0x[0-9a-fA-F]+", "Object"),
        # Stack traces
        (r"in\s+<[^>]+>", ""),
        (r"in\s+[a-zA-Z_]+\(\)", ""),
    ]

    # Specific error message mappings
    ERROR_MAPPINGS = {
        # Field errors
        r"Invalid field .+ in leaf": "Invalid field '{}' in search criteria",
        r"Field\s+(\w+)\s+does not exist": "Field '{}' does not exist on this model",
        r"Unknown field .+ in domain": "Unknown field '{}' in search criteria",
        # Model errors
        r"Model .+ does not exist": "Model '{}' is not available",
        r"Access denied on model": "You don't have permission to access this model",
        # Connection errors
        r"Failed to execute .+ on .+: .+": "Operation failed: {}",
        r"Connection refused": "Cannot connect to Odoo server",
        r"Operation timeout after \d+ seconds": "Request timed out",
        # Authentication errors
        r"Invalid API key": "Authentication failed: Invalid API key",
        r"Access denied": "Permission denied for this operation",
        # Record errors
        r"Record not found": "The requested record does not exist",
        r"Record .+ does not exist": "Record ID {} not found",
        # Domain errors
        r"Invalid domain": "Invalid search criteria format",
        r"Malformed domain": "Search criteria is not properly formatted",
    }

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

        sanitized = message

        # First, try to match against known error patterns
        for pattern, replacement in cls.ERROR_MAPPINGS.items():
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                # Extract any captured groups (like field names)
                if match.groups():
                    return replacement.format(*match.groups())
                elif "{}" in replacement:
                    # Try to extract relevant info from the message
                    extracted = cls._extract_relevant_info(message, pattern)
                    if extracted:
                        return replacement.format(extracted)
                return replacement

        # Remove patterns that expose internals
        for pattern, replacement in cls.PATTERNS_TO_REMOVE:
            sanitized = re.sub(pattern, replacement, sanitized, flags=re.MULTILINE)

        # Clean up multiple spaces and newlines
        sanitized = re.sub(r"\s+", " ", sanitized).strip()

        # If the message is now too generic or empty, provide a better default
        if not sanitized or sanitized == "file" or len(sanitized) < 10:
            return "An error occurred while processing your request"

        # Ensure the message starts with a capital letter
        if sanitized and sanitized[0].islower():
            sanitized = sanitized[0].upper() + sanitized[1:]

        return sanitized

    @classmethod
    def _extract_relevant_info(cls, message: str, pattern: str) -> Optional[str]:
        """Extract relevant information from error message.

        Args:
            message: The error message
            pattern: The pattern that matched

        Returns:
            Extracted information or None
        """
        # Try to extract field names - look for the actual field name after model prefix
        if "field" in pattern.lower():
            # First try to find field after model name (e.g., res.partner.field_name)
            full_field_match = re.search(
                r"[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_.]*\.([a-zA-Z_][a-zA-Z0-9_]*)",
                message,
            )
            if full_field_match:
                return full_field_match.group(1)
            # Otherwise try to find any quoted field name
            field_match = re.search(r"['\"]([a-zA-Z_][a-zA-Z0-9_]*)['\"]", message)
            if field_match:
                return field_match.group(1)

        # Try to extract model names
        model_match = re.search(
            r"model\s+['\"]?([a-zA-Z_][a-zA-Z0-9_.]*)['\"]?", message, re.IGNORECASE
        )
        if model_match and "model" in pattern.lower():
            return model_match.group(1)

        # Try to extract record IDs
        id_match = re.search(r"ID\s+(\d+)", message, re.IGNORECASE)
        if id_match and "record" in pattern.lower():
            return id_match.group(1)

        return None

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
            return (m.group(1) or m.group(2)).strip()

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
            result = m.group(1).strip()
            return result or "An error occurred while processing your request"

        # Case 3: no recognisable pattern — apply generic sanitisation
        # (strips file paths, module paths, class names, memory addresses)
        return cls.sanitize_message(fault_string)
