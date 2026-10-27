"""Odoo's load() answers ids=False when one bad row rolls the batch back.

Both transports must hand that result on, so record_import can show Odoo's
own row messages instead of crashing on len(False)."""

from unittest.mock import MagicMock, Mock, patch

from mcp_server_odoo.config import OdooConfig
from mcp_server_odoo.odoo_connection import OdooConnection
from mcp_server_odoo.odoo_json2_connection import OdooJSON2Connection
from mcp_server_odoo.performance import PerformanceManager

ROLLED_BACK = {
    "ids": False,
    "messages": [{"type": "error", "message": "No matching record", "rows": {"from": 0}}],
}


def test_xmlrpc_load_hands_on_a_rolled_back_batch():
    config = Mock(spec=OdooConfig)
    config.url = "http://localhost:8069"
    config.get_endpoint_paths.return_value = {
        "db": "/xmlrpc/2/db",
        "common": "/xmlrpc/2/common",
        "object": "/xmlrpc/2/object",
    }
    conn = OdooConnection(config, performance_manager=PerformanceManager(config))
    with patch.object(conn, "execute_kw", return_value=ROLLED_BACK):
        assert conn.load_records("res.partner", ["name"], [["A"]]) == ROLLED_BACK


def test_json2_load_hands_on_a_rolled_back_batch():
    conn = MagicMock(spec=OdooJSON2Connection)
    conn._call = MagicMock(return_value=ROLLED_BACK)
    conn._fields_cache = {}
    result = OdooJSON2Connection.load_records(conn, "res.partner", ["name"], [["A"]])
    assert result == ROLLED_BACK
