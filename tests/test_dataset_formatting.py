"""Tests for DatasetFormatter and formatting integration with real Odoo data.

RecordFormatter unit tests live in tests/test_record_formatting.py.
"""

import pytest

from mcp_server_odoo.config import get_config
from mcp_server_odoo.formatters import DatasetFormatter, RecordFormatter
from mcp_server_odoo.odoo_connection import OdooConnection
from mcp_server_odoo.odoo_connection import OdooConnectionError as XMLRPCConnectionError
from mcp_server_odoo.odoo_json2_connection import OdooConnectionError as JSON2ConnectionError

# Catch either connection error type
ConnectionError_ = (XMLRPCConnectionError, JSON2ConnectionError)


def _create_connection(config):
    """Create the appropriate connection based on api_version."""
    if config.api_version == "json2":
        from mcp_server_odoo.odoo_json2_connection import OdooJSON2Connection

        return OdooJSON2Connection(config)
    return OdooConnection(config)


class TestDatasetFormatter:
    """Test DatasetFormatter functionality."""

    @pytest.fixture
    def formatter(self):
        """Create a DatasetFormatter instance."""
        return DatasetFormatter("res.partner")

    def test_format_search_results(self, formatter):
        """Test formatting search results."""
        records = [
            {"id": 1, "name": "Company A", "email": "a@example.com"},
            {"id": 2, "name": "Company B", "email": "b@example.com"},
        ]

        result = formatter.format_search_results(
            records,
            domain=[("is_company", "=", True)],
            fields=["name", "email"],
            limit=10,
            offset=0,
            total_count=50,
        )

        assert "Search Results: res.partner" in result
        assert "Search criteria: is_company = True" in result
        assert "Showing records 1-2 of 50" in result
        assert "Fields: name, email" in result
        assert "[1] Company A" in result
        assert "email: a@example.com" in result

    def test_format_empty_search_results(self, formatter):
        """Test formatting empty search results."""
        result = formatter.format_search_results(
            [], domain=[("name", "ilike", "nonexistent")], total_count=0
        )

        assert "No records found matching the criteria." in result
        assert "Search criteria: name ilike nonexistent" in result

    def test_format_search_with_pagination(self, formatter):
        """Test formatting with pagination info."""
        records = [{"id": i, "name": f"Record {i}"} for i in range(11, 21)]

        result = formatter.format_search_results(
            records,
            limit=10,
            offset=10,
            total_count=30,
            current_page=2,
            total_pages=3,
            prev_uri="odoo://res.partner/search?limit=10&offset=0",
            next_uri="odoo://res.partner/search?limit=10&offset=20",
        )

        assert "Page 2 of 3" in result
        assert "Showing records 11-20 of 30" in result
        assert "[11] Record 11" in result
        assert "[20] Record 20" in result
        assert "← Previous page: odoo://res.partner/search?limit=10&offset=0" in result
        assert "→ Next page: odoo://res.partner/search?limit=10&offset=20" in result

    def test_format_complex_domain(self, formatter):
        """Test formatting complex search domains."""
        domain = [
            "|",
            ("is_company", "=", True),
            "&",
            ("customer_rank", ">", 0),
            ("active", "=", True),
        ]

        records = [{"id": 1, "name": "Test"}]
        result = formatter.format_search_results(records, domain=domain)

        assert "| is_company = True & customer_rank > 0 active = True" in result

    def test_format_search_with_selected_fields(self, formatter):
        """Test formatting with specific fields shown inline."""
        records = [
            {
                "id": 1,
                "name": "Test Company",
                "email": "test@example.com",
                "phone": "123-456-7890",
                "is_company": True,
            }
        ]

        result = formatter.format_search_results(records, fields=["email", "phone", "is_company"])

        assert "[1] Test Company" in result
        assert "    email: test@example.com" in result
        assert "    phone: 123-456-7890" in result
        assert "    is_company: Yes" in result


