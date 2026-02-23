"""Odoo JSON/2 API connection management.

This module provides the OdooJSON2Connection class for connecting to
Odoo 19+ via the JSON/2 external API endpoint (/json/2/).

The JSON/2 API is Odoo 19's replacement for XML-RPC and JSON-RPC,
both of which are scheduled for removal in Odoo 20.

Reference: https://www.odoo.com/documentation/19.0/developer/reference/external_api.html
"""

import logging
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlparse

import httpx

from .config import OdooConfig
from .error_sanitizer import ErrorSanitizer

from .exceptions import OdooConnectionError  # noqa: F401

logger = logging.getLogger(__name__)


class OdooJSON2Connection:
    """Manages connections to Odoo via the JSON/2 API.

    The JSON/2 API uses simple HTTP POST requests with Bearer token auth.
    Each request is a POST to /json/2/{model}/{method} with a flat JSON body.

    Key differences from XML-RPC:
    - Auth via Authorization: Bearer header (not uid+password per call)
    - Database via X-Odoo-Database header
    - All arguments are named (no positional args)
    - ids and context are top-level keys in request body
    - Responses are raw JSON (no RPC envelope)
    - Proper HTTP status codes for errors
    """

    DEFAULT_TIMEOUT = 30

    def __init__(self, config: OdooConfig, timeout: int = DEFAULT_TIMEOUT):
        """Initialize connection with configuration.

        Args:
            config: OdooConfig object with connection parameters
            timeout: HTTP request timeout in seconds
        """
        self.config = config
        self.timeout = timeout

        # Parse and validate URL
        parsed = urlparse(config.url)
        if parsed.scheme not in ("http", "https"):
            raise OdooConnectionError(
                f"Invalid URL scheme: {parsed.scheme}. Must be http or https"
            )
        if not parsed.hostname:
            raise OdooConnectionError("Invalid URL: missing hostname")

        self._base_url = config.url.rstrip("/")
        self._json2_url = f"{self._base_url}/json/2"

        # Connection state
        self._connected = False
        self._authenticated = False
        self._uid: Optional[int] = None
        self._database: Optional[str] = None
        self._version: Optional[Dict[str, Any]] = None

        # httpx client (created on connect)
        self._client: Optional[httpx.Client] = None

        # Field cache
        self._fields_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}

        logger.info(f"Initialized OdooJSON2Connection for {parsed.hostname}")

    def _build_headers(self) -> Dict[str, str]:
        """Build HTTP headers for JSON/2 requests.

        Returns:
            Dict with Authorization, Content-Type, and X-Odoo-Database headers
        """
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json; charset=utf-8",
        }
        if self._database:
            headers["X-Odoo-Database"] = self._database
        return headers

    def _call(self, model: str, method: str, **kwargs: Any) -> Any:
        """Make a JSON/2 API call.

        Args:
            model: Odoo model name (e.g., 'res.partner')
            method: ORM method name (e.g., 'search_read')
            **kwargs: Named arguments for the method. Special keys:
                - ids: list of record IDs (for record-level methods)
                - context: dict of context values

        Returns:
            The parsed JSON response (raw, no envelope)

        Raises:
            OdooConnectionError: If the request fails
        """
        if not self._client:
            raise OdooConnectionError("Not connected. Call connect() first.")

        url = f"{self._json2_url}/{model}/{method}"

        # Build request body from kwargs, filtering out None values
        body = {k: v for k, v in kwargs.items() if v is not None}

        logger.debug(f"JSON/2 call: POST {url} body={body}")

        try:
            response = self._client.post(url, json=body)
        except httpx.TimeoutException:
            raise OdooConnectionError(
                f"Request timeout after {self.timeout}s: {model}/{method}"
            ) from None
        except httpx.ConnectError as e:
            raise OdooConnectionError(f"Connection failed: {e}") from e
        except httpx.HTTPError as e:
            raise OdooConnectionError(f"HTTP error: {e}") from e

        # Handle error responses
        if response.status_code == 200:
            return response.json()

        # Parse error body
        error_msg = self._parse_error_response(response)

        if response.status_code == 401:
            raise OdooConnectionError(f"Authentication failed: {error_msg}")
        elif response.status_code == 403:
            raise OdooConnectionError(f"Access denied: {error_msg}")
        elif response.status_code == 404:
            raise OdooConnectionError(f"Not found: {error_msg}")
        elif response.status_code == 422:
            raise OdooConnectionError(f"Invalid request: {error_msg}")
        else:
            raise OdooConnectionError(
                f"Server error ({response.status_code}): {error_msg}"
            )

    def _parse_error_response(self, response: httpx.Response) -> str:
        """Extract error message from a JSON/2 error response.

        JSON/2 error responses contain:
        {
            "name": "exception.class.Name",
            "message": "human-readable message",
            "arguments": [...],
            "context": {},
            "debug": "full traceback"
        }
        """
        try:
            data = response.json()
            message = data.get("message", "")
            return ErrorSanitizer.sanitize_message(str(message))
        except Exception:
            return ErrorSanitizer.sanitize_message(response.text[:200])

    # --- Connection lifecycle ---

    def connect(self) -> None:
        """Establish connection to Odoo server.

        Creates an httpx client and verifies the server is reachable
        by fetching the version endpoint.

        Raises:
            OdooConnectionError: If connection fails
        """
        if self._connected:
            logger.warning("Already connected to Odoo")
            return

        try:
            self._client = httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
            )

            # Test connection by fetching server version
            self._version = self._fetch_version()
            self._connected = True

            version_str = self._version.get("server_version", "unknown")
            logger.info(f"Connected to Odoo {version_str}")

        except OdooConnectionError:
            self._cleanup_client()
            raise
        except Exception as e:
            self._cleanup_client()
            raise OdooConnectionError(f"Connection failed: {e}") from e

    def _fetch_version(self) -> Dict[str, Any]:
        """Fetch Odoo server version (no auth required).

        Returns:
            Version info dict

        Raises:
            OdooConnectionError: If request fails
        """
        try:
            response = self._client.get(f"{self._base_url}/web/version")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise OdooConnectionError(
                f"Failed to fetch server version: HTTP {e.response.status_code}"
            ) from e
        except Exception as e:
            raise OdooConnectionError(f"Failed to fetch server version: {e}") from e

    def disconnect(self, suppress_logging: bool = False) -> None:
        """Close connection and cleanup resources."""
        if not self._connected:
            if not suppress_logging:
                try:
                    logger.warning("Not connected to Odoo")
                except (ValueError, RuntimeError):
                    pass
            return

        self._cleanup_client()
        self._connected = False
        self._authenticated = False
        self._uid = None
        self._database = None
        self._fields_cache.clear()

        if not suppress_logging:
            try:
                logger.info("Disconnected from Odoo server")
            except (ValueError, RuntimeError):
                pass

    def _cleanup_client(self) -> None:
        """Close the httpx client if open."""
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def authenticate(self, database: Optional[str] = None) -> None:
        """Authenticate with Odoo using Bearer token.

        For JSON/2, authentication is stateless — the API key is sent
        with every request. This method resolves the database name and
        retrieves the authenticated user's UID via context_get.

        Args:
            database: Database name. If not provided, uses config.database.

        Raises:
            OdooConnectionError: If authentication fails
        """
        if not self._connected:
            raise OdooConnectionError("Not connected. Call connect() first.")

        if not self.config.api_key:
            raise OdooConnectionError(
                "API key required for JSON/2 authentication. Set ODOO_API_KEY."
            )

        # Resolve database (optional for single-db instances like odoo.sh)
        self._database = database or self.config.database

        # Update client headers now that we have the database
        self._client.headers.update(self._build_headers())

        # Get UID by calling res.users/context_get
        try:
            context = self._call("res.users", "context_get")
            self._uid = context.get("uid")

            if not self._uid:
                raise OdooConnectionError(
                    "Authentication failed: could not retrieve user ID"
                )

            self._authenticated = True
            logger.info(
                f"Authenticated via JSON/2 as UID {self._uid} "
                f"on database '{self._database}'"
            )

        except OdooConnectionError:
            raise
        except Exception as e:
            raise OdooConnectionError(f"Authentication failed: {e}") from e

    # --- Properties ---

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_authenticated(self) -> bool:
        return self._authenticated

    @property
    def uid(self) -> Optional[int]:
        return self._uid

    @property
    def database(self) -> Optional[str]:
        return self._database

    # --- ORM methods ---

    def search(
        self, model: str, domain: List[Union[str, List[Any]]], **kwargs: Any
    ) -> List[int]:
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

    def search_count(
        self, model: str, domain: List[Union[str, List[Any]]]
    ) -> int:
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

    def write(
        self, model: str, ids: List[int], values: Dict[str, Any]
    ) -> bool:
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

    def get_server_version(self) -> Optional[Dict[str, Any]]:
        """Get Odoo server version information.

        Returns:
            Version info dict, or None if not connected
        """
        if not self._connected:
            return None
        return self._version

    # --- Context manager ---

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False

    def __del__(self):
        try:
            if hasattr(self, "_connected") and self._connected:
                self.disconnect(suppress_logging=True)
        except (ValueError, AttributeError, RuntimeError):
            pass
