import inspect
import logging
import os
import re
import typing

import ddtrace
from ddtrace import config as ddconfig
from ddtrace.contrib.internal.coverage.constants import PCT_COVERED_KEY
from ddtrace.ext import test
from ddtrace.internal.ci_visibility.constants import CIVISIBILITY_LOG_FILTER_RE
from ddtrace.internal.ci_visibility.telemetry.constants import TEST_FRAMEWORKS
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
    span: ddtrace.trace.Span, test_method_object, test_name: str, repo_directory: str
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


def _add_pct_covered_to_span(coverage_data: dict, span: ddtrace.trace.Span):
    if not coverage_data or PCT_COVERED_KEY not in coverage_data:
        log.warning("Tried to add total covered percentage to session span but no data was found")
        return
    lines_pct_value = coverage_data[PCT_COVERED_KEY]
    if not isinstance(lines_pct_value, float):
        log.warning("Tried to add total covered percentage to session span but the format was unexpected")
        return
    span.set_tag(test.TEST_LINES_PCT, lines_pct_value)


def _generate_fully_qualified_test_name(test_module_path: str, test_suite_name: str, test_name: str) -> str:
    return "{}.{}.{}".format(test_module_path, test_suite_name, test_name)


def _generate_fully_qualified_module_name(test_module_path: str, test_suite_name: str) -> str:
    return "{}.{}".format(test_module_path, test_suite_name)


def take_over_logger_stream_handler(remove_ddtrace_stream_handlers=True):
    """Creates a handler with a filter for CIVisibility-specific messages. The also removes the existing
    handlers on the DDTrace logger, to prevent double-logging.

    This is useful for testrunners (currently pytest) that have their own logger.

    NOTE: This should **only** be called from testrunner-level integrations (eg: pytest, unittest).
    """
    if ddconfig._debug_mode:
        log.debug("CIVisibility not taking over ddtrace logger handler because debug mode is enabled")
        return

    level = ddconfig._ci_visibility_log_level

    if level.upper() == "NONE":
        log.debug("CIVisibility not taking over ddtrace logger because level is set to: %s", level)
        return

    root_logger = logging.getLogger()

    if remove_ddtrace_stream_handlers:
        log.debug("CIVisibility removing DDTrace logger handler")
        ddtrace_logger = logging.getLogger("ddtrace")
        for handler in list(ddtrace_logger.handlers):
            ddtrace_logger.removeHandler(handler)
    else:
        log.warning("Keeping DDTrace logger handler, double logging is likely")

    logger_name_re = re.compile(CIVISIBILITY_LOG_FILTER_RE)

    ci_visibility_handler = logging.StreamHandler()
    ci_visibility_handler.addFilter(lambda record: bool(logger_name_re.match(record.name)))
    ci_visibility_handler.setFormatter(
        logging.Formatter("[Datadog CI Visibility] %(levelname)-8s %(name)s:%(filename)s:%(lineno)d %(message)s")
    )

    try:
        ci_visibility_handler.setLevel(level.upper())
    except ValueError:
        log.warning("Invalid log level: %s", level)
        return

    root_logger.addHandler(ci_visibility_handler)
    root_logger.setLevel(min(root_logger.level, ci_visibility_handler.level))

    log.debug("logger setup complete")


def combine_url_path(*args: str):
    """Combine URL path segments.

    NOTE: this is custom-built for its current usage in the Test Visibility codebase. Use with care.
    """
    return "/".join(str(segment).strip("/") for segment in args)


def _get_test_framework_telemetry_name(test_framework: str) -> TEST_FRAMEWORKS:
    for framework in TEST_FRAMEWORKS:
        if framework.value == test_framework:
            return framework
    return TEST_FRAMEWORKS.MANUAL
