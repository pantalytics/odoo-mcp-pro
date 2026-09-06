"""Tests for the invalid-field hint.

The message shapes below are the ones seen in production, recovered from the
failure corpus: Odoo phrases the same rejection at least five different ways
depending on version and code path, so the parser is tested against all of them
rather than against one idealised form.
"""

from unittest.mock import MagicMock

import pytest

from mcp_server_odoo.field_hints import (
    extract_invalid_field,
    field_hint,
    suggest_fields,
    with_field_hint,
)

PARTNER_FIELDS = ["id", "name", "ref", "parent_id", "partner_id", "email", "country_id"]


class TestExtractInvalidField:
    @pytest.mark.parametrize(
        "message,expected",
        [
            ("Invalid field 'x_foo' on model 'res.partner'", "x_foo"),
            ("Invalid field 'x_foo' on 'res.partner'", "x_foo"),
            ("Invalid field 'x_foo' in leaf ('x_foo', '=', 1)", "x_foo"),
            ("Unknown field 'x_foo' in domain", "x_foo"),
            ("Invalid field x_foo in request", "x_foo"),
            ("Field 'x_foo' does not exist", "x_foo"),
            ("Invalid field partner_id.x_foo in condition", "partner_id.x_foo"),
        ],
    )
    def test_recognises_odoo_phrasings(self, message, expected):
        assert extract_invalid_field(message) == expected

    @pytest.mark.parametrize(
        "message",
        [
            "",
            "Access denied: You are not allowed to access res.partner records",
            "Operation failed: Request-sent",
            "Record ID 42 not found",
        ],
    )
    def test_leaves_other_errors_alone(self, message):
        assert extract_invalid_field(message) is None


class TestSuggestFields:
    def test_near_miss_is_suggested(self):
        assert "ref" in suggest_fields("refs", PARTNER_FIELDS)

    def test_dotted_path_matches_on_the_first_segment(self):
        """In partnr_id.x_foo only the head has to exist on THIS model.

        Matching the tail would suggest fields of a different model entirely,
        so the tail must not influence the result at all.
        """
        assert suggest_fields("partnr_id.x_foo", PARTNER_FIELDS)[0] == "partner_id"
        assert suggest_fields("partnr_id.x_foo", PARTNER_FIELDS) == suggest_fields(
            "partnr_id.something_else", PARTNER_FIELDS
        )

    def test_nothing_close_returns_nothing(self):
        assert suggest_fields("zzzzzzzz", PARTNER_FIELDS) == []

    def test_no_known_fields_returns_nothing(self):
        assert suggest_fields("ref", []) == []


class TestFieldHint:
    def test_hint_names_the_alternatives(self):
        hint = field_hint(
            "Invalid field 'refs' on model 'res.partner'", "res.partner", PARTNER_FIELDS
        )
        assert "ref" in hint
        assert "odoo://res.partner/fields" in hint

    def test_hint_without_a_match_still_points_at_the_list(self):
        hint = field_hint("Invalid field 'zzzzzzzz' on res.partner", "res.partner", PARTNER_FIELDS)
        assert "odoo://res.partner/fields" in hint
        assert str(len(PARTNER_FIELDS)) in hint

    def test_not_a_field_error_gets_no_hint(self):
        assert field_hint("Operation failed: Request-sent", "res.partner", PARTNER_FIELDS) is None


class TestWithFieldHint:
    def _getter(self, calls=None):
        def getter(model):
            if calls is not None:
                calls.append(model)
            return PARTNER_FIELDS

        return getter

    def test_enriches_a_field_error(self):
        result = with_field_hint(
            "Invalid field 'refs' on model 'res.partner'", "res.partner", "read", self._getter()
        )
        assert result.startswith("Invalid field 'refs'")
        assert "Did you mean" in result

    def test_fields_get_is_not_enriched(self):
        """Guard against recursing into the very call that just failed."""
        calls = []
        message = "Invalid field 'refs' on model 'res.partner'"

        result = with_field_hint(message, "res.partner", "fields_get", self._getter(calls))

        assert result == message
        assert calls == []

    def test_other_errors_never_pay_for_a_lookup(self):
        calls = []
        message = "Operation failed: Request-sent"

        result = with_field_hint(message, "res.partner", "search_count", self._getter(calls))

        assert result == message
        assert calls == []

    def test_a_broken_lookup_returns_the_original_error(self):
        """A diagnostic must never replace the error it describes."""
        message = "Invalid field 'refs' on model 'res.partner'"

        def boom(model):
            raise RuntimeError("Odoo unreachable")

        assert with_field_hint(message, "res.partner", "read", boom) == message


class TestTransportIntegration:
    def test_xmlrpc_fault_carries_the_hint(self):
        import xmlrpc.client

        from mcp_server_odoo.exceptions import OdooConnectionError
        from mcp_server_odoo.odoo_connection.orm import OdooConnectionOrmMixin

        class _Conn(OdooConnectionOrmMixin):
            def __init__(self):
                self._authenticated = True
                self._connected = True
                self._auth_method = "api_key"
                self._database = "db"
                self._uid = 1
                self.config = MagicMock(api_key="k")
                self.object_proxy = MagicMock()
                self.object_proxy.execute_kw.side_effect = xmlrpc.client.Fault(
                    1, "Invalid field 'refs' on model 'res.partner'"
                )

            def fields_get(self, model, attributes=None):
                return {f: {} for f in PARTNER_FIELDS}

        with pytest.raises(OdooConnectionError) as exc:
            _Conn().execute_kw("res.partner", "read", [[1]], {})

        assert "Did you mean" in str(exc.value)
        assert "ref" in str(exc.value)
