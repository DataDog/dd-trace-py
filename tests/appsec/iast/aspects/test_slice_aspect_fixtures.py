# -*- encoding: utf-8 -*-
import logging
import sys

import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import create_context
from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking import reset_context
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
from tests.appsec.iast.aspects.conftest import _iast_patched_module
from tests.utils import override_env


mod = _iast_patched_module("tests.appsec.iast.fixtures.aspects.str_methods")


@pytest.mark.skipif(sys.version_info < (3, 9, 0), reason="Python version not supported by IAST")
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
        ("abcde", 0, -1, 1, "abcd", True),
        ("abcde", 1, -2, 1, "bc", True),
        ("abcdef", 2, 3, 1, "c", True),
        ("abcde", 0, -2, 1, "abc", True),
        ("abcde", 0, -3, 1, "ab", True),
        ("abcde", 1, -3, 1, "b", True),
        ("abcdefg", 1, -3, 2, "bd", True),
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
            source_name="input_str",
            source_value="foo",
            source_origin=OriginType.PARAMETER,
        )
        result = mod.do_slice_2(tainted_input, start_pos, end_pos, step)  # pylint: disable=no-member
        assert result == expected_result
        tainted_ranges = get_tainted_ranges(result)
        assert len(tainted_ranges) == 1
        assert tainted_ranges[0].start == 0
        assert tainted_ranges[0].length == len(expected_result)


@pytest.mark.skipif(sys.version_info < (3, 9, 0), reason="Python version not supported by IAST")
@pytest.mark.parametrize(
    "input_str_not_tainted",
    [
        ("abcde"),
        ("fghij"),
        ("áéíóú"),
        ("😆😁😄😃😀"),
    ],
)
@pytest.mark.parametrize(
    "input_str_tainted, start_pos, end_pos, step, expected_result",
    [
        ("abcde", 0, 1, 1, "a"),
        ("abcde", 1, 2, 1, "b"),
        ("abc", 2, 3, 1, "c"),
        ("abcde", 0, 2, 1, "ab"),
        ("abcde", 0, 3, 1, "abc"),
        ("abcde", 1, 3, 1, "bc"),
        ("abcde", 1, 4, 1, "bcd"),
        ("abcde", 0, 4, 2, "ac"),
        ("abcde", 0, 4, 3, "ad"),
        ("abcde", 1, 5, 2, "bd"),
        ("abcde", 1, 5, 3, "be"),
        ("abcde", 0, 1, 1, "a"),
        ("abcde", 1, 2, 1, "b"),
    ],
)
def test_string_slice_2_and_two_strings(
    input_str_not_tainted, input_str_tainted, start_pos, end_pos, step, expected_result
):
    result = mod.do_slice_2_and_two_strings(
        input_str_not_tainted, input_str_tainted, start_pos + 5, end_pos + 5, step
    )  # pylint: disable=no-member
    assert result == expected_result

    tainted_input = taint_pyobject(
        pyobject=input_str_tainted,
        source_name="input_str_tainted",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )
    result = mod.do_slice_2_and_two_strings(
        input_str_not_tainted, tainted_input, start_pos + 5, end_pos + 5, step
    )  # pylint: disable=no-member
    assert result == expected_result
    tainted_ranges = get_tainted_ranges(result)
    assert len(tainted_ranges) == 1
    assert tainted_ranges[0].start == 0
    assert tainted_ranges[0].length == len(expected_result)


@pytest.mark.skipif(sys.version_info < (3, 9, 0), reason="Python version not supported by IAST")
@pytest.mark.parametrize(
    "input_str_tainted1",
    [
        ("abcde"),
        ("fghij"),
        ("áéíóú"),
        ("😆😁😄😃😀"),
    ],
)
@pytest.mark.parametrize(
    "input_str_tainted2, start_pos, end_pos, step, expected_result",
    [
        ("abcde", 0, 1, 1, "a"),
        ("abcde", 1, 2, 1, "b"),
        ("abc", 2, 3, 1, "c"),
        ("abcde", 0, 2, 1, "ab"),
        ("abcde", 0, 3, 1, "abc"),
        ("abcde", 1, 3, 1, "bc"),
        ("abcde", 1, 4, 1, "bcd"),
        ("abcde", 0, 4, 2, "ac"),
        ("abcde", 0, 4, 3, "ad"),
        ("abcde", 1, 5, 2, "bd"),
        ("abcde", 1, 5, 3, "be"),
        ("abcde", 0, 1, 1, "a"),
        ("abcde", 1, 2, 1, "b"),
    ],
)
def test_string_slice_2_and_two_strings_two_tainted(
    input_str_tainted1, input_str_tainted2, start_pos, end_pos, step, expected_result
):
    result = mod.do_slice_2_and_two_strings(
        input_str_tainted1, input_str_tainted2, start_pos + 5, end_pos + 5, step
    )  # pylint: disable=no-member
    assert result == expected_result

    tainted_input1 = taint_pyobject(
        pyobject=input_str_tainted1,
        source_name="input_str_tainted1",
        source_value=input_str_tainted1,
        source_origin=OriginType.PARAMETER,
    )

    tainted_input2 = taint_pyobject(
        pyobject=input_str_tainted2,
        source_name="input_str_tainted2",
        source_value=input_str_tainted2,
        source_origin=OriginType.PARAMETER,
    )

    result = mod.do_slice_2_and_two_strings(
        tainted_input1, tainted_input2, start_pos + 5, end_pos + 5, step
    )  # pylint: disable=no-member
    assert result == expected_result
    tainted_ranges = get_tainted_ranges(result)
    assert len(tainted_ranges) == 1
    assert tainted_ranges[0].start == 0
    assert tainted_ranges[0].length == len(expected_result)


