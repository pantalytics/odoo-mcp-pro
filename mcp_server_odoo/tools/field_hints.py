"""Name every unknown field at once when Odoo refuses a read or a domain.

Odoo stops at the first bad name ("Invalid field 'product_uom' on model
'sale.order.line'"), so a call with three guessed names took three failed
calls to fix, and a client without resources (ChatGPT) had no way to see the
real names. After Odoo refuses, this checks every name the call used against
`fields_get` (cached on the connection) and says which ones the model does not
have, with the closest real names.

An explicit `fields` list on a read is checked before the call as well, so a
wrong name (often one from an older Odoo version) never reaches Odoo: no HTTP
500 for the client and no ERROR traceback in the Odoo log. The check uses the
cached `fields_get`, and if that cannot be read the call goes to Odoo as usual.
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


def _unknown_names(connection: Any, model: str, names: Iterable[str]) -> List[str]:
    """The names in `names` that `model` does not have, in call order.

    Empty when every name exists or when the field list cannot be read, so a
    failure here never blocks a call that Odoo might accept.
    """
    if connection is None:
        return []
    wanted = [n for n in dict.fromkeys(names) if n and n not in _MAGIC_FIELDS]
    if not wanted:
        return []
    try:
        known = connection.fields_get(model)
    except Exception:
        return []
    if not isinstance(known, dict) or not known:
        return []
    return [n for n in wanted if n not in known]


def _describe(connection: Any, model: str, unknown: List[str]) -> str:
    """`name (did you mean a or b?), other` for the unknown names."""
    known = list(connection.fields_get(model))
    parts = []
    for name in unknown:
        close = difflib.get_close_matches(name, known, n=3, cutoff=0.6)
        parts.append(f"{name} (did you mean {' or '.join(close)}?)" if close else name)
    return ", ".join(parts)


def unknown_fields_error(
    connection: Any, model: str, exc: Exception, names: Iterable[str]
) -> Optional[ValidationError]:
    """A ValidationError naming the fields `model` does not have, or None.

    None when `exc` is not Odoo refusing the call, when every name exists (the
    refusal was about something else, so Odoo's own message stands), or when
    the field list cannot be read.
    """
    if not isinstance(exc, OdooExecutionError):
        return None
    unknown = _unknown_names(connection, model, names)
    if not unknown:
        return None
    return ValidationError(
        f"{model} has no field {_describe(connection, model, unknown)}. Odoo said: {exc}. "
        f"To see the real field names, call method_execute with model='{model}', "
        "method='fields_get', kwargs={'attributes': ['string', 'type']}."
    )


def unknown_fields_precheck(
    connection: Any, model: str, names: Iterable[str]
) -> Optional[ValidationError]:
    """A ValidationError for a field list naming fields `model` does not have.

    Runs before the read, so Odoo never sees the bad name: no HTTP 500 for the
    client and no traceback at ERROR level in the Odoo log. Uses the cached
    `fields_get`; None when every name exists or the field list cannot be read
    (the call then goes to Odoo as before).
    """
    unknown = _unknown_names(connection, model, names)
    if not unknown:
        return None
    return ValidationError(
        f"{model} has no field {_describe(connection, model, unknown)}. "
        f"Nothing was read. To see the real field names, read odoo://{model}/fields "
        f"or call method_execute with model='{model}', "
        "method='fields_get', kwargs={'attributes': ['string', 'type']}."
    )


async def unknown_fields_hint(
    connection: Any, model: str, exc: Exception, names: List[str]
) -> Optional[ValidationError]:
    """`unknown_fields_error` off the event loop, under the connection lock."""
    if connection is None or not names or not isinstance(exc, OdooExecutionError):
        return None
    return await run_blocking(connection, unknown_fields_error, connection, model, exc, names)


async def check_requested_fields(connection: Any, model: str, fields: Any) -> None:
    """Raise a ValidationError when an explicit `fields` list has unknown names.

    `None` (smart defaults) and `["__all__"]` are not checked. Runs off the
    event loop under the connection lock, like the other field lookups.
    """
    if not isinstance(fields, list) or fields == ["__all__"]:
        return
    names = [n for n in fields if isinstance(n, str)]
    if not names:
        return
    error = await run_blocking(connection, unknown_fields_precheck, connection, model, names)
    if error is not None:
        raise error
