"""Tests for ``iast_suppress_context``: suppressing IAST taint *source* generation.

The context manager is used by the AppSec machinery to avoid creating throwaway tainted objects while
analysing request data that never reaches the customer's business logic. It works by clearing the active
IAST request context id (which gates source generation) and restoring it on exit, so it must:
- suppress ``taint_pyobject`` while active and restore behaviour afterwards,
- restore the previous context id (not hard-reset),
- leave taint *propagation* (aspects) untouched,
- be harmless when there is no active IAST request context.
"""

from ddtrace.appsec._iast._iast_request_context_base import _get_iast_context_id
from ddtrace.appsec._iast._iast_request_context_base import iast_suppress_context
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted


def _taint(value):
    return taint_pyobject(
        pyobject=value,
        source_name="test",
        source_value=value,
        source_origin=OriginType.PARAMETER,
    )


def test_taint_sources_suppressed_inside_context(iast_context_defaults):
    # Sanity: tainting works normally.
    assert is_pyobject_tainted(_taint("normal_value"))

    with iast_suppress_context():
        assert not is_pyobject_tainted(_taint("suppressed_value"))

    # Behaviour is restored after the block.
    assert is_pyobject_tainted(_taint("restored_value"))


def test_context_id_is_saved_and_restored(iast_context_defaults):
    context_id = _get_iast_context_id()
    assert context_id is not None

    with iast_suppress_context():
        # The active context id is cleared while suppressed.
        assert _get_iast_context_id() is None

    # The original context id is restored on exit.
    assert _get_iast_context_id() == context_id


def test_propagation_is_not_suppressed(iast_context_defaults):
    from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

    tainted = _taint("tainted")
    assert is_pyobject_tainted(tainted)

    # Source generation is disabled, but propagation through aspects must still work on already-tainted input.
    with iast_suppress_context():
        result = add_aspect(tainted, "_suffix")
        assert is_pyobject_tainted(result)
