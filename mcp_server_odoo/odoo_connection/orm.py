# SPDX-License-Identifier: MPL-2.0
# SPDX-FileCopyrightText: 2025 Andrey Ivanov <ivnv.xd@gmail.com>
# SPDX-FileCopyrightText: 2025-2026 Pantalytics B.V.
#
# Derived from mcp-server-odoo (https://github.com/ivnvxd/mcp-server-odoo).
# This file stays under the Mozilla Public License 2.0; see LICENSE.MPL-2.0.
"""ORM execution methods for OdooConnection.

This module provides OdooConnectionOrmMixin, a plain mixin class that
implements the ORM wrappers (search, read, create, write, unlink, ...)
on top of execute_kw. All instance attributes it references are
initialized in `core.OdooConnection.__init__`.
"""

import logging
import socket
import xmlrpc.client
from typing import Any, Dict, List, Optional, Union

from ..error_sanitizer import ErrorSanitizer
from ..exceptions import OdooConnectionError

logger = logging.getLogger(__name__)


class OdooConnectionOrmMixin:
    """Mixin with ORM execution methods."""

    def execute(self, model: str, method: str, *args) -> Any:
        """Execute an operation on an Odoo model.

        This is a simplified interface that calls execute_kw with empty kwargs.

        Args:
            model: The Odoo model name (e.g., 'res.partner')
            method: The method to call (e.g., 'search', 'read')
            *args: Arguments to pass to the method

        Returns:
            The result from Odoo

        Raises:
            OdooConnectionError: If not authenticated or execution fails
        """
        return self.execute_kw(model, method, list(args), {})

    def call_method(
        self,
        model: str,
        method: str,
        ids: Optional[List[int]] = None,
        **kwargs: Any,
    ) -> Any:
        """Transport-agnostic recordset method call (XML-RPC backend).

        See `OdooConnectionProtocol.call_method` for semantics.
        """
        args = [list(ids)] if ids is not None else []
        return self.execute_kw(model, method, args, kwargs)

    def execute_kw(self, model: str, method: str, args: List[Any], kwargs: Dict[str, Any]) -> Any:
        """Execute an operation on an Odoo model with keyword arguments.

        This is the main method for interacting with Odoo models via XML-RPC.

        Args:
            model: The Odoo model name (e.g., 'res.partner')
            method: The method to call (e.g., 'search_read')
            args: List of positional arguments for the method
            kwargs: Dictionary of keyword arguments for the method

        Returns:
            The result from Odoo

        Raises:
            OdooConnectionError: If not authenticated or execution fails
        """
        if not self._authenticated:
            raise OdooConnectionError("Not authenticated. Call authenticate() first.")

        if not self._connected:
            raise OdooConnectionError("Not connected to Odoo")

        # Get the appropriate password/token based on auth method
        password_or_token = (
            self.config.api_key if self._auth_method == "api_key" else self.config.password
        )

        try:
            # Log the operation
            logger.debug(f"Executing {method} on {model} with args={args}, kwargs={kwargs}")

            # Execute via object proxy
            result = self.object_proxy.execute_kw(
                self._database, self._uid, password_or_token, model, method, args, kwargs
            )

            logger.debug("Operation completed successfully")
            return result

        except xmlrpc.client.Fault as e:
            # Odoo's XML-RPC endpoint marshals responses with allow_none=False,
            # so a method that legitimately returns None (e.g.
            # account.move.button_draft, account.payment.action_cancel,
            # stock.picking.button_validate on a full transfer) raises
            # "cannot marshal None" *after* the method already ran and committed.
            # Treat that as the successful void return it is — otherwise the
            # caller sees a false failure and may retry a financial action that
            # in fact succeeded (double post / double payment). Verified against
            # Odoo 18 + 19: the state change persists despite this fault.
            if "cannot marshal None" in (e.faultString or ""):
                logger.debug(
                    f"{method} on {model} returned None; XML-RPC cannot encode "
                    f"None, treating as a successful void return"
                )
                return None
            logger.error(f"XML-RPC fault during {method} on {model}: {e}")
            # Sanitize the fault string before exposing to user
            sanitized_message = ErrorSanitizer.sanitize_xmlrpc_fault(e.faultString)
            raise OdooConnectionError(f"Operation failed: {sanitized_message}") from e
        except socket.timeout:
            logger.error(f"Timeout during {method} on {model}")
            raise OdooConnectionError(f"Operation timeout after {self.timeout} seconds") from None
        except Exception as e:
            logger.error(f"Error during {method} on {model}: {e}")
            # Sanitize generic errors as well
            sanitized_message = ErrorSanitizer.sanitize_message(str(e))
            raise OdooConnectionError(f"Operation failed: {sanitized_message}") from e

    def search(self, model: str, domain: List[Union[str, List[Any]]], **kwargs) -> List[int]:
        """Search for records matching a domain.

        Args:
            model: The Odoo model name
            domain: Odoo domain filter (e.g., [['is_company', '=', True]])
            **kwargs: Additional parameters (limit, offset, order)

        Returns:
            List of record IDs matching the domain
        """
        return self.execute_kw(model, "search", [domain], kwargs)

    def read(
        self, model: str, ids: List[int], fields: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Read records by IDs.

        Args:
            model: The Odoo model name
            ids: List of record IDs to read
            fields: List of field names to read (None for all fields)

        Returns:
            List of dictionaries containing record data
        """
        kwargs = {}
        if fields:
            kwargs["fields"] = fields

        with self._performance_manager.monitor.track_operation(f"read_{model}"):
            records = self.execute_kw(model, "read", [ids], kwargs)

        return records

    def search_read(
        self,
        model: str,
        domain: List[Union[str, List[Any]]],
        fields: Optional[List[str]] = None,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """Search for records and read their data in one operation.

        Args:
            model: The Odoo model name
            domain: Odoo domain filter
            fields: List of field names to read (None for all fields)
            **kwargs: Additional parameters (limit, offset, order)

        Returns:
            List of dictionaries containing record data
        """
        if fields:
            kwargs["fields"] = fields
        return self.execute_kw(model, "search_read", [domain], kwargs)

    def fields_get(
        self, model: str, attributes: Optional[List[str]] = None
    ) -> Dict[str, Dict[str, Any]]:
        """Get field definitions for a model.

        Args:
            model: The Odoo model name
            attributes: List of field attributes to return

        Returns:
            Dictionary mapping field names to their definitions
        """
        # Check cache first
        cached_fields = self._performance_manager.get_cached_fields(model)
        if cached_fields and not attributes:  # Only use cache if no specific attributes requested
            logger.debug(f"Field definitions for {model} retrieved from cache")
            return cached_fields

        # Get fields from server
        kwargs = {}
        if attributes:
            kwargs["attributes"] = attributes

        with self._performance_manager.monitor.track_operation(f"fields_get_{model}"):
            fields = self.execute_kw(model, "fields_get", [], kwargs)

        # Cache if we got all attributes
        if not attributes:
            self._performance_manager.cache_fields(model, fields)

        return fields

    def search_count(self, model: str, domain: List[Union[str, List[Any]]]) -> int:
        """Count records matching a domain.

        Args:
            model: The Odoo model name
            domain: Odoo domain filter

        Returns:
            Number of records matching the domain
        """
        return self.execute_kw(model, "search_count", [domain], {})

    def create(
        self, model: str, values: Dict[str, Any], context: Optional[Dict[str, Any]] = None
    ) -> int:
        """Create a new record.

        Args:
            model: The Odoo model name
            values: Dictionary of field values for the new record
            context: Optional Odoo context. Needed for records whose defaults
                depend on it, e.g. transient wizards that read active_model /
                active_ids / default_* from the context.

        Returns:
            ID of the created record

        Raises:
            OdooConnectionError: If creation fails
        """
        try:
            with self._performance_manager.monitor.track_operation(f"create_{model}"):
                kw = {"context": context} if context else {}
                record_id = self.execute_kw(model, "create", [values], kw)
                # Invalidate cache for this model
                self._performance_manager.invalidate_record_cache(model)
                logger.info(f"Created {model} record with ID {record_id}")
                return record_id
        except Exception as e:
            logger.error(f"Failed to create {model} record: {e}")
            raise

    def create_bulk(self, model: str, vals_list: List[Dict[str, Any]]) -> List[int]:
        """Create multiple records in a single call.

        Args:
            model: Odoo model name
            vals_list: List of dicts, each containing field values for one record

        Returns:
            List of IDs of the created records

        Raises:
            OdooConnectionError: If creation fails
        """
        try:
            with self._performance_manager.monitor.track_operation(f"create_bulk_{model}"):
                result = self.execute_kw(model, "create", [vals_list], {})
                self._performance_manager.invalidate_record_cache(model)
                if not isinstance(result, list):
                    result = [result]
                logger.info(f"Bulk created {len(result)} {model} record(s)")
                return result
        except Exception as e:
            logger.error(f"Failed to bulk create {model} records: {e}")
            raise

    def load_records(
        self,
        model: str,
        fields: List[str],
        data: List[List[str]],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Import records using Odoo's load() method with external ID support.

        This wraps Odoo's native load() ORM method, which supports:
        - Idempotent upsert via external IDs (column name 'id')
        - Relational field resolution via external IDs (e.g., 'parent_id/id')
        - Batch processing with per-row error reporting

        Args:
            model: Odoo model name (e.g., 'res.partner')
            fields: List of field names. Use 'id' for external ID column.
                    Use 'field/id' to reference related records by external ID.
            data: List of rows, each row is a list of string values.
                  Values must be strings (Odoo load() expects strings).
            context: Optional context dict (e.g., {'tracking_disable': True})

        Returns:
            Dict with 'ids' (created/updated record IDs) and 'messages' (errors)

        Raises:
            OdooConnectionError: If the load operation fails
        """
        try:
            with self._performance_manager.monitor.track_operation(f"load_{model}"):
                kwargs = {}
                if context:
                    kwargs["context"] = context
                result = self.execute_kw(model, "load", [fields, data], kwargs)
                self._performance_manager.invalidate_record_cache(model)
                logger.info(
                    f"Loaded {len(data)} row(s) into {model}: "
                    f"{len(result.get('ids', []))} OK, "
                    f"{len(result.get('messages', []))} messages"
                )
                return result
        except Exception as e:
            logger.error(f"Failed to load records into {model}: {e}")
            raise

    def write(self, model: str, ids: List[int], values: Dict[str, Any]) -> bool:
        """Update existing records.

        Args:
            model: The Odoo model name
            ids: List of record IDs to update
            values: Dictionary of field values to update

        Returns:
            True if update was successful

        Raises:
            OdooConnectionError: If update fails
        """
        try:
            with self._performance_manager.monitor.track_operation(f"write_{model}"):
                result = self.execute_kw(model, "write", [ids, values], {})
                # Invalidate cache for updated records
                for record_id in ids:
                    self._performance_manager.invalidate_record_cache(model, record_id)
                logger.info(f"Updated {len(ids)} {model} record(s)")
                return result
        except Exception as e:
            logger.error(f"Failed to update {model} records: {e}")
            raise

    def unlink(self, model: str, ids: List[int]) -> bool:
        """Delete records.

        Args:
            model: The Odoo model name
            ids: List of record IDs to delete

        Returns:
            True if deletion was successful

        Raises:
            OdooConnectionError: If deletion fails
        """
        try:
            with self._performance_manager.monitor.track_operation(f"unlink_{model}"):
                result = self.execute_kw(model, "unlink", [ids], {})
                # Invalidate cache for deleted records
                for record_id in ids:
                    self._performance_manager.invalidate_record_cache(model, record_id)
                logger.info(f"Deleted {len(ids)} {model} record(s)")
                return result
        except Exception as e:
            logger.error(f"Failed to delete {model} records: {e}")
            raise

    def check_access_rights(self, model: str, operation: str) -> bool:
        """Check if the current user has the given access right on a model.

        Args:
            model: Odoo model name (e.g., 'res.partner')
            operation: One of 'read', 'write', 'create', 'unlink'

        Returns:
            True if access is granted, False if denied or on error
        """
        try:
            result = self.execute_kw(
                model, "check_access_rights", [operation], {"raise_exception": False}
            )
            return bool(result)
        except Exception as e:
            # If model doesn't exist (module not installed), assume no access
            error_str = str(e).lower()
            if "doesn't exist" in error_str or "does not exist" in error_str:
                return False
            # For other errors (network, etc.), assume access is granted
            # and let the actual operation fail with a clear error
            return True

    def get_server_version(self) -> Optional[Dict[str, Any]]:
        """Get Odoo server version information.

        Returns:
            Dictionary with version information or None if not connected
        """
        if not self._connected:
            return None

        try:
            return self.common_proxy.version()  # type: ignore[invalid-return-type]  # XML-RPC proxy is untyped
        except Exception as e:
            logger.error(f"Failed to get server version: {e}")
            return None
