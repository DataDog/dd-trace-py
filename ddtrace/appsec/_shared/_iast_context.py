"""Context-local IAST state, independent of request lifecycle and reporting."""

import contextlib
import contextvars
from typing import Iterator
from typing import Optional


# AIDEV-NOTE: This module must stay outside the _iast package and must never
# import from ddtrace (stdlib only). Importing any _iast submodule executes
# ddtrace/appsec/_iast/__init__.py, which reaches _asm_request_context through
# _listener -> _iast_request_context -> reporter -> _exploit_prevention.stack_traces
# and also loads the native taint-tracking extension. Moving these primitives
# under _iast would therefore re-create the import cycle this module broke, and
# would make ASM initialize IAST just to suppress taint sources.
IAST_CONTEXT: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar("iast_var", default=None)

# Keep source suppression separate from IAST_CONTEXT. Clearing the
# request context id disables request-scoped taint queries and propagation and
# can send no-context queries through unsafe native fallback paths.
_IAST_TAINT_SOURCES_SUPPRESSED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "iast_taint_sources_suppressed", default=False
)


@contextlib.contextmanager
def iast_suppress_context() -> Iterator[None]:
    """Temporarily disable IAST taint source generation for the current context."""
    token = _IAST_TAINT_SOURCES_SUPPRESSED.set(True)
    try:
        yield
    finally:
        _IAST_TAINT_SOURCES_SUPPRESSED.reset(token)


def _is_iast_taint_source_enabled() -> bool:
    return not _IAST_TAINT_SOURCES_SUPPRESSED.get()


def _get_iast_context_id() -> Optional[int]:
    """Retrieve the current IAST context identifier from the ContextVar."""
    return IAST_CONTEXT.get()


def is_iast_request_enabled() -> bool:
    """Check whether IAST is currently operating within an active request context."""
    return _get_iast_context_id() is not None
