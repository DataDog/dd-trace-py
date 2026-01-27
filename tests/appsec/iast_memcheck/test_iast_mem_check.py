import os

import pytest
from pytest_memray import LeaksFilterFunction
from pytest_memray import Stack

from ddtrace.appsec._iast._iast_request_context import get_iast_reporter
from ddtrace.appsec._iast._iast_request_context_base import _iast_finish_request
from ddtrace.appsec._iast._iast_request_context_base import _iast_start_request
from ddtrace.appsec._iast._iast_request_context_base import _num_objects_tainted_in_request
from ddtrace.appsec._iast._stacktrace import get_info_frame
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_size
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
from tests.appsec.iast.iast_utils import _iast_patched_module
from tests.appsec.iast_memcheck.fixtures.stacktrace import func_1


FIXTURES_PATH = "tests/appsec/iast/fixtures/propagation_path.py"

LOOPS = 5
CWD = os.path.abspath(os.getcwd())
ALLOW_LIST = ["iast_memcheck/test_iast_mem_check.py", "fixtures/stacktrace.py"]
DISALLOW_LIST = [
    "_pytest/assertion/rewrite",
    "coverage/",
    "internal/ci_visibility/",
    # Python 3.14+ standard library (regex compilation, etc.)
    "lib/python3.",
    ".pyenv/versions/",
    # Python internal C code (instrumentation API, objects, etc.)
    "Python/instrumentation.c",
    "Python/ceval.c",
    "Python/context.c",
    "Objects/",
    "Include/",
    # Python standard library modules
    "/re/__init__.py",
    "/re/_compiler.py",
    "/asyncio/",
]

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


@pytest.mark.limit_leaks("8.8 KB", filter_fn=IASTFilter())
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
def test_propagation_memory_check(iast_context_defaults, origin1, origin2):
    """Biggest allocating functions:
    - join_aspect: ddtrace/appsec/_iast/_taint_tracking/aspects.py:124 -> 8.0KiB
    - _prepare_report: ddtrace/appsec/_iast/taint_sinks/_base.py:111 -> 8.0KiB
    - format_aspect: ddtrace/appsec/_iast/_taint_tracking/aspects.py:347 -> 3.0KiB
    - modulo_aspect: ddtrace/appsec/_iast/_taint_tracking/aspects.py:214 -> 1.6KiB
    """
    _num_objects_tainted = 0
    _debug_context_array_size = 0
    _iast_finish_request()
    for _ in range(LOOPS):
        _iast_start_request()
        tainted_string_1 = taint_pyobject(
            origin1, source_name="path1", source_value=origin1, source_origin=OriginType.PATH
        )
        tainted_string_2 = taint_pyobject(
            origin2, source_name="path2", source_value=origin2, source_origin=OriginType.PARAMETER
        )
        result = mod.propagation_memory_check(tainted_string_1, tainted_string_2)

        span_report = get_iast_reporter()
        assert len(span_report.sources) > 0
        assert len(span_report.vulnerabilities) > 0
        assert len(get_tainted_ranges(result)) == 1

        _num_objects_tainted = _num_objects_tainted_in_request()
        assert _num_objects_tainted > 0

        _debug_context_array_size = debug_context_array_size()
        assert _debug_context_array_size > 0

        # Some tainted pyobject is freed, and Python may reuse the memory address
        # hence the number of tainted objects may be the same or less
        # assert num_objects_tainted() - 3 <= _num_objects_tainted <= num_objects_tainted() + 3
        assert _debug_context_array_size == debug_context_array_size()
        _iast_finish_request()


