from itertools import groupby
import json
from typing import Dict  # noqa:F401
from typing import Iterable  # noqa:F401
from typing import List  # noqa:F401
from typing import Optional  # noqa:F401
from typing import Tuple  # noqa:F401

import ddtrace
from ddtrace.internal.ci_visibility.constants import COVERAGE_TAG_NAME
from ddtrace.internal.ci_visibility.telemetry.constants import TEST_FRAMEWORKS
from ddtrace.internal.ci_visibility.telemetry.coverage import COVERAGE_LIBRARY
from ddtrace.internal.ci_visibility.telemetry.coverage import record_code_coverage_empty
from ddtrace.internal.ci_visibility.telemetry.coverage import record_code_coverage_error
from ddtrace.internal.ci_visibility.telemetry.coverage import record_code_coverage_files
from ddtrace.internal.ci_visibility.telemetry.coverage import record_code_coverage_finished
from ddtrace.internal.ci_visibility.telemetry.coverage import record_code_coverage_started
from ddtrace.internal.ci_visibility.utils import get_relative_or_absolute_path_for_path
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)
_global_relative_file_paths_for_cov: Dict[str, Dict[str, str]] = {}

try:
    from coverage import Coverage
    from coverage import version_info as coverage_version

    # this public attribute became private after coverage==6.3
    EXECUTE_ATTR = "_execute" if coverage_version > (6, 3) else "execute"
except ImportError:
    Coverage = None  # type: ignore[misc,assignment]
    EXECUTE_ATTR = ""


def is_coverage_available():
    return Coverage is not None


def _initialize_coverage(root_dir):
    coverage_kwargs = {
        "data_file": None,
        "source": [root_dir],
        "config_file": False,
        "omit": [
            "*/site-packages/*",
        ],
    }
    cov_object = Coverage(**coverage_kwargs)
    cov_object.set_option("run:parallel", True)
    return cov_object


def _start_coverage(root_dir: str):
    coverage = _initialize_coverage(root_dir)
    coverage.start()
    return coverage


def _stop_coverage(module):
    if _module_has_dd_coverage_enabled(module):
        module._dd_coverage.stop()
        module._dd_coverage.erase()
        del module._dd_coverage


def _module_has_dd_coverage_enabled(module, silent_mode: bool = False) -> bool:
    if not hasattr(module, "_dd_coverage"):
        if not silent_mode:
            log.warning("Datadog Coverage has not been initiated")
        return False
    return True


def _coverage_has_valid_data(coverage_data: Coverage, silent_mode: bool = False) -> bool:
    if not coverage_data._collector or len(coverage_data._collector.data) == 0:
        if not silent_mode:
            log.warning("No coverage collector or data found for item")
        return False
    return True


def _switch_coverage_context(
    coverage_data: Coverage, unique_test_name: str, framework: Optional[TEST_FRAMEWORKS] = None
):
    record_code_coverage_started(COVERAGE_LIBRARY.COVERAGEPY, framework)
    if not _coverage_has_valid_data(coverage_data, silent_mode=True):
        return
    coverage_data._collector.data.clear()  # type: ignore[union-attr]
    try:
        coverage_data.switch_context(unique_test_name)
    except RuntimeError as err:
        record_code_coverage_error()
        log.warning(err)


def _report_coverage_to_span(
    coverage_data: Coverage, span: ddtrace.Span, root_dir: str, framework: Optional[TEST_FRAMEWORKS] = None
):
    span_id = str(span.trace_id)
    if not _coverage_has_valid_data(coverage_data):
        record_code_coverage_error()
        return
    record_code_coverage_finished(COVERAGE_LIBRARY.COVERAGEPY, framework)
    span.set_tag_str(
        COVERAGE_TAG_NAME,
        build_payload(coverage_data, root_dir, span_id),
    )
    coverage_data._collector.data.clear()  # type: ignore[union-attr]


def segments(lines: Iterable[int]) -> List[Tuple[int, int, int, int, int]]:
    """Extract the relevant report data for a single file."""
    _segments = []
    for _key, g in groupby(enumerate(sorted(lines)), lambda x: x[1] - x[0]):
        group = list(g)
        start = group[0][1]
        end = group[-1][1]
        _segments.append((start, 0, end, 0, -1))

    return _segments


def _lines(coverage: Coverage, context: Optional[str]) -> Dict[str, List[Tuple[int, int, int, int, int]]]:
    if not coverage._collector or not coverage._collector.data:
        return {}

    return {
        k: segments(v.keys()) if isinstance(v, dict) else segments(v)  # type: ignore
        for k, v in list(coverage._collector.data.items())
    }


def build_payload(coverage: Coverage, root_dir: str, test_id: Optional[str] = None) -> str:
    """
    Generate a CI Visibility coverage payload, formatted as follows:

    "files": [
        {
            "filename": <String>,
            "segments": [
                [Int, Int, Int, Int, Int],  # noqa:F401
            ]
        },
        ...
    ]

    For each segment of code for which there is coverage, there are always five integer values:
        The first number indicates the start line of the code block (index starting in 1)
        The second number indicates the start column of the code block (index starting in 1). Use value -1 if the
            column is unknown.
        The third number indicates the end line of the code block
        The fourth number indicates the end column of the code block
        The fifth number indicates the number of executions of the block
            If the number is >0 then it indicates the number of executions
            If the number is -1 then it indicates that the number of executions are unknown

    :param coverage: Coverage object containing coverage data
    :param root_dir: the directory relative to which paths to covered files should be resolved
    :param test_id: a unique identifier for the current test run
    """
    root_dir_str = str(root_dir)
    if root_dir_str not in _global_relative_file_paths_for_cov:
        _global_relative_file_paths_for_cov[root_dir_str] = {}
    files_data = []
    for filename, lines in _lines(coverage, test_id).items():
        if filename not in _global_relative_file_paths_for_cov[root_dir_str]:
            _global_relative_file_paths_for_cov[root_dir_str][filename] = get_relative_or_absolute_path_for_path(
                filename, root_dir_str
            )
        if lines:
            files_data.append(
                {"filename": _global_relative_file_paths_for_cov[root_dir_str][filename], "segments": lines}
            )
        else:
            files_data.append({"filename": _global_relative_file_paths_for_cov[root_dir_str][filename]})

    if len(files_data) == 0:
        record_code_coverage_empty()
    record_code_coverage_files(len(files_data))

    return json.dumps({"files": files_data})
