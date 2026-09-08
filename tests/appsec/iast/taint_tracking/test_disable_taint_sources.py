"""Tests for suppressing IAST taint source generation.

The context manager is used by the AppSec machinery to avoid creating throwaway tainted objects while
analysing request data that never reaches the customer's business logic. It works by setting a context-local
source-suppression flag while preserving the active IAST request context id, so it must:
- suppress taint_pyobject while active and restore behaviour afterwards,
- preserve the current context id,
- leave taint propagation (aspects) untouched,
- be harmless when there is no active IAST request context.
"""

import contextvars

import pytest

from ddtrace.appsec._asm_request_context import iast_disabled_taint_sources
from ddtrace.appsec._iast import _iast_request_context_base
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import get_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted
from ddtrace.appsec._shared import _iast_context
from ddtrace.appsec._shared._iast_context import _get_iast_context_id
from ddtrace.appsec._shared._iast_context import iast_suppress_context


@pytest.fixture(params=[iast_suppress_context, iast_disabled_taint_sources])
def suppress_taint_sources(request):
    return request.param


def _taint(value):
    return taint_pyobject(
        pyobject=value,
        source_name="test",
        source_value=value,
        source_origin=OriginType.PARAMETER,
    )


def test_taint_sources_suppressed_inside_context(iast_context_defaults, suppress_taint_sources):
    # Sanity: tainting works normally.
    assert is_pyobject_tainted(_taint("normal_value"))

    with suppress_taint_sources():
        assert not is_pyobject_tainted(_taint("suppressed_value"))

    # Behaviour is restored after the block.
    assert is_pyobject_tainted(_taint("restored_value"))


def test_context_id_is_preserved(iast_context_defaults, suppress_taint_sources):
    context_id = _get_iast_context_id()
    assert context_id is not None

    with suppress_taint_sources():
        assert _get_iast_context_id() == context_id

    assert _get_iast_context_id() == context_id


def test_nested_taint_source_suppression(iast_context_defaults, suppress_taint_sources):
    with suppress_taint_sources():
        assert not is_pyobject_tainted(_taint("outer_suppressed_value"))

        with suppress_taint_sources():
            assert not is_pyobject_tainted(_taint("inner_suppressed_value"))

        assert not is_pyobject_tainted(_taint("outer_still_suppressed_value"))

    assert is_pyobject_tainted(_taint("restored_after_nested_suppression"))


def test_propagation_is_not_suppressed(iast_context_defaults, suppress_taint_sources):
    from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

    tainted = _taint("tainted")
    assert is_pyobject_tainted(tainted)

    # Source generation is disabled, but propagation through aspects must still work on already-tainted input.
    with suppress_taint_sources():
        result = add_aspect(tainted, "_suffix")
        assert is_pyobject_tainted(result)


def test_taint_sources_restored_after_exception(iast_context_defaults, suppress_taint_sources):
    context_id = _get_iast_context_id()
    with pytest.raises(ValueError, match="suppressed"):
        with suppress_taint_sources():
            assert not is_pyobject_tainted(_taint("suppressed_before_exception"))
            raise ValueError("suppressed")

    assert _get_iast_context_id() == context_id
    assert is_pyobject_tainted(_taint("restored_after_exception"))


def test_taint_source_suppression_without_request(suppress_taint_sources):
    def without_request():
        assert _get_iast_context_id() is None
        assert _iast_context._is_iast_taint_source_enabled()
        with suppress_taint_sources():
            assert _get_iast_context_id() is None
            assert not _iast_context._is_iast_taint_source_enabled()
            assert not is_pyobject_tainted(_taint("suppressed_without_request"))
        assert _iast_context._is_iast_taint_source_enabled()
        assert _get_iast_context_id() is None

    contextvars.Context().run(without_request)


def test_taint_source_suppression_is_context_local(suppress_taint_sources):
    other_context = contextvars.copy_context()
    with suppress_taint_sources():
        assert not _iast_context._is_iast_taint_source_enabled()
        assert other_context.run(_iast_context._is_iast_taint_source_enabled)
        assert not _iast_context._is_iast_taint_source_enabled()
    assert _iast_context._is_iast_taint_source_enabled()


def test_is_iast_request_enabled_re_export_is_shared():
    # _iast_request_context_base re-exports this one symbol for the taint sinks and handlers;
    # they must see the same object the owning module defines.
    assert _iast_request_context_base.is_iast_request_enabled is _iast_context.is_iast_request_enabled


def test_get_ranges_uses_shared_request_context(iast_context_defaults):
    tainted = _taint("shared_context_value")
    context_id = _iast_context._get_iast_context_id()
    assert context_id is not None
    ranges = get_ranges(tainted, context_id)
    assert ranges
    assert get_ranges(tainted) == ranges
    assert contextvars.Context().run(get_ranges, tainted) == []
    assert contextvars.Context().run(get_ranges, tainted, context_id) == ranges
