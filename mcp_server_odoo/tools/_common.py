"""Shared module-level constants for the tools package."""

from __future__ import annotations

import asyncio
import contextvars
import re

from ..logging_config import get_logger

logger = get_logger("mcp_server_odoo.tools")

MAX_BULK_SIZE = 1000  # Maximum records per bulk operation
MAX_BINARY_SIZE_BYTES = 25 * 1024 * 1024  # set_binary_field upload cap
MAX_CONCURRENT_BINARY_UPLOADS = 3  # parallel set_binary_field calls; higher OOMs the server
_BINARY_UPLOAD_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_BINARY_UPLOADS)
_AVATAR_FIELD_RE = re.compile(r"^avatar_(128|256|512|1024|1920)$")

# ContextVar to pass the current user's sub from _get_user_context to the wrapper
_current_sub: contextvars.ContextVar[str] = contextvars.ContextVar("_current_sub", default="stdio")