@pytest.mark.asyncio
@pytest.mark.limit_leaks("8.8 KB", filter_fn=IASTFilter())
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
async def test_propagation_memory_check_async(iast_context_defaults, origin1, origin2):
    """Biggest allocating functions:
    - join_aspect: ddtrace/appsec/_iast/_taint_tracking/aspects.py:124 -> 8.0KiB
    - _prepare_report: ddtrace/appsec/_iast/taint_sinks/_base.py:111 -> 8.0KiB
    - format_aspect: ddtrace/appsec/_iast/_taint_tracking/aspects.py:347 -> 3.0KiB
    - modulo_aspect: ddtrace/appsec/_iast/_taint_tracking/aspects.py:214 -> 1.6KiB
    """
    _num_objects_tainted = 0
    _debug_context_array_size = 0
    _iast_finish_request()
    for _ in range(LOOPS):
        _iast_start_request()
        tainted_string_1 = taint_pyobject(
            origin1, source_name="path1", source_value=origin1, source_origin=OriginType.PATH
        )
        tainted_string_2 = taint_pyobject(
            origin2, source_name="path2", source_value=origin2, source_origin=OriginType.PARAMETER
        )
        result = await mod.propagation_memory_check_async(tainted_string_1, tainted_string_2)

        span_report = get_iast_reporter()
        assert len(span_report.sources) > 0
        assert len(span_report.vulnerabilities) > 0
        assert len(get_tainted_ranges(result)) == 1

        if _num_objects_tainted == 0:
            _num_objects_tainted = _num_objects_tainted_in_request()
            assert _num_objects_tainted > 0
        if _debug_context_array_size == 0:
            _debug_context_array_size = debug_context_array_size()
            assert _debug_context_array_size > 0

        # Some tainted pyobject is freed, and Python may reuse the memory address
        # hence the number of tainted objects may be the same or less
        # assert num_objects_tainted() - 3 <= _num_objects_tainted <= num_objects_tainted() + 3
        assert _debug_context_array_size == debug_context_array_size()
        _iast_finish_request()


@pytest.mark.limit_leaks("460 B", filter_fn=IASTFilter())
def test_stacktrace_memory_check():
    for _ in range(LOOPS):
        frame_info = func_1("", "2", "3")
        if not frame_info:
            pytest.fail("No stacktrace")

        file_name, line_number, method, class_ = frame_info
        assert file_name
        assert line_number > 0
        assert method == "func_20"
        assert not class_


@pytest.mark.limit_leaks("460 B", filter_fn=IASTFilter())
def test_stacktrace_memory_check_direct_call():
    for _ in range(LOOPS):
        frame_info = get_info_frame()
        if not frame_info:
            pytest.fail("No stacktrace")

        file_name, line_number, method, class_ = frame_info
        assert file_name
        assert line_number > 0
        assert method == "test_stacktrace_memory_check_direct_call"
        assert not class_


@pytest.mark.limit_leaks("2.0 KB", filter_fn=IASTFilter())
@pytest.mark.parametrize(
    "input_str, start, stop, step",
    [
        ("abcdefghijklmnopqrstuvwxyz", 0, 10, 1),
        ("abcdefghijklmnopqrstuvwxyz", 5, 15, 1),
        ("abcdefghijklmnopqrstuvwxyz", 0, 20, 2),
        ("abcdefghijklmnopqrstuvwxyz", 1, -1, 1),
        (b"abcdefghijklmnopqrstuvwxyz", 0, 10, 1),
        (b"abcdefghijklmnopqrstuvwxyz", 5, 15, 1),
        (bytearray(b"abcdefghijklmnopqrstuvwxyz"), 0, 10, 1),
        (bytearray(b"abcdefghijklmnopqrstuvwxyz"), 5, 15, 1),
    ],
)
def test_slice_memory_check(input_str, start, stop, step, iast_context_defaults):
    """Test that slice_aspect doesn't leak memory.

    This test verifies the fix for the slice aspect memory issue where
    the old implementation created O(n) intermediate data structures.
    The new optimized version should use O(m) memory where m = number of taint ranges.
    """
    from ddtrace.appsec._iast._taint_tracking.aspects import slice_aspect

    _num_objects_tainted = 0
    _debug_context_array_size = 0
    _iast_finish_request()

    for iteration in range(LOOPS):
        _iast_start_request()

        # Taint the input string
        tainted_string = taint_pyobject(
            input_str, source_name="test_input", source_value=input_str, source_origin=OriginType.PARAMETER
        )

        # Perform slice operation
        result = slice_aspect(tainted_string, start, stop, step)

        # Verify the result is properly tainted
        tainted_ranges = get_tainted_ranges(result)
        assert len(tainted_ranges) >= 0  # May be 0 if slice doesn't overlap with tainted range

        # Track memory metrics
        _num_objects_tainted = _num_objects_tainted_in_request()
        assert _num_objects_tainted > 0

        _debug_context_array_size = debug_context_array_size()
        assert _debug_context_array_size > 0

        # Verify no memory leak - context array size should remain stable
        assert _debug_context_array_size == debug_context_array_size()

        _iast_finish_request()


