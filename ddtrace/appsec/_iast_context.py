"""Context-local IAST state, independent of request lifecycle and reporting."""

import contextlib
import contextvars
from typing import Iterator
from typing import Optional


# AIDEV-NOTE: Keep this outside the _iast package so AppSec can suppress taint
# sources without initializing IAST or loading its native extensions.
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
