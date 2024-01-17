import pytest


OriginType = pytest.importorskip("ddtrace.appsec._iast._taint_tracking.OriginType")
get_tainted_ranges = pytest.importorskip("ddtrace.appsec._iast._taint_tracking.get_tainted_ranges")
taint_pyobject = pytest.importorskip("ddtrace.appsec._iast._taint_tracking.taint_pyobject")
_iast_patched_module = pytest.importorskip("tests.appsec.iast.aspects.conftest._iast_patched_module")

mod = _iast_patched_module("tests.appsec.iast.fixtures.aspects.str_methods")


def test_string_assigment():
    string_input = taint_pyobject(
        pyobject="foo",
        source_name="test_add_aspect_tainting_left_hand",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )
    assert get_tainted_ranges(string_input)

    res = mod.do_string_assignment(string_input)
    assert len(get_tainted_ranges(res)) == 1


def test_multiple_string_assigment():
    string_input = taint_pyobject(
        pyobject="foo",
        source_name="test_add_aspect_tainting_left_hand",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )
    assert get_tainted_ranges(string_input)

    results = mod.do_multiple_string_assigment(string_input)
    for res in results:
        assert len(get_tainted_ranges(res)) == 1


def test_multiple_tuple_string_assigment():
    string_input = taint_pyobject(
        pyobject="foo",
        source_name="test_add_aspect_tainting_left_hand",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )
    assert get_tainted_ranges(string_input)

    results = mod.do_tuple_string_assignment(string_input)
    assert len(get_tainted_ranges(results[-1])) == 1