class TestFormattingIntegration:
    """Integration tests with real Odoo data."""

    @pytest.mark.integration
    def test_format_real_partner_record(self):
        """Test formatting real partner records from Odoo."""
        config = get_config()
        connection = _create_connection(config)

        try:
            connection.connect()
            connection.authenticate()

            # Get a partner record with fields metadata
            try:
                partner_ids = connection.search("res.partner", [], limit=1)
            except ConnectionError_ as e:
                if "429" in str(e) or "Too many requests" in str(e):
                    pytest.skip("Rate limited by server")
                raise

            if partner_ids:
                # Get fields metadata
                fields_meta = connection.fields_get("res.partner")

                # Read the record with specific fields to avoid marshaling issues
                records = connection.read(
                    "res.partner",
                    partner_ids,
                    [
                        "name",
                        "email",
                        "phone",
                        "street",
                        "city",
                        "country_id",
                        "is_company",
                        "child_ids",
                        "parent_id",
                    ],
                )

                # Format the record
                formatter = RecordFormatter("res.partner")
                result = formatter.format_record(records[0], fields_meta)

                # Basic assertions
                assert f"Record: res.partner/{partner_ids[0]}" in result
                assert "Fields:" in result or "Relationships:" in result
                assert "=" * 50 in result

        finally:
            connection.disconnect()

    @pytest.mark.integration
    def test_format_real_search_results(self):
        """Test formatting real search results from Odoo."""
        config = get_config()
        connection = _create_connection(config)

        try:
            connection.connect()
            connection.authenticate()

            # Search for companies
            domain = [("is_company", "=", True)]
            records = connection.search_read(
                "res.partner", domain, fields=["name", "email", "phone", "country_id"], limit=5
            )

            # Get total count
            total = connection.search_count("res.partner", domain)

            # Format the results
            formatter = DatasetFormatter("res.partner")
            # Calculate pagination info
            current_page = 1
            total_pages = (total + 4) // 5 if total > 0 else 1
            next_uri = "odoo://res.partner/search?limit=5&offset=5" if total > 5 else None

            result = formatter.format_search_results(
                records,
                domain=domain,
                fields=["name", "email", "phone", "country_id"],
                limit=5,
                offset=0,
                total_count=total,
                current_page=current_page,
                total_pages=total_pages,
                next_uri=next_uri,
            )

            # Basic assertions
            assert "Search Results: res.partner" in result
            assert "is_company = True" in result
            assert f"of {total}" in result

            # Check for specific fields if records exist
            if records:
                assert "[1]" in result
                if "email" in records[0] and records[0]["email"]:
                    assert "email:" in result

        finally:
            connection.disconnect()

    @pytest.mark.integration
    def test_format_record_with_relationships(self):
        """Test formatting records with relationship fields."""
        config = get_config()
        connection = _create_connection(config)

        try:
            connection.connect()
            connection.authenticate()

            # Find a partner with relationships
            domain = ["|", ("child_ids", "!=", False), ("parent_id", "!=", False)]
            partner_ids = connection.search("res.partner", domain, limit=1)

            if partner_ids:
                # Get fields metadata
                fields_meta = connection.fields_get("res.partner")

                # Read the record with specific fields to avoid marshaling issues
                records = connection.read(
                    "res.partner",
                    partner_ids,
                    [
                        "name",
                        "email",
                        "phone",
                        "street",
                        "city",
                        "country_id",
                        "is_company",
                        "child_ids",
                        "parent_id",
                    ],
                )

                # Format the record
                formatter = RecordFormatter("res.partner")
                result = formatter.format_record(records[0], fields_meta)

                # Check for relationships section
                if "parent_id" in records[0] and records[0]["parent_id"]:
                    assert "Relationships:" in result
                    assert "parent_id:" in result
                    assert "odoo://res.partner/record/" in result

                if "child_ids" in records[0] and records[0]["child_ids"]:
                    assert "child_ids:" in result
                    assert "record(s)" in result
                    assert "odoo://res.partner/search?" in result

        finally:
            connection.disconnect()

    @pytest.mark.integration
    def test_format_various_field_types(self):
        """Test formatting various Odoo field types."""
        config = get_config()
        connection = _create_connection(config)

        try:
            connection.connect()
            try:
                connection.authenticate()
            except ConnectionError_ as e:
                if "429" in str(e) or "Too many requests" in str(e).lower():
                    pytest.skip("Rate limited by server")
                raise

            # Get a product record (has various field types and is usually enabled)
            try:
                product_ids = connection.search("product.product", [], limit=1)
                model = "product.product"
            except ConnectionError_ as e:
                if "429" in str(e) or "Too many requests" in str(e):
                    pytest.skip("Rate limited by server")
                # Fallback to res.partner which we know is enabled
                try:
                    product_ids = connection.search("res.partner", [], limit=1)
                    model = "res.partner"
                except ConnectionError_ as e:
                    if "429" in str(e) or "Too many requests" in str(e):
                        pytest.skip("Rate limited by server")
                    raise

            if product_ids:
                # Get fields metadata
                fields_meta = connection.fields_get(model)

                # Read the record with limited fields to avoid marshaling issues
                # Select fields that are likely to exist in both product and partner models
                basic_fields = ["name", "active", "create_date", "write_date"]
                if model == "res.partner":
                    basic_fields.extend(["email", "phone", "is_company", "country_id"])
                else:  # product.product
                    basic_fields.extend(["list_price", "standard_price", "type", "categ_id"])

                records = connection.read(model, product_ids, basic_fields)

                # Format the record
                formatter = RecordFormatter(model)
                result = formatter.format_record(records[0], fields_meta)

                # Check basic structure
                assert f"Record: {model}/{product_ids[0]}" in result
                assert "Fields:" in result or "Relationships:" in result

                # Check for different field types based on what's in the record
                record = records[0]

                # Check for boolean fields
                bool_fields = [
                    k for k, v in fields_meta.items() if v.get("type") == "boolean" and k in record
                ]
                if bool_fields:
                    field = bool_fields[0]
                    if record[field]:
                        assert f"{field}: Yes" in result
                    else:
                        assert f"{field}: No" in result

                # Check for many2one fields
                m2o_fields = [
                    k
                    for k, v in fields_meta.items()
                    if v.get("type") == "many2one" and k in record and record[k]
                ]
                if m2o_fields:
                    field = m2o_fields[0]
                    assert f"{field}:" in result
                    assert "odoo://" in result

                # Check for date/datetime fields (excluding create_date which is omitted)
                date_fields = [
                    k
                    for k, v in fields_meta.items()
                    if v.get("type") in ("date", "datetime")
                    and k in record
                    and record[k]
                    and k not in RecordFormatter.OMIT_FIELDS
                ]
                if date_fields:
                    field = date_fields[0]
                    assert f"{field}:" in result

        finally:
            connection.disconnect()
