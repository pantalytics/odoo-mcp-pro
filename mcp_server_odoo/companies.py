"""Which company a create or update runs in, on a multi-company Odoo.

A call without `allowed_company_ids` in its context runs with every company
the user may access switched on, and the user's default company as the
current one. A create that names another `company_id` then stored its
company-dependent values (cost price) on the default company and took
defaults from all companies at once (a product got the sale taxes of every
company), and an update of a company-dependent value on another company's
record changed the default company's value. Measured on Odoo 18, 19 and 20.
These helpers make the record's own company the only one switched on for the
call, which is what the company switcher does in Odoo's own UI.

Records are visible across companies without any of this. Their
company-dependent values are not: those are read as the default company.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .error_handling import ValidationError


def _named_company(values: Dict[str, Any]) -> Optional[int]:
    company_id = values.get("company_id")
    if not company_id:
        # Missing, False, None or 0: no company, which Odoo reads as shared or
        # as the model's own default.
        return None
    if isinstance(company_id, bool) or not isinstance(company_id, int):
        raise ValidationError(
            f"company_id must be a company's id, a number, not {company_id!r}. "
            "server_info_get lists the companies."
        )
    return company_id


def _only(company_id: Optional[int]) -> Optional[Dict[str, Any]]:
    return {"allowed_company_ids": [company_id]} if company_id else None


def company_context(values: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The context that makes the company named in `values` the current one."""
    return _only(_named_company(values))


def bulk_company_context(vals_list: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The same for a bulk create, which runs in one company or is refused.

    Rows that name different companies, or some a company and some none, would
    each need their own current company; one call cannot give them that.
    """
    named = {_named_company(values) for values in vals_list}
    if len(named) > 1:
        raise ValidationError(
            "These records are for more than one company (or some name a "
            "company_id and some do not). Send one records_create call per company."
        )
    return _only(named.pop() if named else None)


def _sets_a_company_value(fields: Dict[str, Any], values: Dict[str, Any]) -> bool:
    """Whether the write may change a company-specific value.

    Directly (a company-dependent field) or through a computed field's inverse
    (product.template.standard_price writes the variant's company-dependent
    cost in the current company).
    """
    for name in values:
        info = fields.get(name, {})
        if info.get("company_dependent") or (
            info.get("store") is False and not info.get("readonly")
        ):
            return True
    return False


async def write_company_context(
    connection: Any, model: str, ids: List[int], values: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """The company an update runs in: the records' own company.

    Records of one company are updated with that company switched on, as Odoo
    does when you edit a record of that company. Shared records keep the
    user's current company. Records of several companies are updated as they
    are, unless the write may change a company-specific value (cost price):
    that is set per company, so it is refused.
    """
    from .tools._common import run_blocking

    # The full field list: cached by both clients, unlike a narrowed one.
    fields = await run_blocking(connection, connection.fields_get, model)
    if "company_id" not in fields:
        return None
    rows = await run_blocking(connection, connection.read, model, ids, ["company_id"])
    companies = {row["company_id"][0] if row.get("company_id") else None for row in rows}
    named = companies - {None}
    if len(companies) > 1:
        if _sets_a_company_value(fields, values):
            raise ValidationError(
                "These records belong to different companies, and a company-specific "
                "value (such as the cost price) is set per company. Update them one "
                "company at a time."
            )
        return None
    return _only(named.pop() if named else None)
