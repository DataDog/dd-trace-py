import json
import os
from pathlib import Path
from typing import Dict  # noqa:F401
from typing import Iterable  # noqa:F401
from typing import List  # noqa:F401
from typing import Optional  # noqa:F401
from typing import Tuple  # noqa:F401
from typing import Union  # noqa:F401

import ddtrace
from ddtrace.internal.ci_visibility.constants import COVERAGE_TAG_NAME
from ddtrace.internal.ci_visibility.telemetry.constants import TEST_FRAMEWORKS
from ddtrace.internal.ci_visibility.telemetry.coverage import COVERAGE_LIBRARY
from ddtrace.internal.ci_visibility.telemetry.coverage import record_code_coverage_finished
from ddtrace.internal.coverage.code import ModuleCodeCollector
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)
_global_relative_file_paths_for_cov: Dict[str, Dict[str, str]] = {}


def _start_coverage(root_dir: str):
    # Experimental feature to use internal coverage collection
    collector = ModuleCodeCollector.CollectInContext()

    from ddtrace.ext.git import extract_workspace_path

    try:
        workspace_path = Path(extract_workspace_path())
    except ValueError:
        workspace_path = Path(os.getcwd())

    setattr(collector, "_workspace_path", workspace_path)
    return collector


def _module_has_dd_coverage_enabled(module, silent_mode: bool = False) -> bool:
    # Experimental feature to use internal coverage collection
    return hasattr(module, "_dd_coverage")


def _report_coverage_to_span(
    coverage_data: ModuleCodeCollector.CollectInContext,
    span: ddtrace.trace.Span,
    root_dir: str,
    framework: Optional[TEST_FRAMEWORKS] = None,
):
    # In this case, coverage_data is the context manager supplied by ModuleCodeCollector.CollectInContext
    workspace_path = getattr(coverage_data, "_workspace_path", Path("/"))
    files = ModuleCodeCollector.report_seen_lines(workspace_path, include_imported=True)
    if not files:
        return
    span.set_tag_str(
        COVERAGE_TAG_NAME,
        json.dumps({"files": files}),
    )
    record_code_coverage_finished(COVERAGE_LIBRARY.COVERAGEPY, framework)
    coverage_data.__exit__(None, None, None)
