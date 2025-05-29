import sys

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
from tests.appsec.iast.iast_utils import _iast_patched_module


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


def test_index_lower_add():
    text = "http://localhost:8000/api/articles?param1=value1"
    string_input = taint_pyobject(
        pyobject=text, source_name="first_element", source_value=text, source_origin=OriginType.PARAMETER
    )
    result_scheme, result_url = mod.index_lower_add(string_input)
    assert result_url == "//localhost:8000/api/articles?param1=value1"
    assert result_scheme == "http"

    assert get_tainted_ranges(result_scheme) == [TaintRange(0, 4, Source("first_element", text, OriginType.PARAMETER))]
    assert get_tainted_ranges(result_url) == [
        TaintRange(0, 43, Source("first_element", "print('Hello, world!')", OriginType.PARAMETER))
    ]


@pytest.mark.skipif(
    sys.version_info >= (3, 11),
    reason="Python 3.11 and 3.12 raise TypeError: don't know how todisassemble _lru_cache_wrapper objects",
)
def test_urlib_parse_patching():
    _iast_patched_module("urllib.parse")

    import dis
    import urllib.parse

    bytecode = dis.Bytecode(urllib.parse.urlsplit)
    assert "add_aspect" in bytecode.codeobj.co_names
    if sys.version_info > (3, 9):
        assert "replace_aspect" in bytecode.codeobj.co_names
    assert "slice_aspect" in bytecode.codeobj.co_names
    assert "lower_aspect" in bytecode.codeobj.co_names


def test_urlib_parse_propagation():
    _iast_patched_module("urllib.parse")
    mod = _iast_patched_module("benchmarks.bm.iast_fixtures.str_methods")

    text = "http://localhost:8000/api/articles?param1=value1"
    string_input = taint_pyobject(
        pyobject=text, source_name="first_element", source_value=text, source_origin=OriginType.PARAMETER
    )

    result = mod.urlib_urlsplit(string_input)
    assert result.path == "/api/articles"
    assert result.scheme == "http"
    assert result.netloc == "localhost:8000"

    assert get_tainted_ranges(result.path) == [TaintRange(0, 13, Source("first_element", text, OriginType.PARAMETER))]
    if sys.version_info > (3, 9):
        assert get_tainted_ranges(result.scheme) == [
            TaintRange(0, 4, Source("first_element", text, OriginType.PARAMETER))
        ]
    assert get_tainted_ranges(result.netloc) == [TaintRange(0, 14, Source("first_element", text, OriginType.PARAMETER))]
