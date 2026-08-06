import sys

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject_with_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
from ddtrace.appsec._iast.secure_marks.base import add_secure_mark
from tests.appsec.iast.iast_utils import _iast_patched_module
from tests.appsec.iast.iast_utils import patch_iast


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
    assert get_tainted_ranges(result.scheme) == [TaintRange(0, 4, Source("first_element", text, OriginType.PARAMETER))]
    assert get_tainted_ranges(result.netloc) == [TaintRange(0, 14, Source("first_element", text, OriginType.PARAMETER))]


@pytest.mark.parametrize(
    "url,safe,expected",
    [
        ("http://localhost:8080/redirect target", ":/%#?=@[]!$&'()*+,;", "http://localhost:8080/redirect%20target"),
        ("/redirect target", "", "%2Fredirect%20target"),
        (b"/\xff", b"\xff", "%2F%FF"),
    ],
)
def test_urllib_quote_propagates_secure_marks(iast_context_defaults, url, safe, expected):
    urllib_parse = _iast_patched_module("urllib.parse")
    patch_iast()
    tainted_url = taint_pyobject(
        pyobject=url,
        source_name="url",
        source_value=url,
        source_origin=OriginType.PARAMETER,
    )
    add_secure_mark(tainted_url, [VulnerabilityType.UNVALIDATED_REDIRECT])

    result = urllib_parse.quote(tainted_url, safe=safe)

    assert result == expected
    ranges = get_tainted_ranges(result)
    assert len(ranges) == 1
    assert ranges[0].start == 0
    assert ranges[0].length == len(result)
    assert ranges[0].has_secure_mark(VulnerabilityType.UNVALIDATED_REDIRECT)


@pytest.mark.parametrize(
    "value,safe,range_specs,expected,expected_positions",
    [
        pytest.param(
            b"/target value",
            b"",
            ((1, 6, "url", VulnerabilityType.UNVALIDATED_REDIRECT),),
            "%2Ftarget%20value",
            ((3, 6),),
            id="shifted-range",
        ),
        pytest.param(
            b"ab c",
            b"",
            ((1, 2, "query", VulnerabilityType.UNVALIDATED_REDIRECT),),
            "ab%20c",
            ((1, 4),),
            id="quoted-byte-inside-range",
        ),
        pytest.param(
            b"/a b?c",
            b"/?",
            ((0, 5, "path", VulnerabilityType.UNVALIDATED_REDIRECT),),
            "/a%20b?c",
            ((0, 7),),
            id="safe-and-quoted-bytes",
        ),
        pytest.param(
            b"/a b/c d",
            b"/",
            (
                (1, 3, "first", VulnerabilityType.UNVALIDATED_REDIRECT),
                (5, 3, "second", VulnerabilityType.SSRF),
            ),
            "/a%20b/c%20d",
            ((1, 5), (7, 5)),
            id="multiple-ranges",
        ),
    ],
)
def test_urllib_quote_adjusts_partial_taint_ranges(
    iast_context_defaults, value, safe, range_specs, expected, expected_positions
):
    urllib_parse = _iast_patched_module("urllib.parse")
    patch_iast()
    input_ranges = [
        TaintRange(start, length, Source(source_name, value, OriginType.PARAMETER))
        for start, length, source_name, _ in range_specs
    ]
    for taint_range, (_, _, _, secure_mark) in zip(input_ranges, range_specs):
        taint_range.add_secure_mark(secure_mark)
    taint_pyobject_with_ranges(value, tuple(input_ranges))

    result = urllib_parse.quote(value, safe=safe)

    assert result == expected
    ranges = get_tainted_ranges(result)
    assert [(taint_range.start, taint_range.length) for taint_range in ranges] == list(expected_positions)
    assert [taint_range.source for taint_range in ranges] == [taint_range.source for taint_range in input_ranges]
    assert [taint_range.secure_marks for taint_range in ranges] == [
        taint_range.secure_marks for taint_range in input_ranges
    ]
