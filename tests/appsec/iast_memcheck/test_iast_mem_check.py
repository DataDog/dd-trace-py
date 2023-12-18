import os

import pytest
from pytest_memray import LeaksFilterFunction
from pytest_memray import Stack

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._stacktrace import get_info_frame
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import active_map_addreses_size
from ddtrace.appsec._iast._taint_tracking import create_context
from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking import initializer_size
from ddtrace.appsec._iast._taint_tracking import num_objects_tainted
from ddtrace.appsec._iast._taint_tracking import reset_context
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
from ddtrace.internal import core
from tests.appsec.iast.aspects.conftest import _iast_patched_module
from tests.appsec.iast_memcheck._stacktrace_py import get_info_frame as get_info_frame_py
from tests.appsec.iast_memcheck.fixtures.stacktrace import func_1
from tests.utils import flaky


FIXTURES_PATH = "tests/appsec/iast/fixtures/propagation_path.py"

LOOPS = 5
CWD = os.path.abspath(os.getcwd())
ALLOW_LIST = ["iast_memcheck/test_iast_mem_check.py", "fixtures/stacktrace.py"]
DISALLOW_LIST = ["_iast/_ast/visitor", "_pytest/assertion/rewrite", "coverage/", "internal/ci_visibility/"]

mod = _iast_patched_module("tests.appsec.iast.fixtures.propagation_path")


class IASTFilter(LeaksFilterFunction):
    def __call__(self, stack: Stack) -> bool:
        for frame in stack.frames:
            for disallowed_element in DISALLOW_LIST:
                if disallowed_element in frame.filename:
                    return False

            for allowed_element in ALLOW_LIST:
                if allowed_element in frame.filename:
                    return True

        return False


@flaky(1704067200)
@pytest.mark.limit_leaks("8.2 KB", filter_fn=IASTFilter())
@pytest.mark.parametrize(
    "origin1, origin2",
    [
        ("taintsource1", "taintsource2"),
        ("taintsource", "taintsource"),
        (b"taintsource1", "taintsource2"),
        (b"taintsource1", b"taintsource2"),
        ("taintsource1", b"taintsource2"),
        (bytearray(b"taintsource1"), "taintsource2"),
        (bytearray(b"taintsource1"), bytearray(b"taintsource2")),
        ("taintsource1", bytearray(b"taintsource2")),
        (bytearray(b"taintsource1"), b"taintsource2"),
        (bytearray(b"taintsource1"), bytearray(b"taintsource2")),
        (b"taintsource1", bytearray(b"taintsource2")),
    ],
)
def test_propagation_memory_check(origin1, origin2, iast_span_defaults):
    """Biggest allocating functions:
    - join_aspect: ddtrace/appsec/_iast/_taint_tracking/aspects.py:124 -> 8.0KiB
    - _prepare_report: ddtrace/appsec/_iast/taint_sinks/_base.py:111 -> 8.0KiB
    - format_aspect: ddtrace/appsec/_iast/_taint_tracking/aspects.py:347 -> 3.0KiB
    - modulo_aspect: ddtrace/appsec/_iast/_taint_tracking/aspects.py:214 -> 1.6KiB
    """
    _num_objects_tainted = 0
    _active_map_addreses_size = 0
    _initializer_size = 0
    for _ in range(LOOPS):
        create_context()
        tainted_string_1 = taint_pyobject(
            origin1, source_name="path1", source_value=origin1, source_origin=OriginType.PATH
        )
        tainted_string_2 = taint_pyobject(
            origin2, source_name="path2", source_value=origin2, source_origin=OriginType.PARAMETER
        )
        result = mod.propagation_memory_check(tainted_string_1, tainted_string_2)

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
        assert len(span_report.sources) > 0
        assert len(span_report.vulnerabilities) > 0
        assert len(get_tainted_ranges(result)) == 6

        if _num_objects_tainted == 0:
            _num_objects_tainted = num_objects_tainted()
            assert _num_objects_tainted > 0
        if _active_map_addreses_size == 0:
            _active_map_addreses_size = active_map_addreses_size()
            assert _active_map_addreses_size > 0
        if _initializer_size == 0:
            _initializer_size = initializer_size()
            assert _initializer_size > 0

        assert _num_objects_tainted == num_objects_tainted()
        assert _active_map_addreses_size == active_map_addreses_size()
        assert _initializer_size == initializer_size()
        reset_context()


@flaky(1704067200)
@pytest.mark.limit_leaks("460 B", filter_fn=IASTFilter())
def test_stacktrace_memory_check():
    for _ in range(LOOPS):
        frame_info = func_1("", "2", "3")
        if not frame_info:
            pytest.fail("No stacktrace")

        file_name, line_number = frame_info
        assert file_name
        assert line_number > 0


@flaky(1704067200)
@pytest.mark.limit_leaks("460 B", filter_fn=IASTFilter())
def test_stacktrace_memory_check_direct_call():
    for _ in range(LOOPS):
        frame_info = get_info_frame(CWD)
        if not frame_info:
            pytest.fail("No stacktrace")

        file_name, line_number = frame_info
        assert file_name
        assert line_number > 0


@flaky(1704067200)
@pytest.mark.limit_leaks("460 KB", filter_fn=IASTFilter())
def test_stacktrace_memory_check_no_native():
    for _ in range(LOOPS):
        frame_info = func_1("", "py", "3")
        if not frame_info:
            pytest.fail("No stacktrace")

        file_name, line_number = frame_info
        assert file_name
        assert line_number > 0


@flaky(1704067200)
@pytest.mark.limit_leaks("24 KB", filter_fn=IASTFilter())
def test_stacktrace_memory_check_no_native_direct_call():
    for _ in range(2):
        frame_info = get_info_frame_py(CWD)
        if not frame_info:
            pytest.fail("No stacktrace")

        file_name, line_number = frame_info
        assert file_name
        assert line_number > 0


@flaky(1704067200)
@pytest.mark.limit_leaks("440 B", filter_fn=IASTFilter())
def test_stacktrace_memory_empty_byte_check():
    for _ in range(LOOPS):
        frame_info = func_1("empty_byte", "2", "3")
        if not frame_info:
            pytest.fail("No stacktrace")

        file_name, line_number = frame_info
        assert file_name
        assert line_number > 0


@flaky(1704067200)
@pytest.mark.limit_leaks("440 B", filter_fn=IASTFilter())
def test_stacktrace_memory_empty_string_check():
    for _ in range(LOOPS):
        frame_info = func_1("empty_string", "2", "3")
        if not frame_info:
            pytest.fail("No stacktrace")

        file_name, line_number = frame_info
        assert file_name
        assert line_number > 0


@flaky(1704067200)
@pytest.mark.limit_leaks("10 KB", filter_fn=IASTFilter())
def test_stacktrace_memory_random_string_check():
    """2.1 KB is enough but CI allocates 1.0 MB bytes"""
    for _ in range(LOOPS):
        frame_info = func_1("random_string", "2", "3")
        if not frame_info:
            pytest.fail("No stacktrace")

        file_name, line_number = frame_info
        assert file_name == ""
        assert line_number == 0