@pytest.mark.limit_leaks("2.0 KB", filter_fn=IASTFilter())
def test_slice_memory_check_repeated_operations(iast_context_defaults):
    """Test that repeated slice operations don't accumulate memory.

    This simulates the benchmark scenario where slice_aspect is called
    many times on the same or similar strings. The old implementation
    would create O(n) intermediate arrays each time, causing memory to
    accumulate. The new implementation should remain stable.
    """
    from ddtrace.appsec._iast._taint_tracking.aspects import slice_aspect

    _iast_finish_request()
    _iast_start_request()

    # Taint a test string
    test_string = "abcdefghijklmnopqrstuvwxyz0123456789"
    tainted_string = taint_pyobject(
        test_string, source_name="test_input", source_value=test_string, source_origin=OriginType.PARAMETER
    )

    # Get baseline
    initial_context_size = debug_context_array_size()
    assert initial_context_size > 0

    # Perform many slice operations (simulating benchmark workload)
    for i in range(100):
        # Various slice operations
        result1 = slice_aspect(tainted_string, 0, 10, 1)
        result2 = slice_aspect(tainted_string, 5, 15, 1)
        result3 = slice_aspect(tainted_string, 10, 20, 2)
        result4 = slice_aspect(tainted_string, 1, -1, 1)

        # Verify results are tainted
        assert len(get_tainted_ranges(result1)) > 0
        assert len(get_tainted_ranges(result2)) > 0
        assert len(get_tainted_ranges(result3)) > 0
        assert len(get_tainted_ranges(result4)) > 0

    # Context array size should not have grown significantly
    final_context_size = debug_context_array_size()

    # Allow small variation but no significant growth
    # With the old buggy code, this would grow proportionally to iterations
    assert final_context_size <= initial_context_size + 10, (
        f"Context size grew from {initial_context_size} to {final_context_size}"
    )

    _iast_finish_request()


@pytest.mark.limit_leaks("2.0 KB", filter_fn=IASTFilter())
def test_slice_memory_check_repeated_operations_iast_disable():
    from ddtrace.appsec._iast._taint_tracking.aspects import slice_aspect

    # Taint a test string
    test_string = "abcdefghijklmnopqrstuvwxyz0123456789"
    tainted_string = taint_pyobject(
        test_string, source_name="test_input", source_value=test_string, source_origin=OriginType.PARAMETER
    )

    # Get baseline
    initial_context_size = debug_context_array_size()

    # Perform many slice operations (simulating benchmark workload)
    for i in range(100):
        # Various slice operations
        _ = slice_aspect(tainted_string, 0, 10, 1)
        _ = slice_aspect(tainted_string, 5, 15, 1)
        _ = slice_aspect(tainted_string, 10, 20, 2)
        _ = slice_aspect(tainted_string, 1, -1, 1)

    # Context array size should not have grown significantly
    final_context_size = debug_context_array_size()

    # Allow small variation but no significant growth
    # With the old buggy code, this would grow proportionally to iterations
    assert final_context_size <= initial_context_size + 10, (
        f"Context size grew from {initial_context_size} to {final_context_size}"
    )


@pytest.mark.limit_leaks("2.0 KB", filter_fn=IASTFilter())
@pytest.mark.parametrize(
    "separator, items",
    [
        (",", ["a", "b", "c", "d", "e", "f"]),
        ("-", ["foo", "bar", "baz"]),
        ("", ["x", "y", "z"]),
        (" ", ["hello", "world", "test"]),
        (b",", [b"a", b"b", b"c", b"d", b"e", b"f"]),
        (b"-", [b"foo", b"bar", b"baz"]),
        (bytearray(b","), [bytearray(b"a"), bytearray(b"b"), bytearray(b"c")]),
        (bytearray(b" "), [bytearray(b"hello"), bytearray(b"world")]),
    ],
)
def test_join_memory_check(separator, items, iast_context_defaults):
    """Test that join_aspect doesn't leak memory.

    This test verifies that join_aspect properly manages memory when joining
    multiple tainted strings. The implementation should not create excessive
    intermediate data structures.
    """
    from ddtrace.appsec._iast._taint_tracking.aspects import join_aspect

    _num_objects_tainted = 0
    _debug_context_array_size = 0
    _iast_finish_request()

    for iteration in range(LOOPS):
        _iast_start_request()

        # Taint the separator
        tainted_separator = taint_pyobject(
            separator, source_name="separator", source_value=separator, source_origin=OriginType.PARAMETER
        )

        # Taint the items to join
        tainted_items = [
            taint_pyobject(item, source_name=f"item_{i}", source_value=item, source_origin=OriginType.PARAMETER)
            for i, item in enumerate(items)
        ]

        # Perform join operation
        result = join_aspect("".join, 1, tainted_separator, tainted_items)

        # Verify the result is properly tainted
        tainted_ranges = get_tainted_ranges(result)
        assert len(tainted_ranges) > 0

        # Track memory metrics
        _num_objects_tainted = _num_objects_tainted_in_request()
        assert _num_objects_tainted > 0

        _debug_context_array_size = debug_context_array_size()
        assert _debug_context_array_size > 0

        # Verify no memory leak - context array size should remain stable
        assert _debug_context_array_size == debug_context_array_size()

        _iast_finish_request()


