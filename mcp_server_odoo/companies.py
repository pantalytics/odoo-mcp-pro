"""Which company a create runs in, on a multi-company Odoo.

A call without `allowed_company_ids` in its context runs with every company
the user may access switched on, and the user's default company as the
current one. A create that names another `company_id` then stores its
company-dependent values (cost price, accounts) on the default company and
takes defaults from all companies at once (a product got the sale taxes of
every company). Measured on Odoo 18, 19 and 20. When the values name a
company, that company becomes the only one switched on for the call, which is
what the company switcher does in Odoo's own UI.

Reads need nothing: without `allowed_company_ids` Odoo lets a call see the
records of every company the user may access.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .error_handling import ValidationError


def _named_company(values: Dict[str, Any]) -> Optional[int]:
    company_id = values.get("company_id")
    # bool is an int in Python; False means "no company", not company 0.
    if isinstance(company_id, int) and not isinstance(company_id, bool):
        return company_id
    return None


def company_context(values: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The context that makes the company named in `values` the current one."""
    company_id = _named_company(values)
    if company_id is None:
        return None
    return {"allowed_company_ids": [company_id]}


def bulk_company_context(vals_list: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The same for a bulk create, which runs in one company or is refused.

    Rows that name different companies, or some a company and some none, would
    each need their own current company; one call cannot give them that.
    """
    named = {_named_company(values) for values in vals_list}
    if named == {None}:
        return None
    if len(named) > 1:
        raise ValidationError(
            "These records are for more than one company (or some name a "
            "company_id and some do not). Send one records_create call per company."
        )
    return {"allowed_company_ids": [named.pop()]}
