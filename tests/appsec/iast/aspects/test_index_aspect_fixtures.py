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


mod = _iast_patched_module("benchmarks.bm.iast_fixtures.str_methods")


def test_string_index_error_index_error():
    with pytest.raises(IndexError) as excinfo:
        mod.do_index("abc", 22)  # pylint: disable=no-member
    assert "string index out of range" in str(excinfo.value)


def test_string_index_error_type_error():
    with pytest.raises(TypeError) as excinfo:
        mod.do_index("abc", "22")  # pylint: disable=no-member
    assert "string indices must be integers" in str(excinfo.value)


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
    with override_env({IAST.ENV_DEBUG: "true"}), caplog.at_level(logging.DEBUG):
        result = mod.do_index(string_input, 3)
        assert result == "d"
    log_messages = [record.message for record in caplog.get_records("call")]
    assert not any("[IAST] " in message for message in log_messages), log_messages
