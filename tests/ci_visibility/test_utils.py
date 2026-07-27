import inspect
import os.path
from unittest import mock
from unittest.mock import patch

from ddtrace.internal.ci_visibility.utils import get_relative_or_absolute_path_for_path
from ddtrace.internal.ci_visibility.utils import get_source_lines_for_test_method


def test_gets_relative_path():
    actual_output = get_relative_or_absolute_path_for_path("ddtrace/contrib", start_directory=os.getcwd())

    assert not os.path.isabs(actual_output)


def test_gets_absolute_path_with_exception():
    with mock.patch("os.path.relpath", side_effect=ValueError()):
        actual_output = get_relative_or_absolute_path_for_path("ddtrace/contrib", start_directory=os.getcwd())

        assert os.path.isabs(actual_output)


# ---------------------------------------------------------------------------
# Helpers used by get_source_lines tests — defined at module scope so they
# have stable, inspectable source locations.
# ---------------------------------------------------------------------------


@patch("os.path.exists")
def _patch_decorated(mock_exists):
    x = 1
    y = 2
    assert x + y == 3


def _ends_with_nested_class():
    x = 1

    class _Inner:
        def method(self):
            return x

        def other(self):
            return x + 1


def _ends_with_nested_function():
    result = []

    def _helper(val):
        result.append(val)
        return val * 2

    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_source_lines_for_patch_decorated_function():
    """Decorated functions must return lines of the original function body, not the decorator wrapper."""
    get_source_lines_for_test_method.cache_clear()
    start, end = get_source_lines_for_test_method(_patch_decorated)

    unwrapped = inspect.unwrap(_patch_decorated)
    source_lines, first_line = inspect.getsourcelines(unwrapped)
    expected_start = first_line
    expected_end = first_line + len(source_lines)

    assert start == expected_start, f"start: expected {expected_start}, got {start}"
    assert end == expected_end, f"end: expected {expected_end}, got {end}"


def test_source_lines_includes_nested_class_body():
    """End line must cover lines inside nested classes defined at the end of a function."""
    get_source_lines_for_test_method.cache_clear()
    start, end = get_source_lines_for_test_method(_ends_with_nested_class)

    source_lines, first_line = inspect.getsourcelines(_ends_with_nested_class)
    expected_start = first_line
    expected_end = first_line + len(source_lines)

    assert start == expected_start, f"start: expected {expected_start}, got {start}"
    assert end == expected_end, f"end: expected {expected_end}, got {end}"


def test_source_lines_includes_nested_function_body():
    """End line must cover lines inside nested functions defined at the end of a function."""
    get_source_lines_for_test_method.cache_clear()
    start, end = get_source_lines_for_test_method(_ends_with_nested_function)

    source_lines, first_line = inspect.getsourcelines(_ends_with_nested_function)
    expected_start = first_line
    expected_end = first_line + len(source_lines)

    assert start == expected_start, f"start: expected {expected_start}, got {start}"
    assert end == expected_end, f"end: expected {expected_end}, got {end}"


def test_source_lines_plain_function():
    """Sanity check: a plain (un-decorated, non-nested) function is reported correctly."""
    get_source_lines_for_test_method.cache_clear()

    def _plain():
        a = 1
        b = 2
        return a + b

    start, end = get_source_lines_for_test_method(_plain)

    source_lines, first_line = inspect.getsourcelines(_plain)
    expected_start = first_line
    expected_end = first_line + len(source_lines)

    assert start == expected_start, f"start: expected {expected_start}, got {start}"
    assert end == expected_end, f"end: expected {expected_end}, got {end}"
