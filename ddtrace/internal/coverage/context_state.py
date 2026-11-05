"""Shared coverage collection-context state.

The context stack lives here (rather than on ModuleCodeCollector) so that the
collector (code.py) and the import-only support module (import_only.py) can both
read and write it without a circular dependency: both modules import this one, and
neither imports the other's state.

The stack entries are ModuleCodeCollector.CollectInContext instances; they are
described structurally here so this module does not depend on the collector.
"""

from collections import defaultdict
from contextvars import ContextVar
import threading as _threading
import typing as t

from ddtrace.internal.compat import is_at_least_py
from ddtrace.internal.coverage.coverage_lines import CoverageLines


class _CollectorLike(t.Protocol):
    closed: bool
    is_import_coverage: bool
    _covered_lines: t.Any
    _covered_files: t.Any
    _import_names: t.Any


# NOTE: A mutable ContextVar default would be shared across threads until set() is called.
# Use an immutable tuple so contexts can share collectors without sharing stack mutations.
ctx_collectors: ContextVar[tuple[_CollectorLike, ...]] = ContextVar("ctx_collectors", default=())
ctx_is_import_coverage = ContextVar("ctx_is_import_coverage", default=False)
ctx_coverage_enabled = ContextVar("ctx_coverage_enabled", default=False)

# Coverage hooks can execute where the coverage ContextVars are not visible, for example when code runs in
# a fresh context. CollectInContext mirrors the context stack into a threading.local() for those cases. The TLS
# stack is only consulted when the executing context carries no coverage state of its own, so a context that
# carries its own still-open collector, for instance one copied while that collector was active, always takes
# precedence over the thread's latest state. A copied context whose collectors have all completed is expired
# and never borrows the thread's collector, which keeps late work from a finished scope out of the next one.
# The mirror is best effort: it reflects the stack of whichever context last entered or exited a collector on
# this thread, and Context.run does not restore thread-local state, so interleaved scopes across copied contexts
# can leave it approximate until the next enter or exit.
# TODO: resynchronize the mirror when execution returns from a copied context, see the xfail test
# test_returning_from_a_copied_context_restores_the_tls_fallback.
_tls_coverage = _threading.local()

_PY_GE_314 = is_at_least_py(3, 14)


def _get_active_collector(stack: tuple[_CollectorLike, ...]) -> t.Optional[_CollectorLike]:
    # Contexts copied during an import may still contain its completed collector.
    for collector in reversed(stack):
        if not collector.closed:
            return collector
    return None


def _get_ctx_collector(coverage_enabled: bool) -> t.Optional[_CollectorLike]:
    # Hooks pass their already-checked state to avoid reading the ContextVar twice.
    if coverage_enabled:
        if stack := ctx_collectors.get():
            collector = stack[-1]
            if not collector.closed:
                return collector
            if active_collector := _get_active_collector(stack):
                return active_collector
        # A context whose own collectors have all completed is expired, and borrowing the thread's
        # active collector would attribute late work to an unrelated scope.
        return None

    # The same lifetime rules apply when a context without any coverage state needs the TLS fallback.
    if _PY_GE_314:
        return _get_active_collector(getattr(_tls_coverage, "stack", ()))
    return None


def _get_ctx_covered_lines() -> "defaultdict[str, CoverageLines]":
    # Keep direct data lookups inline to avoid extra resolver calls.
    if ctx_coverage_enabled.get():
        if stack := ctx_collectors.get():
            collector = stack[-1]
            if not collector.closed:
                return t.cast("defaultdict[str, CoverageLines]", collector._covered_lines)
            for collector in reversed(stack):
                if not collector.closed:
                    return t.cast("defaultdict[str, CoverageLines]", collector._covered_lines)
        # An expired copied context drops late lines rather than borrowing the thread's collector.
        return defaultdict(CoverageLines)
    if _PY_GE_314:
        if active_collector := _get_active_collector(getattr(_tls_coverage, "stack", ())):
            return t.cast("defaultdict[str, CoverageLines]", active_collector._covered_lines)
    return defaultdict(CoverageLines)


def _get_ctx_covered_files() -> "set[str]":
    if ctx_coverage_enabled.get():
        if stack := ctx_collectors.get():
            collector = stack[-1]
            if not collector.closed:
                return t.cast("set[str]", collector._covered_files)
            for collector in reversed(stack):
                if not collector.closed:
                    return t.cast("set[str]", collector._covered_files)
        # An expired copied context drops late files rather than borrowing the thread's collector.
        return set()
    if _PY_GE_314:
        if active_collector := _get_active_collector(getattr(_tls_coverage, "stack", ())):
            return t.cast("set[str]", active_collector._covered_files)
    return set()
