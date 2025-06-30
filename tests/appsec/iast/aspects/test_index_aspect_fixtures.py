import logging
import sys

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._context import create_context
from ddtrace.appsec._iast._taint_tracking._context import reset_context
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
from tests.appsec.iast.iast_utils import _iast_patched_module
from tests.utils import override_global_config


mod = _iast_patched_module("benchmarks.bm.iast_fixtures.str_methods")


def test_string_index_error_index_error():
    with pytest.raises(IndexError) as excinfo:
        mod.do_index("abc", 22)  # pylint: disable=no-member
    assert "string index out of range" in str(excinfo.value)


def test_string_index_error_type_error():
    with pytest.raises(TypeError) as excinfo:
        mod.do_index("abc", "22")  # pylint: disable=no-member
    assert "string indices must be integers" in str(excinfo.value)


def test_string_error_key_error():
    with pytest.raises(KeyError) as excinfo:
        mod.do_index_on_dict({1: 1, 2: 2}, 3)
    assert "3" in str(excinfo.value)


@pytest.mark.skipif(sys.version_info < (3, 9, 0), reason="Python version not supported by IAST")
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


def test_dictionary_index():
    string_input = taint_pyobject(
        pyobject="foobar",
        source_name="test_dictionary_index",
        source_value="foobar",
        source_origin=OriginType.PARAMETER,
    )
    assert get_tainted_ranges(string_input)
    d = {"a": string_input, "b": 2, "c": 3}
    result = mod.do_index_on_dict(d, "a")
    assert result == "foobar"
    tainted_ranges = get_tainted_ranges(result)
    assert len(tainted_ranges) == 1


@pytest.mark.skipif(sys.version_info < (3, 9, 0), reason="Python version not supported by IAST")
def test_index_error_with_tainted_gives_one_log_metric(telemetry_writer):
    string_input = taint_pyobject(
        pyobject="abcde",
        source_name="test_add_aspect_tainting_left_hand",
        source_value="abcde",
        source_origin=OriginType.PARAMETER,
    )
    with pytest.raises(IndexError):
        mod.do_index(string_input, 100)

    list_metrics_logs = list(telemetry_writer._logs)
    assert len(list_metrics_logs) == 0


@pytest.mark.skip_iast_check_logs
@pytest.mark.skipif(sys.version_info < (3, 9, 0), reason="Python version not supported by IAST")
def test_propagate_ranges_with_no_context(caplog):
    """Test taint_pyobject without context. This test is to ensure that the function does not raise an exception."""
    input_str = "abcde"
    create_context()
    string_input = taint_pyobject(
        pyobject=input_str,
        source_name="test_add_aspect_tainting_left_hand",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )
    assert get_tainted_ranges(string_input)

    reset_context()
    with override_global_config(dict(_iast_debug=True)), caplog.at_level(logging.DEBUG):
        result = mod.do_index(string_input, 3)
        assert result == "d"
    log_messages = [record.message for record in caplog.get_records("call")]
    assert not any("iast::" in message for message in log_messages), log_messages


@pytest.mark.skipif(sys.version_info < (3, 9, 0), reason="Python version not supported by IAST")
def test_re_match_index_indexerror():
    regexp = r"(?P<username>\w+)@(?P<domain>\w+)\.(?P<tld>\w+)"
    input_str = "user@example.com"
    string_input = taint_pyobject(
        pyobject=input_str,
        source_name="test_add_aspect_tainting_left_hand",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )
    with pytest.raises(IndexError):
        mod.do_re_match_index(string_input, regexp, 4)

    with pytest.raises(IndexError):
        mod.do_re_match_index(string_input, regexp, "doesntexist")


@pytest.mark.parametrize(
    "input_str, index, tainted, expected_result, ",
    [
        ("user@example.com", 0, True, "user@example.com"),
        ("user@example.com", 1, True, "user"),
        ("user@example.com", 2, True, "example"),
        ("user@example.com", 3, True, "com"),
        ("user@example.com", "username", True, "user"),
        ("user@example.com", "domain", True, "example"),
        ("user@example.com", "tld", True, "com"),
        ("cleanuser@example.com", 0, False, "cleanuser@example.com"),
        ("cleanuser@example.com", 1, False, "cleanuser"),
        ("cleanuser@example.com", 2, False, "example"),
        ("cleanuser@example.com", 3, False, "com"),
        ("cleanuser@example.com", "username", False, "cleanuser"),
        ("cleanuser@example.com", "domain", False, "example"),
        ("cleanuser@example.com", "tld", False, "com"),
    ],
)
@pytest.mark.skipif(sys.version_info < (3, 9, 0), reason="Python version not supported by IAST")
def test_re_match_index(input_str, index, tainted, expected_result):
    regexp = r"(?P<username>\w+)@(?P<domain>\w+)\.(?P<tld>\w+)"
    if tainted:
        string_input = taint_pyobject(
            pyobject=input_str,
            source_name="test_add_aspect_tainting_left_hand",
            source_value="foo",
            source_origin=OriginType.PARAMETER,
        )
    else:
        string_input = input_str
    result = mod.do_re_match_index(string_input, regexp, index)
    assert result == expected_result
    assert len(get_tainted_ranges(result)) == int(tainted)


@pytest.mark.skipif(sys.version_info < (3, 9, 0), reason="Python version not supported by IAST")
def test_re_match_index_indexerror_bytes():
    regexp = rb"(?P<username>\w+)@(?P<domain>\w+)\.(?P<tld>\w+)"
    input_str = b"user@example.com"
    string_input = taint_pyobject(
        pyobject=input_str,
        source_name="test_re_match_index_indexerror_bytes",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )
    with pytest.raises(IndexError):
        mod.do_re_match_index(string_input, regexp, 4)

    with pytest.raises(IndexError):
        mod.do_re_match_index(string_input, regexp, b"doesntexist")


@pytest.mark.parametrize(
    "input_str, index, tainted, expected_result, ",
    [
        (b"user@example.com", 0, True, b"user@example.com"),
        (b"user@example.com", 1, True, b"user"),
        (b"user@example.com", 2, True, b"example"),
        (b"user@example.com", 3, True, b"com"),
        (b"user@example.com", "username", True, b"user"),
        (b"user@example.com", "domain", True, b"example"),
        (b"user@example.com", "tld", True, b"com"),
        (b"cleanuser@example.com", 0, False, b"cleanuser@example.com"),
        (b"cleanuser@example.com", 1, False, b"cleanuser"),
        (b"cleanuser@example.com", 2, False, b"example"),
        (b"cleanuser@example.com", 3, False, b"com"),
        (b"cleanuser@example.com", "username", False, b"cleanuser"),
        (b"cleanuser@example.com", "domain", False, b"example"),
        (b"cleanuser@example.com", "tld", False, b"com"),
    ],
)
@pytest.mark.skipif(sys.version_info < (3, 9, 0), reason="Python version not supported by IAST")
def test_re_match_index_bytes(input_str, index, tainted, expected_result):
    regexp = rb"(?P<username>\w+)@(?P<domain>\w+)\.(?P<tld>\w+)"
    if tainted:
        string_input = taint_pyobject(
            pyobject=input_str,
            source_name="test_re_match_index_bytes",
            source_value="foo",
            source_origin=OriginType.PARAMETER,
        )
    else:
        string_input = input_str
    result = mod.do_re_match_index(string_input, regexp, index)
    assert result == expected_result
    assert len(get_tainted_ranges(result)) == int(tainted)
