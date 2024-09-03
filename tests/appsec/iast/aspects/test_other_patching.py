from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
from tests.appsec.iast.aspects.conftest import _iast_patched_module


mod = _iast_patched_module("benchmarks.bm.iast_fixtures.str_methods")


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


def test_preprocess_lexer_input():
    """This test check a propagation error in pygments.lexer package
    https://github.com/pygments/pygments/blob/2b915b92b81899a79a559d4ea0003b2454d636f4/pygments/lexer.py#L206
    """
    text = "print('Hello, world!')"
    string_input = taint_pyobject(
        pyobject=text, source_name="first_element", source_value=text, source_origin=OriginType.PARAMETER
    )
    result = mod._preprocess_lexer_input(string_input)
    assert result == "print('Hello, world!')\n"
    assert get_tainted_ranges(result) == [
        TaintRange(0, 22, Source("first_element", "print('Hello, world!')", OriginType.PARAMETER))
    ]
