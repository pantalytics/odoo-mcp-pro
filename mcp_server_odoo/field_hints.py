"""Turn Odoo's "Invalid field" errors into something the AI can act on.

Odoo answers a bad field name with the name it rejected and nothing else:
``Invalid field 'x_partner_ref' on model 'res.partner'``. The caller learns
that its guess was wrong but not what the alternatives are, so the next guess
is no better informed than the first. In production this is the single largest
failure class on the server, and ``read`` accounts for most of it.

This module reads the rejected name out of the message and matches it against
the model's real fields, so the error can carry a suggestion back. The work is
pure string handling; fetching the field list is the caller's job, because only
the caller knows whether it can afford the round trip (in practice it is cached).
"""

import difflib
import re
from typing import Callable, Iterable, List, Optional

# How many alternatives to offer. Enough to cover a near miss and a plausible
# second reading, few enough that the AI does not start shopping.
MAX_SUGGESTIONS = 3

# Below this ratio the "suggestion" is noise. difflib scores a shared prefix
# generously, so 'x' would otherwise pull in every field starting with x.
MIN_RATIO = 0.6

# Odoo phrases this differently per version and per code path. Seen in prod:
#   Invalid field 'x_foo' on model 'res.partner'
#   Invalid field 'x_foo' on 'res.partner'
#   Invalid field 'x_foo' in leaf ('x_foo', '=', 1)
#   Unknown field 'x_foo' in domain
#   Invalid field partner_id.x_foo in condition ('partner_id.x_foo', '=', 1)
# The name is quoted in most but not all of them, hence the optional quote.
_INVALID_FIELD_RE = re.compile(
    r"\b(?:invalid|unknown)\s+field\s+['\"]?([a-zA-Z_][A-Za-z0-9_.]*)['\"]?",
    re.IGNORECASE,
)
# The other shape: "Field 'x_foo' does not exist" / "Field x_foo does not exist".
_MISSING_FIELD_RE = re.compile(
    r"\bfield\s+['\"]?([a-zA-Z_][A-Za-z0-9_.]*)['\"]?\s+does\s+not\s+exist",
    re.IGNORECASE,
)


def extract_invalid_field(message: str) -> Optional[str]:
    """Return the field name Odoo rejected, or None if this is another error.

    A dotted path (``partner_id.x_foo``) comes back whole; splitting it is
    ``suggest_fields``' problem, since only the first segment lives on the
    model we have the field list for.
    """
    if not message:
        return None
    for pattern in (_INVALID_FIELD_RE, _MISSING_FIELD_RE):
        match = pattern.search(message)
        if match:
            return match.group(1)
    return None


def suggest_fields(field: str, known_fields: Iterable[str]) -> List[str]:
    """Rank the model's real fields by closeness to the rejected name.

    Only the first segment of a dotted path is matched: in
    ``partner_id.x_foo`` the part that has to exist on THIS model is
    ``partner_id``, and a suggestion for ``x_foo`` would point at the wrong
    model entirely.
    """
    if not field:
        return []
    head = field.split(".", 1)[0]
    candidates = [f for f in known_fields if f]
    if not candidates:
        return []
    return difflib.get_close_matches(head, candidates, n=MAX_SUGGESTIONS, cutoff=MIN_RATIO)


def field_hint(message: str, model: str, known_fields: Iterable[str]) -> Optional[str]:
    """Build the sentence to append to an invalid-field error, or None.

    None means "leave the message alone": either it is not a field error, or we
    have nothing useful to add and a vaguer error is worse than a short one.
    """
    field = extract_invalid_field(message)
    if field is None:
        return None
    fields = sorted(f for f in known_fields if f)
    if not fields:
        return None
    matches = suggest_fields(field, fields)
    if matches:
        return f"Did you mean: {', '.join(matches)}? Full list: odoo://{model}/fields"
    return f"{model} has {len(fields)} fields, none close to '{field}'. Read odoo://{model}/fields for the list."


def with_field_hint(
    message: str,
    model: str,
    method: str,
    fields_getter: Callable[[str], Iterable[str]],
) -> str:
    """Append the hint to an error message, or hand it back untouched.

    ``fields_getter`` is the connection's own ``fields_get``, passed in rather
    than imported so this module stays free of transport concerns and both the
    XML-RPC and JSON/2 layers can share one implementation. It is called only
    once the message is known to be a field error, so the round trip is paid
    on the failure path only, and in practice it is served from the field cache.

    Best effort throughout: anything that goes wrong here returns the original
    message, because a diagnostic must never replace the error it describes.
    """
    if method == "fields_get":
        return message  # would recurse into the call that just failed
    if extract_invalid_field(message) is None:
        return message
    try:
        known = fields_getter(model)
        hint = field_hint(message, model, known)
    except Exception:
        return message
    return f"{message} {hint}" if hint else message
