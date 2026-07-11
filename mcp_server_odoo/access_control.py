# SPDX-License-Identifier: MPL-2.0
# SPDX-FileCopyrightText: 2025 Andrey Ivanov <ivnv.xd@gmail.com>
# SPDX-FileCopyrightText: 2025-2026 Pantalytics B.V.
#
# Derived from mcp-server-odoo (https://github.com/ivnvxd/mcp-server-odoo).
# This file stays under the Mozilla Public License 2.0; see LICENSE.MPL-2.0.
"""Access control for Odoo MCP Server.

Uses Odoo's native check_access_rights to verify permissions.
Works with both JSON/2 (Odoo 19+) and XML-RPC (Odoo 14-18).
No additional Odoo modules required.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .config import OdooConfig

logger = logging.getLogger(__name__)


class AccessControlError(Exception):
    """Exception for access control failures."""

    pass


@dataclass
class ModelPermissions:
    """Permissions for a specific model."""

    model: str
    enabled: bool
    can_read: bool = False
    can_write: bool = False
    can_create: bool = False
    can_unlink: bool = False

    def can_perform(self, operation: str) -> bool:
        """Check if a specific operation is allowed."""
        operation_map = {
            "read": self.can_read,
            "write": self.can_write,
            "create": self.can_create,
            "unlink": self.can_unlink,
            "delete": self.can_unlink,  # Alias
        }
        return operation_map.get(operation, False)


@dataclass
class CacheEntry:
    """Cache entry for permission data."""

    data: Any
    timestamp: datetime

    def is_expired(self, ttl_seconds: int) -> bool:
        """Check if cache entry is expired."""
        return datetime.now() - self.timestamp > timedelta(seconds=ttl_seconds)


class AccessController:
    """Controls access to Odoo models via Odoo's native check_access_rights.

    Works with both JSON/2 (Odoo 19+) and XML-RPC (Odoo 14-18) connections.
    No additional Odoo modules required.
    """

    # Cache TTL in seconds
    CACHE_TTL = 300  # 5 minutes

    def __init__(self, config: OdooConfig, connection: Any = None, cache_ttl: int = CACHE_TTL):
        """Initialize access controller.

        Args:
            config: OdooConfig with connection details
            connection: Odoo connection (JSON/2 or XML-RPC) used to check
                        permissions via check_access_rights. When provided,
                        model access reflects the user's actual Odoo ACLs.
            cache_ttl: Cache time-to-live in seconds
        """
        self.config = config
        self.connection = connection
        self.cache_ttl = cache_ttl
        self._cache: Dict[str, CacheEntry] = {}

        if connection is not None:
            logger.info(
                "Access control via check_access_rights, cached for %d seconds.",
                cache_ttl,
            )
        else:
            logger.info("No connection — access control delegated to Odoo server.")

    def _get_from_cache(self, key: str) -> Optional[Any]:
        """Get value from cache if not expired."""
        if key in self._cache:
            entry = self._cache[key]
            if not entry.is_expired(self.cache_ttl):
                logger.debug(f"Cache hit for {key}")
                return entry.data
            else:
                logger.debug(f"Cache expired for {key}")
                del self._cache[key]
        return None

    def _set_cache(self, key: str, data: Any) -> None:
        """Set value in cache."""
        self._cache[key] = CacheEntry(data=data, timestamp=datetime.now())
        logger.debug(f"Cached {key}")

    def clear_cache(self) -> None:
        """Clear all cached data."""
        self._cache.clear()
        logger.info("Cleared access control cache")

    def _get_connection_model_permissions(self, model: str) -> "ModelPermissions":
        """Fetch permissions for a single model via Odoo's check_access_rights.

        Works for all users — no special admin rights required.
        Calls check_access_rights for each CRUD operation and caches the result.
        """
        if self.connection is None:
            return ModelPermissions(
                model=model,
                enabled=True,
                can_read=True,
                can_write=True,
                can_create=True,
                can_unlink=True,
            )

        cache_key = f"_j2_{model}"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        can_read = self.connection.check_access_rights(model, "read")
        can_write = self.connection.check_access_rights(model, "write")
        can_create = self.connection.check_access_rights(model, "create")
        can_unlink = self.connection.check_access_rights(model, "unlink")

        perms = ModelPermissions(
            model=model,
            enabled=can_read,
            can_read=can_read,
            can_write=can_write,
            can_create=can_create,
            can_unlink=can_unlink,
        )
        self._set_cache(cache_key, perms)
        logger.debug(
            "JSON/2 permissions for %s: read=%s write=%s create=%s unlink=%s",
            model,
            can_read,
            can_write,
            can_create,
            can_unlink,
        )
        return perms

    def get_enabled_models(self) -> List[Dict[str, str]]:
        """Get list of all MCP-enabled models.

        Returns:
            List of dicts with 'model' and 'name' keys

        Raises:
            AccessControlError: If request fails
        """
        # All models are accessible — Odoo's own ACLs enforce permissions
        # per user via check_access_rights at the operation level.
        return []

    def is_model_enabled(self, model: str) -> bool:
        """Check if a model is MCP-enabled.

        Args:
            model: The Odoo model name (e.g., 'res.partner')

        Returns:
            True if model is enabled, False otherwise
        """
        return self._get_connection_model_permissions(model).enabled

    def get_model_permissions(self, model: str) -> ModelPermissions:
        """Get permissions for a specific model.

        Args:
            model: The Odoo model name

        Returns:
            ModelPermissions object with permission details

        Raises:
            AccessControlError: If request fails
        """
        return self._get_connection_model_permissions(model)

    def check_operation_allowed(self, model: str, operation: str) -> Tuple[bool, Optional[str]]:
        """Check if an operation is allowed on a model.

        Args:
            model: The Odoo model name
            operation: The operation to check (read, write, create, unlink)

        Returns:
            Tuple of (allowed, error_message)
        """
        permissions = self._get_connection_model_permissions(model)
        if not permissions.can_perform(operation):
            return False, f"Operation '{operation}' not allowed on model '{model}'"
        return True, None

    def validate_model_access(self, model: str, operation: str) -> None:
        """Validate model access, raising exception if denied.

        Args:
            model: The Odoo model name
            operation: The operation to perform

        Raises:
            AccessControlError: If access is denied
        """
        allowed, error_msg = self.check_operation_allowed(model, operation)
        if not allowed:
            raise AccessControlError(error_msg or f"Access denied to {model}.{operation}")

    def filter_enabled_models(self, models: List[str]) -> List[str]:
        """Filter list of models to only include enabled ones.

        Args:
            models: List of model names to filter

        Returns:
            List of enabled model names
        """
        # In JSON/2 mode, filter to models where user has at least read access
        if self.config.api_version == "json2":
            return [m for m in models if self._get_connection_model_permissions(m).can_read]

        try:
            enabled_models = self.get_enabled_models()
            enabled_set = {m["model"] for m in enabled_models}
            return [m for m in models if m in enabled_set]
        except AccessControlError as e:
            logger.error(f"Failed to filter models: {e}")
            return []

    def get_all_permissions(self) -> Dict[str, ModelPermissions]:
        """Get permissions for all enabled models.

        Returns:
            Dict mapping model names to their permissions
        """
        permissions = {}

        try:
            enabled_models = self.get_enabled_models()

            for model_info in enabled_models:
                model = model_info["model"]
                try:
                    permissions[model] = self.get_model_permissions(model)
                except AccessControlError as e:
                    logger.warning(f"Failed to get permissions for {model}: {e}")

        except AccessControlError as e:
            logger.error(f"Failed to get all permissions: {e}")

        return permissions
