"""Per-request connection context for multi-tenant mode.

The current_connection ContextVar is set by ASGI middleware (in server.py)
and read by tool/resource handlers to get the correct Odoo connection
for the current request.
"""

from contextvars import ContextVar
from typing import Any, Optional

current_connection: ContextVar[Optional[Any]] = ContextVar("current_connection", default=None)
