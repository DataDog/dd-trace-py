import inspect
import os
import typing

from ddtrace.internal.logger import get_logger

log = get_logger(__name__)


def get_source_file_path_for_test_method(test_method_object, test_name) -> typing.Union[str, None]:
    try:
        source_file_path = os.path.relpath(inspect.getfile(test_method_object))
    except TypeError:
        log.debug("Tried to collect file path for test %s but it is a built-in Python function", test_name)
        return None
    return source_file_path


def get_source_lines_for_test_method(
    test_method_object, test_name
) -> typing.Union[typing.Tuple[int, int], typing.Tuple[None, None]]:
    try:
        source_lines_tuple = inspect.getsourcelines(test_method_object)
    except TypeError or OSError:
        log.debug("Tried to collect source start/end lines for test method %s but an exception was raised", test_name)
        return None, None
    start_line = source_lines_tuple[1]
    end_line = start_line + len(source_lines_tuple[0])
    return start_line, end_line
