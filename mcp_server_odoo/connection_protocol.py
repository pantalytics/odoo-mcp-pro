"""Connection protocol for Odoo MCP Server.

Defines the interface that all Odoo connection implementations must satisfy.
Both OdooConnection (XML-RPC) and OdooJSON2Connection (JSON/2) conform to
this protocol, allowing tools and resources to be transport-agnostic.
"""

from typing import Any, Dict, List, Optional, Protocol, Union, runtime_checkable


@runtime_checkable
class OdooConnectionProtocol(Protocol):
    """Protocol defining the Odoo connection interface.

    Any class implementing this protocol can be used as the connection
    backend for MCP tools and resources.
    """

    @property
    def is_connected(self) -> bool: ...

    @property
    def is_authenticated(self) -> bool: ...

    @property
    def uid(self) -> Optional[int]: ...

    @property
    def database(self) -> Optional[str]: ...

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def authenticate(self, database: Optional[str] = None) -> None: ...

    def search(
        self, model: str, domain: List[Union[str, List[Any]]], **kwargs: Any
    ) -> List[int]: ...

    def read(
        self, model: str, ids: List[int], fields: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]: ...

    def search_read(
        self,
        model: str,
        domain: List[Union[str, List[Any]]],
        fields: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]: ...

    def search_count(self, model: str, domain: List[Union[str, List[Any]]]) -> int: ...

    def fields_get(
        self, model: str, attributes: Optional[List[str]] = None
    ) -> Dict[str, Dict[str, Any]]: ...

    def create(self, model: str, values: Dict[str, Any]) -> int: ...

    def create_bulk(self, model: str, vals_list: List[Dict[str, Any]]) -> List[int]: ...

    def load_records(
        self,
        model: str,
        fields: List[str],
        data: List[List[str]],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]: ...

    def write(self, model: str, ids: List[int], values: Dict[str, Any]) -> bool: ...

    def unlink(self, model: str, ids: List[int]) -> bool: ...

    def check_access_rights(self, model: str, operation: str) -> bool: ...

    def get_server_version(self) -> Optional[Dict[str, Any]]: ...

    def call_method(
        self,
        model: str,
        method: str,
        ids: Optional[List[int]] = None,
        **kwargs: Any,
    ) -> Any:
        """Call an arbitrary ORM method on a model or recordset.

        Transport-agnostic wrapper around `execute_kw` (XML-RPC) /
        `_call` (JSON/2). Use for non-CRUD methods like `message_post`,
        `action_confirm`, `activity_schedule`, etc.

        Args:
            model: Odoo model name.
            ids: Recordset ids — pass for record-level methods. Omit
                for class-level (`@api.model`) methods.
            **kwargs: Keyword arguments forwarded to the Odoo method.

        Returns:
            Whatever the Odoo method returns.
        """
        ...
