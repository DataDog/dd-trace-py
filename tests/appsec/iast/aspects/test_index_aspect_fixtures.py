# -*- encoding: utf-8 -*-
import sys

import pytest

from ddtrace.appsec._iast._utils import _is_python_version_supported as python_supported_by_iast
from tests.appsec.iast.aspects.conftest import _iast_patched_module


if python_supported_by_iast():
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
    from ddtrace.appsec._iast._taint_tracking import taint_pyobject

    mod = _iast_patched_module("tests.appsec.iast.fixtures.aspects.str_methods")


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_string_index_error_index_error():
    with pytest.raises(IndexError) as excinfo:
        mod.do_index("abc", 22)  # pylint: disable=no-member
    assert "string index out of range" in str(excinfo.value)


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_string_index_error_type_error():
    with pytest.raises(TypeError) as excinfo:
        mod.do_index("abc", "22")  # pylint: disable=no-member
    assert "string indices must be integers" in str(excinfo.value)


@pytest.mark.skipif(
    not python_supported_by_iast() or sys.version_info < (3, 9, 0), reason="Python version not supported by IAST"
)
@pytest.mark.parametrize(
    "input_str, index_pos, expected_result, tainted",
    [
        ("abcde", 0, "a", True),
        ("abcde", 1, "b", True),
        ("abc", 2, "c", True),
        (b"abcde", 0, 97, False),
        (b"abcde", 1, 98, False),
        (b"abc", 2, 99, False),
        (bytearray(b"abcde"), 0, 97, False),
        (bytearray(b"abcde"), 1, 98, False),
        (bytearray(b"abc"), 2, 99, False),
    ],
)
def test_string_index(input_str, index_pos, expected_result, tainted):
    string_input = taint_pyobject(
        pyobject=input_str,
        source_name="test_add_aspect_tainting_left_hand",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )
    assert get_tainted_ranges(string_input)
    result = mod.do_index(string_input, index_pos)

    assert result == expected_result
    tainted_ranges = get_tainted_ranges(result)
    if not tainted:
        assert len(tainted_ranges) == 0
    else:
        assert len(tainted_ranges) == 1
        assert tainted_ranges[0].start == 0
        assert tainted_ranges[0].length == 1
