"""XML-RPC faults, said the way Odoo says them."""

import xmlrpc.client

from ..error_sanitizer import ErrorSanitizer
from ..exceptions import OdooExecutionError

# Odoo's /xmlrpc/2 fault codes (addons/rpc/controllers/xmlrpc.py). Code 2 is
# UserError and its subclasses (ValidationError, MissingError), which this
# transport does not tell apart; code 1 carries the traceback, whose last line
# names the exception.
_FAULT_CODE_TYPES = {2: "UserError", 3: "AccessDenied", 4: "AccessError"}


def _fault_as_execution_error(fault: xmlrpc.client.Fault) -> OdooExecutionError:
    """Say an XML-RPC fault the way Odoo does: ``<ExceptionClass>: <message>``."""
    error_type = _FAULT_CODE_TYPES.get(fault.faultCode)
    message = ErrorSanitizer.sanitize_xmlrpc_fault(fault.faultString)
    if error_type is None:
        # Code 1: the sanitizer reduced the traceback to "TypeError: ..."
        head, sep, _ = message.partition(":")
        if sep and head.isidentifier():
            error_type = head
        return OdooExecutionError(message, error_type=error_type)
    return OdooExecutionError(f"{error_type}: {message}", error_type=error_type)
