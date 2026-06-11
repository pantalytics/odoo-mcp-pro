"""Tests for RecordFormatter.

DatasetFormatter and formatting integration tests live in
tests/test_dataset_formatting.py.
"""

from datetime import date, datetime

import pytest

from mcp_server_odoo.formatters import RecordFormatter


class TestRecordFormatter:
    """Test RecordFormatter functionality."""

    @pytest.fixture
    def formatter(self):
        """Create a RecordFormatter instance."""
        return RecordFormatter("res.partner")

    def test_format_simple_record(self, formatter):
        """Test formatting a simple record."""
        record = {
            "id": 1,
            "name": "Test Company",
            "display_name": "Test Company",
            "email": "test@example.com",
            "phone": "+1234567890",
            "is_company": True,
            "active": True,
        }

        result = formatter.format_record(record)

        assert "Record: res.partner/1" in result
        assert "Name: Test Company" in result
        assert "email: test@example.com" in result
        assert "phone: +1234567890" in result
        assert "is_company: True" in result
        assert "active: True" in result  # Without metadata, boolean shows as True

    def test_format_record_with_metadata(self, formatter):
        """Test formatting with field metadata."""
        record = {
            "id": 2,
            "name": "Test User",
            "credit_limit": 5000.0,
            "user_id": False,
            "date": "2024-01-15",
            "state": "confirmed",
        }

        fields_metadata = {
            "credit_limit": {"type": "monetary"},
            "user_id": {"type": "many2one", "relation": "res.users"},
            "date": {"type": "date"},
            "state": {
                "type": "selection",
                "selection": [("draft", "Draft"), ("confirmed", "Confirmed"), ("done", "Done")],
            },
        }

        result = formatter.format_record(record, fields_metadata)

        assert "credit_limit: 5,000.00" in result  # Monetary formatting
        assert "user_id: Not set" in result
        assert "date: 2024-01-15" in result
        assert "state: Confirmed (confirmed)" in result  # Selection formatting

    def test_format_numeric_fields(self, formatter):
        """Test formatting of numeric fields."""
        record = {
            "id": 3,
            "name": "Test",
            "int_field": 12345,
            "float_field": 3.14159,
            "monetary_field": 9999.99,
        }

        fields_metadata = {
            "int_field": {"type": "integer"},
            "float_field": {"type": "float", "digits": (16, 4)},
            "monetary_field": {"type": "monetary"},
        }

        result = formatter.format_record(record, fields_metadata)

        assert "int_field: 12,345" in result  # Integer with thousand separator
        assert "float_field: 3.1416" in result  # Float with specified precision
        assert "monetary_field: 9,999.99" in result  # Monetary formatting

    def test_format_many2one_field(self, formatter):
        """Test formatting of many2one fields."""
        record = {
            "id": 4,
            "name": "Test",
            "partner_id": (10, "Parent Company"),
            "country_id": False,
        }

        fields_metadata = {
            "partner_id": {"type": "many2one", "relation": "res.partner"},
            "country_id": {"type": "many2one", "relation": "res.country"},
        }

        result = formatter.format_record(record, fields_metadata)

        assert "Relationships:" in result
        assert "partner_id: Parent Company (odoo://res.partner/record/10)" in result
        assert "country_id: Not set" in result

    def test_format_one2many_field(self, formatter):
        """Test formatting of one2many fields."""
        record = {"id": 5, "name": "Parent", "child_ids": [1, 2, 3, 4, 5]}

        fields_metadata = {
            "child_ids": {
                "type": "one2many",
                "relation": "res.partner",
                "relation_field": "parent_id",
            }
        }

        result = formatter.format_record(record, fields_metadata)

        assert "Relationships:" in result
        assert "child_ids: 5 record(s)" in result
        assert "→ View all: odoo://res.partner/search?" in result

    def test_format_many2many_field(self, formatter):
        """Test formatting of many2many fields."""
        record = {"id": 6, "name": "Test", "tag_ids": [10, 20, 30]}

        fields_metadata = {"tag_ids": {"type": "many2many", "relation": "res.partner.category"}}

        result = formatter.format_record(record, fields_metadata)

        assert "tag_ids: 3 record(s)" in result
        assert "odoo://res.partner.category/search?domain" in result

    def test_format_binary_field(self, formatter):
        """Test formatting of binary fields."""
        record = {"id": 7, "name": "Test", "image": b"fake_binary_data"}

        fields_metadata = {"image": {"type": "binary"}}

        result = formatter.format_record(record, fields_metadata)

        assert "[Binary data - use res.partner/image to retrieve]" in result

    def test_omit_internal_fields(self, formatter):
        """Test that internal fields are omitted."""
        record = {
            "id": 8,
            "name": "Test",
            "email": "test@example.com",
            "__last_update": "2024-01-01 00:00:00",
            "write_date": "2024-01-01 00:00:00",
            "create_uid": (1, "Admin"),
            "_prefetch_field": "internal",
        }

        result = formatter.format_record(record)

        assert "__last_update" not in result
        assert "write_date" not in result
        assert "create_uid" not in result
        assert "_prefetch_field" not in result
        assert "email: test@example.com" in result

    def test_format_list(self, formatter):
        """Test formatting a list of records."""
        records = [
            {"id": 1, "name": "Company A", "display_name": "Company A"},
            {"id": 2, "name": "Company B", "display_name": "Company B"},
            {"id": 3, "name": "Company C", "display_name": "Company C"},
        ]

        result = formatter.format_list(records)

        assert "res.partner Records (3 found)" in result
        assert "[1] Company A" in result
        assert "[2] Company B" in result
        assert "[3] Company C" in result

    def test_format_empty_list(self, formatter):
        """Test formatting an empty list."""
        result = formatter.format_list([])

        assert "No res.partner records found." in result

    def test_format_datetime_field(self, formatter):
        """Test formatting of datetime fields."""
        record = {
            "id": 9,
            "name": "Test",
            "date_field": "2024-01-15",
            "datetime_field": "2024-01-15 14:30:00",
            "datetime_compact": "20240115T14:30:00",  # Odoo compact format
            "date_obj": date(2024, 1, 15),
            "datetime_obj": datetime(2024, 1, 15, 14, 30),
        }

        fields_metadata = {
            "date_field": {"type": "date"},
            "datetime_field": {"type": "datetime"},
            "datetime_compact": {"type": "datetime"},
            "date_obj": {"type": "date"},
            "datetime_obj": {"type": "datetime"},
        }

        result = formatter.format_record(record, fields_metadata)

        assert "date_field: 2024-01-15" in result
        assert "datetime_field: 2024-01-15T14:30:00+00:00" in result
        assert "datetime_compact: 2024-01-15T14:30:00+00:00" in result
        assert "date_obj: 2024-01-15" in result
        assert "datetime_obj: 2024-01-15T14:30:00+00:00" in result