@pytest.mark.skipif(sys.version_info < (3, 9, 0), reason="Python version not supported by IAST")
@pytest.mark.parametrize(
    "input_str_tainted1, input_str_tainted2, start_pos, end_pos, step, expected_result",
    [
        ("fghij", "abcde", 4, 8, 1, "jabc"),
    ],
)
def test_string_slice_2_and_two_strings_two_tainted_overlap_tained(
    input_str_tainted1, input_str_tainted2, start_pos, end_pos, step, expected_result
):
    result = mod.do_slice_2_and_two_strings(
        input_str_tainted1, input_str_tainted2, start_pos, end_pos, step
    )  # pylint: disable=no-member
    assert result == expected_result

    tainted_input1 = taint_pyobject(
        pyobject=input_str_tainted1,
        source_name="input_str_tainted1",
        source_value=input_str_tainted1,
        source_origin=OriginType.PARAMETER,
    )

    tainted_input2 = taint_pyobject(
        pyobject=input_str_tainted2,
        source_name="input_str_tainted2",
        source_value=input_str_tainted2,
        source_origin=OriginType.PARAMETER,
    )

    result = mod.do_slice_2_and_two_strings(
        tainted_input1, tainted_input2, start_pos, end_pos, step
    )  # pylint: disable=no-member
    assert result == expected_result
    tainted_ranges = get_tainted_ranges(result)
    assert len(tainted_ranges) == 2
    assert tainted_ranges[0].start == 0
    assert tainted_ranges[0].length == 1
    assert tainted_ranges[1].start == 1
    assert tainted_ranges[1].length == 3


@pytest.mark.skipif(sys.version_info < (3, 9, 0), reason="Python version not supported by IAST")
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
def test_string_slice_with_none_params(input_str, start_pos, end_pos, step, expected_result, tainted):
    result = mod.do_slice(input_str, start_pos, end_pos, step)  # pylint: disable=no-member
    assert result == expected_result

    tainted_input = taint_pyobject(
        pyobject=input_str,
        source_name="input_str",
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


def test_string_slice_and_unpack():
    """This test verify that site-packages/Crypto/Util/number.py:bytes_to_long is correctly instrumented with no
    errors.
    """
    for _ in range(10):
        input_str = b"\xcch\x08A\x93;\xa9:\xa9*\n\xeaA]\x13\xec"
        expected_result = 271702677468084275015420463885508744172
        result = mod.do_slice_complex(input_str)
        assert result == expected_result

        tainted_input = taint_pyobject(
            pyobject=input_str,
            source_name="input_str",
            source_value="foo",
            source_origin=OriginType.PARAMETER,
        )
        result = mod.do_slice_complex(tainted_input)
        assert result == expected_result


def test_string_slice_negative():
    """This test verify that site-packages/Crypto/Util/number.py:bytes_to_long is correctly instrumented with no
    errors.
    """
    input_str = b"\xc1\xd6/q\x85\n\xd40\xb6\x93\xbd* {\xb3\xaa"
    expected_result = b"\xc1\xd6/q\x85\n\xd40\xb6\x93\xbd* {\xb3\xaa"
    result = mod.do_slice_negative(input_str)
    assert result == expected_result

    tainted_input = taint_pyobject(
        pyobject=input_str,
        source_name="input_str",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )
    result = mod.do_slice_negative(tainted_input)
    assert result == expected_result


@pytest.mark.skip_iast_check_logs
def test_propagate_ranges_with_no_context(caplog):
    create_context()
    tainted_input = taint_pyobject(
        pyobject="abcde",
        source_name="input_str",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )

    reset_context()
    with override_env({IAST.ENV_DEBUG: "true"}), caplog.at_level(logging.DEBUG):
        result = mod.do_slice(tainted_input, 0, 3, None)
        assert result == "abc"
    log_messages = [record.message for record in caplog.get_records("call")]
    assert not any("[IAST] " in message for message in log_messages), log_messages