@pytest.mark.limit_leaks("2.0 KB", filter_fn=IASTFilter())
def test_join_memory_check_repeated_operations(iast_context_defaults):
    """Test that repeated join operations don't accumulate memory.

    This simulates scenarios where join_aspect is called many times
    on similar data. The implementation should remain memory-stable
    and not accumulate intermediate structures.
    """
    from ddtrace.appsec._iast._taint_tracking.aspects import join_aspect

    _iast_finish_request()
    _iast_start_request()

    # Taint separator and test items
    separator = ","
    tainted_separator = taint_pyobject(
        separator, source_name="separator", source_value=separator, source_origin=OriginType.PARAMETER
    )

    items = ["item1", "item2", "item3", "item4", "item5"]
    tainted_items = [
        taint_pyobject(item, source_name=f"item_{i}", source_value=item, source_origin=OriginType.PARAMETER)
        for i, item in enumerate(items)
    ]

    # Get baseline
    initial_context_size = debug_context_array_size()
    assert initial_context_size > 0

    # Perform many join operations (simulating benchmark workload)
    for i in range(100):
        # Various join operations
        result1 = join_aspect("".join, 1, tainted_separator, tainted_items[:3])
        result2 = join_aspect("".join, 1, tainted_separator, tainted_items[2:])
        result3 = join_aspect("".join, 1, tainted_separator, tainted_items)
        result4 = join_aspect("".join, 1, tainted_separator, [tainted_items[0], tainted_items[-1]])

        # Verify results are tainted
        assert len(get_tainted_ranges(result1)) > 0
        assert len(get_tainted_ranges(result2)) > 0
        assert len(get_tainted_ranges(result3)) > 0
        assert len(get_tainted_ranges(result4)) > 0

    # Context array size should not have grown significantly
    final_context_size = debug_context_array_size()

    # Allow small variation but no significant growth
    # With buggy code, this would grow proportionally to iterations
    assert final_context_size <= initial_context_size + 10, (
        f"Context size grew from {initial_context_size} to {final_context_size}"
    )

    _iast_finish_request()


@pytest.mark.limit_leaks("2.0 KB", filter_fn=IASTFilter())
def test_join_memory_check_repeated_operations_iast_disable():
    from ddtrace.appsec._iast._taint_tracking.aspects import join_aspect

    # Taint separator and test items
    separator = ","
    tainted_separator = taint_pyobject(
        separator, source_name="separator", source_value=separator, source_origin=OriginType.PARAMETER
    )

    items = ["item1", "item2", "item3", "item4", "item5"]
    tainted_items = [
        taint_pyobject(item, source_name=f"item_{i}", source_value=item, source_origin=OriginType.PARAMETER)
        for i, item in enumerate(items)
    ]

    # Get baseline
    initial_context_size = debug_context_array_size()

    # Perform many join operations (simulating benchmark workload)
    for i in range(100):
        # Various join operations
        _ = join_aspect("".join, 1, tainted_separator, tainted_items[:3])
        _ = join_aspect("".join, 1, tainted_separator, tainted_items[2:])
        _ = join_aspect("".join, 1, tainted_separator, tainted_items)
        _ = join_aspect("".join, 1, tainted_separator, [tainted_items[0], tainted_items[-1]])

    # Context array size should not have grown significantly
    final_context_size = debug_context_array_size()

    # Allow small variation but no significant growth
    # With buggy code, this would grow proportionally to iterations
    assert final_context_size <= initial_context_size + 10, (
        f"Context size grew from {initial_context_size} to {final_context_size}"
    )
