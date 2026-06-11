"""ORM methods for the Odoo JSON/2 connection.

This module provides Json2OrmMixin, a plain mixin class implementing the
ORM wrappers (search, read, create, write, unlink, ...) that delegate to
`self._call` on OdooJSON2Connection. All instance attributes it references
are initialized in `OdooJSON2Connection.__init__`.

Transport code (HTTP client, _call, error parsing) lives in
`odoo_json2_connection.py` — keep it there.
"""

import logging
from typing import Any, Dict, List, Optional, Union

from .exceptions import OdooConnectionError

logger = logging.getLogger(__name__)


class Json2OrmMixin:
    """Mixin with ORM methods delegating through the JSON/2 transport."""

    def search(self, model: str, domain: List[Union[str, List[Any]]], **kwargs: Any) -> List[int]:
        """Search for record IDs matching a domain.

        Args:
            model: Odoo model name
            domain: Domain filter
            **kwargs: limit, offset, order

        Returns:
            List of matching record IDs
        """
        return self._call(model, "search", domain=domain, **kwargs)

    def read(
        self, model: str, ids: List[int], fields: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Read records by IDs.

        Args:
            model: Odoo model name
            ids: Record IDs to read
            fields: Field names to return (None = all fields)

        Returns:
            List of record dicts
        """
        kwargs: Dict[str, Any] = {"ids": ids}
        if fields:
            kwargs["fields"] = fields
        return self._call(model, "read", **kwargs)

    def search_read(
        self,
        model: str,
        domain: List[Union[str, List[Any]]],
        fields: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """Search and read in one call.

        Args:
            model: Odoo model name
            domain: Domain filter
            fields: Field names to return
            **kwargs: limit, offset, order

        Returns:
            List of record dicts
        """
        if fields:
            kwargs["fields"] = fields
        return self._call(model, "search_read", domain=domain, **kwargs)

    def search_count(self, model: str, domain: List[Union[str, List[Any]]]) -> int:
        """Count records matching a domain.

        Args:
            model: Odoo model name
            domain: Domain filter

        Returns:
            Number of matching records
        """
        return self._call(model, "search_count", domain=domain)

    def fields_get(
        self, model: str, attributes: Optional[List[str]] = None
    ) -> Dict[str, Dict[str, Any]]:
        """Get field definitions for a model.

        Results are cached per model (when no specific attributes requested).

        Args:
            model: Odoo model name
            attributes: Field attributes to include in response

        Returns:
            Dict mapping field names to their metadata
        """
        # Check cache (only for full field requests)
        if not attributes and model in self._fields_cache:
            logger.debug(f"Field definitions for {model} retrieved from cache")
            return self._fields_cache[model]

        kwargs: Dict[str, Any] = {}
        if attributes:
            kwargs["attributes"] = attributes

        result = self._call(model, "fields_get", **kwargs)

        # Cache full field requests
        if not attributes:
            self._fields_cache[model] = result

        return result

    def create(self, model: str, values: Dict[str, Any]) -> int:
        """Create a new record.

        Args:
            model: Odoo model name
            values: Field values for the new record

        Returns:
            ID of the created record
        """
        # Odoo 19 JSON/2 expects vals_list (list of dicts) for create
        result = self._call(model, "create", vals_list=[values])
        # Invalidate field cache for this model (in case of computed fields)
        self._fields_cache.pop(model, None)
        # create returns a list of IDs; extract the single ID
        record_id = result[0] if isinstance(result, list) else result
        logger.info(f"Created {model} record with ID {record_id}")
        return record_id

    def create_bulk(self, model: str, vals_list: List[Dict[str, Any]]) -> List[int]:
        """Create multiple records in a single call.

        Args:
            model: Odoo model name
            vals_list: List of dicts, each containing field values for one record

        Returns:
            List of IDs of the created records
        """
        result = self._call(model, "create", vals_list=vals_list)
        self._fields_cache.pop(model, None)
        if not isinstance(result, list):
            result = [result]
        logger.info(f"Bulk created {len(result)} {model} record(s)")
        return result

    def load_records(
        self,
        model: str,
        fields: List[str],
        data: List[List[str]],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Import records using Odoo's load() method with external ID support.

        Args:
            model: Odoo model name (e.g., 'res.partner')
            fields: List of field names. Use 'id' for external ID column.
            data: List of rows, each row is a list of string values.
            context: Optional context dict (e.g., {'tracking_disable': True})

        Returns:
            Dict with 'ids' (created/updated record IDs) and 'messages' (errors)
        """
        kwargs: Dict[str, Any] = {"fields": fields, "data": data}
        if context:
            kwargs["context"] = context
        result = self._call(model, "load", **kwargs)
        self._fields_cache.pop(model, None)
        logger.info(
            f"Loaded {len(data)} row(s) into {model}: "
            f"{len(result.get('ids', []))} OK, "
            f"{len(result.get('messages', []))} messages"
        )
        return result

    def write(self, model: str, ids: List[int], values: Dict[str, Any]) -> bool:
        """Update existing records.

        Args:
            model: Odoo model name
            ids: Record IDs to update
            values: Field values to update

        Returns:
            True if successful
        """
        result = self._call(model, "write", ids=ids, vals=values)
        logger.info(f"Updated {len(ids)} {model} record(s)")
        return result

    def unlink(self, model: str, ids: List[int]) -> bool:
        """Delete records.

        Args:
            model: Odoo model name
            ids: Record IDs to delete

        Returns:
            True if successful
        """
        result = self._call(model, "unlink", ids=ids)
        logger.info(f"Deleted {len(ids)} {model} record(s)")
        return result

    def check_access_rights(self, model: str, operation: str) -> bool:
        """Check if the current user has the given access right on a model.

        Uses Odoo's built-in check_access_rights ORM method, which works for
        all users regardless of admin status (no ir.model.access read rights needed).

        Args:
            model: Odoo model name (e.g., 'res.partner')
            operation: One of 'read', 'write', 'create', 'unlink'

        Returns:
            True if access is granted, False if denied or on error
        """
        try:
            result = self._call(
                model,
                "check_access_rights",
                operation=operation,
                raise_exception=False,
            )
            return bool(result)
        except OdooConnectionError as e:
            # If check_access_rights returns 404, the method may not be exposed
            # via JSON/2 on this Odoo instance. Assume access is granted and let
            # the actual operation fail with a clear error if not permitted.
            if "Not found" in str(e):
                logger.debug(f"check_access_rights not available for {model}, assuming allowed")
                return True
            return False

    def call_method(
        self,
        model: str,
        method: str,
        ids: Optional[List[int]] = None,
        **kwargs: Any,
    ) -> Any:
        """Transport-agnostic recordset method call (JSON/2 backend).

        See `OdooConnectionProtocol.call_method` for semantics. JSON/2
        passes the recordset as `ids` in the request body.
        """
        body: Dict[str, Any] = {}
        if ids is not None:
            body["ids"] = list(ids)
        body.update(kwargs)
        return self._call(model, method, **body)

    def get_server_version(self) -> Optional[Dict[str, Any]]:
        """Get Odoo server version information.

        Returns:
            Version info dict, or None if not connected
        """
        if not self._connected:
            return None
        return self._version
