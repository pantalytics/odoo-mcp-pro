"""Argument checks and normalisation for method_execute.

Kept apart from methods.py so the tool's orchestration stays readable.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..error_handling import ValidationError


def refuse_empty_recordset(
    model: str,
    method: str,
    ids: Optional[List[int]],
    kwargs: Optional[Dict[str, Any]],
    positional_args: Optional[List[Any]],
) -> None:
    """Refuse a call that would run the method on no records.

    Odoo answers a method on an empty recordset without complaint: read
    returns [] and write returns true and changes nothing (ticket #237).
    """
    if positional_args is not None:
        raise ValidationError(
            f"method_execute takes no positional args. Pass record ids as "
            f"ids=[...] and the method's arguments by name in kwargs, e.g. "
            f"method_execute(model='{model}', method='{method}', ids=[42], "
            f"kwargs={{...}}). Nothing was called."
        )
    if kwargs and "ids" in kwargs:
        raise ValidationError(
            "Pass record ids in the ids parameter, not inside kwargs: "
            f"method_execute(model='{model}', method='{method}', ids=[...]). "
            "Nothing was called."
        )
    if ids is not None and not ids:
        raise ValidationError(
            f"ids is empty, so {model}.{method} would run on no records and "
            f"change nothing. Pass the record ids, or omit ids for a "
            f"model-level method. Nothing was called."
        )


def _normalize_kwargs(method: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Make a passthrough call behave like the dedicated tool would.

    Only `message_post` needs this. Odoo escapes a plain `str` body (it expects
    `markupsafe.Markup`, which cannot cross XML-RPC or JSON/2), so a body of
    "<p>Hi</p>" reaches the chatter -- and the customer's inbox -- as literal
    "&lt;p&gt;Hi&lt;/p&gt;". `message_post` sets `body_is_html`; a caller
    reaching for `message_post` here means the same thing by `body`, so set it
    too unless they said otherwise.
    """
    if method != "message_post" or "body" not in kwargs:
        return kwargs
    return {"body_is_html": True, **kwargs}
