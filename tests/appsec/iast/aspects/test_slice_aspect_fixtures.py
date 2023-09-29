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


@pytest.mark.skipif(
    not python_supported_by_iast() or sys.version_info < (3, 9, 0), reason="Python version not supported by IAST"
)
@pytest.mark.parametrize(
    "input_str, start_pos, end_pos, step, expected_result, tainted",
    [
        ("abcde", 0, 1, 1, "a", True),
        ("abcde", "0", "1", 1, "a", False),
        ("abcde", 1, 2, 1, "b", True),
        ("abcde", "1", 2, 1, "b", False),
        ("abc", 2, 3, 1, "c", True),
        ("abc", 2, "3", 1, "c", False),
        ("abcde", 0, 2, 1, "ab", True),
        ("abcde", 0, 2, "1", "ab", False),
        ("abcde", 0, 3, 1, "abc", True),
        ("abcde", "0", "3", 1, "abc", False),
        ("abcde", 1, 3, 1, "bc", True),
        ("abcde", 1, 4, 1, "bcd", True),
        ("abcde", 1, "4", "1", "bcd", False),
        ("abcde", 0, 4, 2, "ac", True),
        ("abcde", 0, 4, 3, "ad", True),
        ("abcde", 1, 5, 2, "bd", True),
        ("abcde", 1, 5, 3, "be", True),
        ("abcde", 0, 1, 1, "a", True),
        ("abcde", 1, 2, 1, "b", True),
        (b"abc", 2, 3, 1, b"c", True),
        (b"abcde", 0, 2, 1, b"ab", True),
        (b"abcde", 0, 3, 1, b"abc", True),
        (b"abcde", 1, 3, 1, b"bc", True),
        (b"abcde", 1, 4, 1, b"bcd", True),
        (b"abcde", 0, 4, 2, b"ac", True),
        (b"abcde", 0, 4, 3, b"ad", True),
        (b"abcde", 1, 5, 2, b"bd", True),
        (b"abcde", 1, 5, 3, b"be", True),
    ],
)
def test_string_slice_2(input_str, start_pos, end_pos, step, expected_result, tainted):
    if not tainted:
        with pytest.raises(TypeError) as excinfo:
            mod.do_slice_2(input_str, start_pos, end_pos, step)  # pylint: disable=no-member
        assert "slice indices must be integers or None or have an __index__ method" in str(excinfo.value)
    else:
        result = mod.do_slice_2(input_str, start_pos, end_pos, step)  # pylint: disable=no-member
        assert result == expected_result

        tainted_input = taint_pyobject(
            pyobject=input_str,
            source_name="test_add_aspect_tainting_left_hand",
            source_value="foo",
            source_origin=OriginType.PARAMETER,
        )
        result = mod.do_slice_2(tainted_input, start_pos, end_pos, step)  # pylint: disable=no-member
        assert result == expected_result
        tainted_ranges = get_tainted_ranges(result)
        assert len(tainted_ranges) == 1
        assert tainted_ranges[0].start == 0
        assert tainted_ranges[0].length == len(expected_result)


@pytest.mark.skipif(
    not python_supported_by_iast() or sys.version_info < (3, 9, 0), reason="Python version not supported by IAST"
)
@pytest.mark.parametrize(
    "input_str, start_pos, end_pos, step, expected_result, tainted",
    [
        ("abcde", None, None, None, "abcde", True),
        ("abcde", None, None, 2, "ace", True),
        ("abcde", None, 2, None, "ab", True),
        ("abcde", None, 4, 2, "ac", True),
        ("abcde", 1, None, None, "bcde", True),
        ("abcde", 1, None, 1, "bcde", True),
        ("abcde", 1, 2, None, "b", True),
        ("abcde", 1, 4, 2, "bd", True),
    ],
)
def test_string_slice(input_str, start_pos, end_pos, step, expected_result, tainted):
    result = mod.do_slice(input_str, start_pos, end_pos, step)  # pylint: disable=no-member
    assert result == expected_result

    tainted_input = taint_pyobject(
        pyobject=input_str,
        source_name="test_add_aspect_tainting_left_hand",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )
    result = mod.do_slice(tainted_input, start_pos, end_pos, step)  # pylint: disable=no-member
    assert result == expected_result
    tainted_ranges = get_tainted_ranges(result)
    if not tainted:
        assert len(tainted_ranges) == 0
    else:
        assert len(tainted_ranges) == 1
        assert tainted_ranges[0].start == 0
        assert tainted_ranges[0].length == len(expected_result)
