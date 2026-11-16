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


async def write_company_context(
    connection: Any, model: str, ids: List[int], values: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """The company an update of company-dependent values runs in.

    Only a write that sets a company-dependent field (cost price, property
    accounts) needs one: the records' own company. Shared records have no
    company of their own and keep the user's current company, as in Odoo.
    """
    from .tools._common import run_blocking

    fields = await run_blocking(
        connection, connection.fields_get, model, ["type", "company_dependent"]
    )
    if "company_id" not in fields or not any(
        fields.get(name, {}).get("company_dependent") for name in values
    ):
        return None
    rows = await run_blocking(connection, connection.read, model, ids, ["company_id"])
    companies = {row["company_id"][0] if row.get("company_id") else None for row in rows}
    if len(companies) > 1:
        raise ValidationError(
            "These records belong to different companies, and a company-specific "
            "value (such as the cost price) is set per company. Update them one "
            "company at a time."
        )
    return _only(companies.pop() if companies else None)
