"""Name every unknown field at once when Odoo refuses a read or a domain.

Odoo stops at the first bad name ("Invalid field 'product_uom' on model
'sale.order.line'"), so a call with three guessed names took three failed
calls to fix, and a client without resources (ChatGPT) had no way to see the
real names. After Odoo refuses, this checks every name the call used against
`fields_get` (cached on the connection) and says which ones the model does not
have, with the closest real names. It only runs on the failure path; a call
that works costs nothing extra.
"""

from __future__ import annotations

import difflib
from typing import Any, Iterable, List, Optional

from ..error_handling import ValidationError
from ..exceptions import OdooExecutionError
from ._common import run_blocking

# Always readable, whether or not fields_get lists them.
_MAGIC_FIELDS = {"id", "display_name", "__last_update"}


def domain_field_names(domain: Any) -> List[str]:
    """The field each domain leaf filters on (first segment of a dotted path)."""
    names: List[str] = []
    if not isinstance(domain, (list, tuple)):
        return names
    for leaf in domain:
        if isinstance(leaf, (list, tuple)) and len(leaf) == 3 and isinstance(leaf[0], str):
            names.append(leaf[0].split(".", 1)[0])
    return names


def unknown_fields_error(
    connection: Any, model: str, exc: Exception, names: Iterable[str]
) -> Optional[ValidationError]:
    """A ValidationError naming the fields `model` does not have, or None.

    None when `exc` is not Odoo refusing the call, when every name exists (the
    refusal was about something else, so Odoo's own message stands), or when
    the field list cannot be read.
    """
    if not isinstance(exc, OdooExecutionError) or connection is None:
        return None
    wanted = [n for n in dict.fromkeys(names) if n and n not in _MAGIC_FIELDS]
    if not wanted:
        return None
    try:
        known = connection.fields_get(model)
    except Exception:
        return None
    if not isinstance(known, dict) or not known:
        return None
    unknown = [n for n in wanted if n not in known]
    if not unknown:
        return None
    parts = []
    for name in unknown:
        close = difflib.get_close_matches(name, list(known), n=3, cutoff=0.6)
        parts.append(f"{name} (did you mean {' or '.join(close)}?)" if close else name)
    return ValidationError(
        f"{model} has no field {', '.join(parts)}. Odoo said: {exc}. "
        f"To see the real field names, call method_execute with model='{model}', "
        "method='fields_get', kwargs={'attributes': ['string', 'type']}."
    )


async def unknown_fields_hint(
    connection: Any, model: str, exc: Exception, names: List[str]
) -> Optional[ValidationError]:
    """`unknown_fields_error` off the event loop, under the connection lock."""
    if connection is None or not names or not isinstance(exc, OdooExecutionError):
        return None
    return await run_blocking(connection, unknown_fields_error, connection, model, exc, names)
