import inspect
import os
import typing

import ddtrace
from ddtrace.ext import test
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def get_relative_or_absolute_path_for_path(path: str, start_directory: str):
    try:
        relative_path = os.path.relpath(path, start=start_directory)
    except ValueError:
        log.debug(
            "Tried to collect relative path but it is using different drive paths on Windows, "
            "using absolute path instead",
        )
        return os.path.abspath(path)
    return relative_path


def get_source_file_path_for_test_method(test_method_object, repo_directory: str) -> typing.Union[str, None]:
    try:
        file_object = inspect.getfile(test_method_object)
    except TypeError:
        return ""

    return get_relative_or_absolute_path_for_path(file_object, repo_directory)


def get_source_lines_for_test_method(
    test_method_object,
) -> typing.Union[typing.Tuple[int, int], typing.Tuple[None, None]]:
    try:
        source_lines_tuple = inspect.getsourcelines(test_method_object)
    except (TypeError, OSError):
        return None, None
    start_line = source_lines_tuple[1]
    end_line = start_line + len(source_lines_tuple[0])
    return start_line, end_line


def _add_start_end_source_file_path_data_to_span(
    span: ddtrace.Span, test_method_object, test_name: str, repo_directory: str
):
    if not test_method_object:
        log.debug(
            "Tried to collect source start/end lines for test method %s but test method object could not be found",
            test_name,
        )
        return
    source_file_path = get_source_file_path_for_test_method(test_method_object, repo_directory)
    if not source_file_path:
        log.debug(
            "Tried to collect file path for test %s but it is a built-in Python function",
            test_name,
        )
        return
    start_line, end_line = get_source_lines_for_test_method(test_method_object)
    if not start_line or not end_line:
        log.debug("Tried to collect source start/end lines for test method %s but an exception was raised", test_name)
    span.set_tag_str(test.SOURCE_FILE, source_file_path)
    if start_line:
        span.set_tag(test.SOURCE_START, start_line)
    if end_line:
        span.set_tag(test.SOURCE_END, end_line)
