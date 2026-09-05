# SPDX-License-Identifier: MPL-2.0
# SPDX-FileCopyrightText: 2025-2026 Pantalytics B.V.
"""Ask a wizard's question through MCP's own round trip (MRTR, 2026-07-28).

Since the 2026-07-28 protocol a tool can answer "I need input first": it returns
an ``InputRequiredResult`` carrying the question, the client shows the form,
and the client re-issues the *same* call with the answer in ``input_responses``.
No stream stays open and any replica can serve the retry, which is exactly
the two-step ``decision`` / ``followup`` flow in ``methods.py`` -- now spoken
in the protocol's own words, so the client renders a form instead of the model
reading a JSON description and calling again.

Older clients cannot receive an ``InputRequiredResult`` at all (the SDK
rejects it as an invalid ``tools/call`` result for them), and on our stateless
transport there is no back-channel to ask them mid-call. So the question is
only asked this way when ``client_can_answer``; everyone else keeps the
``followup`` dict. See docs/adr/0004-interactive-elements-sdk-v2-and-mcp-apps.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from mcp.server.elicitation import render_elicitation_schema
from mcp.server.mcpserver import Context
from mcp.types import ElicitRequest, ElicitRequestFormParams, ElicitResult, InputRequiredResult
from mcp_types.version import is_version_at_least

from .wizards import WizardHandler

INPUT_REQUIRED_VERSION = "2026-07-28"
"""First protocol revision whose tools/call may return an InputRequiredResult."""

DECISION_KEY = "decision"
"""Key of the one question we ask; the retry carries the answer under it."""


@dataclass(frozen=True)
class Answer:
    """What the client brought back on its retry."""

    action: str
    """``accept``, ``decline`` or ``cancel`` -- the elicitation's own vocabulary."""

    decision: Optional[Dict[str, Any]]
    """The filled-in form on ``accept``; ``None`` otherwise."""


def client_can_answer(ctx: Optional[Context]) -> bool:
    """True when the client can show a form mid-call and retry with the answer.

    Needs both halves: a protocol that carries ``InputRequiredResult`` and a
    client that advertised form elicitation. Anything less gets the
    ``followup`` dict, which every client can act on.
    """
    if ctx is None:
        return False
    version = ctx.protocol_version
    if not version or not is_version_at_least(version, INPUT_REQUIRED_VERSION):
        return False
    caps = ctx.client_capabilities
    return bool(caps is not None and caps.elicitation is not None and caps.elicitation.form)


def ask_decision(handler: WizardHandler) -> InputRequiredResult:
    """The wizard's question as a form the client renders.

    The schema is the same primitive-only pydantic model ``followup_descriptor``
    describes, rendered the way the elicitation spec wants it. No
    ``request_state``: the retry re-runs the Odoo method, which hands the same
    wizard back, so there is nothing to remember between the two calls.
    """
    return InputRequiredResult(
        input_requests={
            DECISION_KEY: ElicitRequest(
                params=ElicitRequestFormParams(
                    message=handler.prompt,
                    requested_schema=render_elicitation_schema(handler.schema),
                )
            )
        }
    )


def answered_decision(ctx: Optional[Context]) -> Optional[Answer]:
    """The answer the client carried on its retry, or ``None`` on a first call."""
    if ctx is None or not ctx.input_responses:
        return None
    response = ctx.input_responses.get(DECISION_KEY)
    if not isinstance(response, ElicitResult):
        return None
    if response.action == "accept":
        return Answer("accept", dict(response.content or {}))
    return Answer(response.action, None)
