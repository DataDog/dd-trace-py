"""Context-local IAST state, independent of request lifecycle and reporting."""

import contextvars
from typing import Optional


# AIDEV-NOTE: This module must stay outside the _iast package and must never
# import from ddtrace (stdlib only). Importing any _iast submodule executes
# ddtrace/appsec/_iast/__init__.py, which reaches _asm_request_context through
# _listener -> _iast_request_context -> reporter -> _exploit_prevention.stack_traces
# and also loads the native taint-tracking extension. Moving these primitives
# under _iast would therefore re-create the import cycle this module broke, and
# would make ASM initialize IAST just to suppress taint sources.
# Taint source suppression, which AAP drives, lives in
# ddtrace/internal/security/_taint_sources.py so that AAP does not import this module.
IAST_CONTEXT: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar("iast_var", default=None)


def _get_iast_context_id() -> Optional[int]:
    """Retrieve the current IAST context identifier from the ContextVar."""
    return IAST_CONTEXT.get()


def is_iast_request_enabled() -> bool:
    """Check whether IAST is currently operating within an active request context."""
    return _get_iast_context_id() is not None
