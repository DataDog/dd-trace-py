"""Switch that lets AAP stop IAST from creating taint sources, without either product importing the other."""

from collections.abc import Iterator
import contextlib
import contextvars


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
