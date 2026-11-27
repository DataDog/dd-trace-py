"""Source locations of test functions, for test optimization tags."""

from dis import findlinestarts
from functools import lru_cache
import inspect
from pathlib import Path
import types
import typing as t

from ddtrace.internal.utils.paths import relative_path


# co_lines() is available from Python 3.10+; check once at import time.
# TODO: remove _CO_LINES_SUPPORTED and the findlinestarts fallback once Python 3.9 support is dropped.
_CO_LINES_SUPPORTED = hasattr((lambda: None).__code__, "co_lines")


@lru_cache(maxsize=512)
def get_relative_path(path: Path, root: Path) -> t.Optional[str]:
    """Return a checkout-relative path, resolving aliases only on a lexical miss."""
    relative = relative_path(path, root)
    if relative is not None:
        return relative
    # Git reports physical roots; callers may use a CI workspace symlink.
    try:
        return relative_path(path.resolve(), root.resolve())
    except (OSError, RuntimeError):
        return None


def _max_line_in_code(code: types.CodeType) -> int:
    """Return the maximum line number in a code object, recursing into nested code objects.

    Nested classes and functions at the tail of a function body have their lines stored only
    in child code objects inside co_consts, so without recursion we would under-report the end
    line for such functions.
    """
    max_line = code.co_firstlineno
    if _CO_LINES_SUPPORTED:
        for _, _, line in code.co_lines():
            if line is not None and line > max_line:
                max_line = line
    else:
        for _, line in findlinestarts(code):
            if line is not None and line > max_line:
                max_line = line
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            nested = _max_line_in_code(const)
            if nested > max_line:
                max_line = nested
    return max_line


@lru_cache(maxsize=512)
def get_source_lines_for_test_method(
    test_method_object: t.Any,
) -> t.Union[tuple[int, int], tuple[None, None]]:
    """
    Get the start and end line numbers for a test method.

    Cached by test method object: parametrized tests and ATF retries reuse the same
    function object across variants/attempts, so only the first call pays the cost.

    Returns:
        Tuple of (start_line, end_line), with None indicating unavailable information
    """
    # Fast path: derive line numbers from the bytecode rather than tokenizing source.
    # Unwrap functools.wraps-based decorators (e.g. @patch, @mock.patch) first so that
    # we read the original test function's code object, not the decorator wrapper's.
    # co_firstlineno gives the start; _max_line_in_code recurses into nested code objects
    # so that tests ending with a nested class/def report the correct end line.
    # TODO: drop the findlinestarts branch once Python 3.9 support is removed.
    try:
        func = inspect.unwrap(test_method_object)
        code = func.__code__
        start_line = code.co_firstlineno
        end_line = _max_line_in_code(code)
        return start_line, end_line + 1
    except AttributeError:
        pass  # not a plain function; fall through to inspect
    except Exception:
        return None, None

    # Fallback for class-based callables and other non-function objects
    try:
        source_lines_tuple = inspect.getsourcelines(test_method_object)
    except (TypeError, OSError):
        return None, None
    start_line = source_lines_tuple[1]
    end_line = start_line + len(source_lines_tuple[0])
    return start_line, end_line
