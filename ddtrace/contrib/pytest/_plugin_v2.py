from pathlib import Path
import re
import typing as t

import pytest

from ddtrace import config as dd_config
from ddtrace.contrib.coverage import patch as patch_coverage
from ddtrace.contrib.internal.coverage.constants import PCT_COVERED_KEY
from ddtrace.contrib.internal.coverage.data import _coverage_data
from ddtrace.contrib.internal.coverage.patch import run_coverage_report
from ddtrace.contrib.internal.coverage.utils import _is_coverage_invoked_by_coverage_run
from ddtrace.contrib.internal.coverage.utils import _is_coverage_patched
from ddtrace.contrib.pytest._plugin_v1 import _extract_reason
from ddtrace.contrib.pytest._plugin_v1 import _is_pytest_cov_enabled
from ddtrace.contrib.pytest._utils import _get_module_path_from_item
from ddtrace.contrib.pytest._utils import _get_names_from_item
from ddtrace.contrib.pytest._utils import _get_session_command
from ddtrace.contrib.pytest._utils import _get_source_file_info
from ddtrace.contrib.pytest._utils import _get_test_id_from_item
from ddtrace.contrib.pytest._utils import _get_test_parameters_json
from ddtrace.contrib.pytest._utils import _is_enabled_early
from ddtrace.contrib.pytest._utils import _is_test_unskippable
from ddtrace.contrib.pytest._utils import _pytest_marked_to_skip
from ddtrace.contrib.pytest.constants import FRAMEWORK
from ddtrace.contrib.pytest.constants import XFAIL_REASON
from ddtrace.contrib.pytest.plugin import is_enabled
from ddtrace.contrib.unittest import unpatch as unpatch_unittest
from ddtrace.ext import test
from ddtrace.ext.test_visibility.api import TestExcInfo
from ddtrace.ext.test_visibility.api import disable_test_visibility
from ddtrace.ext.test_visibility.api import enable_test_visibility
from ddtrace.ext.test_visibility.api import is_test_visibility_enabled
from ddtrace.internal.ci_visibility.constants import SKIPPED_BY_ITR_REASON
from ddtrace.internal.ci_visibility.telemetry.coverage import COVERAGE_LIBRARY
from ddtrace.internal.ci_visibility.telemetry.coverage import record_code_coverage_empty
from ddtrace.internal.ci_visibility.telemetry.coverage import record_code_coverage_finished
from ddtrace.internal.ci_visibility.telemetry.coverage import record_code_coverage_started
from ddtrace.internal.ci_visibility.utils import take_over_logger_stream_handler
from ddtrace.internal.coverage.code import ModuleCodeCollector
from ddtrace.internal.coverage.installer import install as install_coverage
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility.api import InternalTest
from ddtrace.internal.test_visibility.api import InternalTestModule
from ddtrace.internal.test_visibility.api import InternalTestSession
from ddtrace.internal.test_visibility.api import InternalTestSuite
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


log = get_logger(__name__)


_NODEID_REGEX = re.compile("^((?P<module>.*)/(?P<suite>[^/]*?))::(?P<name>.*?)$")


def _handle_itr_should_skip(item, test_id) -> bool:
    """Checks whether a test should be skipped

    This function has the side effect of marking the test as skipped immediately if it should be skipped.
    """
    if not InternalTestSession.is_test_skipping_enabled():
        return False

    suite_id = test_id.parent_id

    item_is_unskippable = InternalTestSuite.is_itr_unskippable(suite_id)

    if InternalTestSuite.is_itr_skippable(suite_id):
        if item_is_unskippable:
            # Marking the test as forced run also applies to its hierarchy
            InternalTest.mark_itr_forced_run(test_id)
            return False
        else:
            InternalTest.mark_itr_skipped(test_id)
            # Marking the test as skipped by ITR so that it appears in pytest's output
            item.add_marker(pytest.mark.skip(reason=SKIPPED_BY_ITR_REASON))  # TODO don't rely on internal for reason
            return True

    return False


def _start_collecting_coverage() -> ModuleCodeCollector.CollectInContext:
    coverage_collector = ModuleCodeCollector.CollectInContext()
    # TODO: don't depend on internal for telemetry
    record_code_coverage_started(COVERAGE_LIBRARY.COVERAGEPY, FRAMEWORK)

    coverage_collector.__enter__()

    return coverage_collector


def _handle_collected_coverage(test_id, coverage_collector) -> None:
    # TODO: clean up internal coverage API usage
    test_covered_lines = coverage_collector.get_covered_lines()
    coverage_collector.__exit__()

    record_code_coverage_finished(COVERAGE_LIBRARY.COVERAGEPY, FRAMEWORK)

    if not test_covered_lines:
        log.debug("No covered lines found for test %s", test_id)
        record_code_coverage_empty()
        return

    coverage_data: t.Dict[Path, CoverageLines] = {}

    for path_str, covered_lines in test_covered_lines.items():
        coverage_data[Path(path_str).absolute()] = covered_lines

    InternalTestSuite.add_coverage_data(test_id.parent_id, coverage_data)


def _handle_coverage_dependencies(suite_id) -> None:
    coverage_data = InternalTestSuite.get_coverage_data(suite_id)
    coverage_paths = coverage_data.keys()
    import_coverage = ModuleCodeCollector.get_import_coverage_for_paths(coverage_paths)
    InternalTestSuite.add_coverage_data(suite_id, import_coverage)


def _disable_ci_visibility():
    try:
        disable_test_visibility()
    except:  # noqa: E722
        log.debug("encountered error during disable_ci_visibility", exc_info=True)


def pytest_load_initial_conftests(early_config, parser, args):
    """Performs the bare-minimum to determine whether or ModuleCodeCollector should be enabled

    ModuleCodeCollector has a tangible impact on the time it takes to load modules, so it should only be installed if
    coverage collection is requested by the backend.
    """
    if not _is_enabled_early(early_config):
        return

    try:
        take_over_logger_stream_handler()
        dd_config.test_visibility.itr_skipping_level = "suite"
        enable_test_visibility(config=dd_config.pytest)
        if InternalTestSession.should_collect_coverage():
            workspace_path = InternalTestSession.get_workspace_path()
            if workspace_path is None:
                workspace_path = Path.cwd().absolute()
            log.warning("Installing ModuleCodeCollector with include_paths=%s", [workspace_path])
            install_coverage(include_paths=[workspace_path], collect_import_time_coverage=True)
    except:  # noqa: E722
        log.warning("encountered error during configure, disabling Datadog CI Visibility", exc_info=True)
        _disable_ci_visibility()


def pytest_configure(config: pytest.Config) -> None:
    try:
        if is_enabled(config):
            take_over_logger_stream_handler()
            unpatch_unittest()
            enable_test_visibility(config=dd_config.pytest)
            if _is_pytest_cov_enabled(config):
                patch_coverage()
        else:
            # If the pytest ddtrace plugin is not enabled, we should disable CI Visibility, as it was enabled during
            # pytest_load_initial_conftests
            _disable_ci_visibility()
    except:  # noqa: E722
        log.warning("encountered error during configure, disabling Datadog CI Visibility", exc_info=True)
        _disable_ci_visibility()


def pytest_sessionstart(session: pytest.Session) -> None:
    if not is_test_visibility_enabled():
        return

    log.debug("CI Visibility enabled - starting test session")

    try:
        command = _get_session_command(session)

        InternalTestSession.discover(
            test_command=command,
            test_framework=FRAMEWORK,
            test_framework_version=pytest.__version__,
            session_operation_name="pytest.test_session",
            module_operation_name="pytest.test_module",
            suite_operation_name="pytest.test_suite",
            test_operation_name=dd_config.pytest.operation_name,
            reject_duplicates=False,
        )

        InternalTestSession.start()
    except:  # noqa: E722
        log.debug("encountered error during session start, disabling Datadog CI Visibility", exc_info=True)
        _disable_ci_visibility()


def _pytest_collection_finish(session) -> None:
    """Discover modules, suites, and tests that have been selected by pytest

    NOTE: Using pytest_collection_finish instead of pytest_collection_modifyitems allows us to capture only the
    tests that pytest has selection for run (eg: with the use of -k as an argument).
    """
    for item in session.items:
        test_id = _get_test_id_from_item(item)
        suite_id = test_id.parent_id
        module_id = suite_id.parent_id

        # TODO: don't rediscover modules and suites if already discovered
        InternalTestModule.discover(module_id, _get_module_path_from_item(item))
        InternalTestSuite.discover(suite_id)

        item_path = Path(item.path if hasattr(item, "path") else item.fspath).absolute()

        item_codeowners = InternalTestSession.get_path_codeowners(item_path)

        source_file_info = _get_source_file_info(item, item_path)

        InternalTest.discover(test_id, codeowners=item_codeowners, source_file_info=source_file_info)

        markers = [marker.kwargs for marker in item.iter_markers(name="dd_tags")]
        for tags in markers:
            InternalTest.set_tags(test_id, tags)

        # Pytest markers do not allow us to determine if the test or the suite was marked as unskippable, but any
        # test marked unskippable in a suite makes the entire suite unskippable (since we are in suite skipping
        # mode)
        if InternalTestSession.is_test_skipping_enabled() and _is_test_unskippable(item):
            InternalTest.mark_itr_unskippable(test_id)
            InternalTestSuite.mark_itr_unskippable(suite_id)


def pytest_collection_finish(session) -> None:
    if not is_test_visibility_enabled():
        return

    try:
        return _pytest_collection_finish(session)
    except:  # noqa: E722
        log.debug("encountered error during collection finish, disabling Datadog CI Visibility", exc_info=True)
        _disable_ci_visibility()


def _pytest_runtest_protocol_pre_yield(item) -> t.Optional[ModuleCodeCollector.CollectInContext]:
    test_id = _get_test_id_from_item(item)
    suite_id = test_id.parent_id
    module_id = suite_id.parent_id

    # TODO: don't re-start modules if already started
    InternalTestModule.start(module_id)
    InternalTestSuite.start(suite_id)

    # DEV: pytest's fixtures resolution may change parameters between collection finish and test run
    parameters = _get_test_parameters_json(item)
    if parameters is not None:
        InternalTest.set_parameters(test_id, parameters)

    InternalTest.start(test_id)

    _handle_itr_should_skip(item, test_id)

    item_will_skip = _pytest_marked_to_skip(item) or InternalTest.was_skipped_by_itr(test_id)

    collect_test_coverage = InternalTestSession.should_collect_coverage() and not item_will_skip

    if collect_test_coverage:
        return _start_collecting_coverage()

    return None


def _pytest_runtest_protocol_post_yield(item, nextitem, coverage_collector):
    test_id = _get_test_id_from_item(item)
    suite_id = test_id.parent_id
    module_id = suite_id.parent_id

    if coverage_collector is not None:
        _handle_collected_coverage(test_id, coverage_collector)

    # We rely on the CI Visibility service to prevent finishing items that have been discovered and have unfinished
    # children, but as an optimization:
    # - we know we don't need to finish the suite if the next item is in the same suite
    # - we know we don't need to finish the module if the next item is in the same module
    # - we trust that the next item is in the same module if it is in the same suite
    next_test_id = _get_test_id_from_item(nextitem) if nextitem else None
    if next_test_id is None or next_test_id.parent_id != suite_id:
        if InternalTestSuite.is_itr_skippable(suite_id) and not InternalTestSuite.was_forced_run(suite_id):
            InternalTestSuite.mark_itr_skipped(suite_id)
        else:
            _handle_coverage_dependencies(suite_id)
            InternalTestSuite.finish(suite_id)
        if nextitem is None or (next_test_id is not None and next_test_id.parent_id.parent_id != module_id):
            InternalTestModule.finish(module_id)


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_protocol(item, nextitem) -> None:
    """Discovers tests, and starts tests, suites, and modules, then handles coverage data collection"""
    if not is_test_visibility_enabled():
        yield
        return

    try:
        coverage_collector = _pytest_runtest_protocol_pre_yield(item)
    except:  # noqa: E722
        log.debug("encountered error during pre-test", exc_info=True)

    # Yield control back to pytest to run the test
    yield

    try:
        return _pytest_runtest_protocol_post_yield(item, nextitem, coverage_collector)
    except:  # noqa: E722
        log.debug("encountered error during post-test", exc_info=True)
        return


def _pytest_runtest_makereport(item, call, outcome):
    result = outcome.get_result()

    test_id = _get_test_id_from_item(item)

    has_exception = call.excinfo is not None

    # There are scenarios in which we may have already finished this item in setup or call, eg:
    # - it was skipped by ITR
    # - it was marked with skipif
    if InternalTest.is_finished(test_id):
        return

    # In cases where a test was marked as XFAIL, the reason is only available during when call.when == "call", so we
    # add it as a tag immediately:
    if getattr(result, "wasxfail", None):
        InternalTest.set_tag(test_id, XFAIL_REASON, result.wasxfail)
    elif "xfail" in getattr(result, "keywords", []) and getattr(result, "longrepr", None):
        InternalTest.set_tag(test_id, XFAIL_REASON, result.longrepr)

    # Only capture result if:
    # - there is an exception
    # - the test failed
    # - the test passed with xfail
    # - we are tearing down the test
    # DEV NOTE: some skip scenarios (eg: skipif) have an exception during setup

    if call.when != "teardown" and not (has_exception or result.failed):
        return

    xfail = hasattr(result, "wasxfail") or "xfail" in result.keywords
    xfail_reason_tag = InternalTest.get_tag(test_id, XFAIL_REASON) if xfail else None
    has_skip_keyword = any(x in result.keywords for x in ["skip", "skipif", "skipped"])

    # If run with --runxfail flag, tests behave as if they were not marked with xfail,
    # that's why no XFAIL_REASON or test.RESULT tags will be added.
    if result.skipped:
        if InternalTest.was_skipped_by_itr(test_id):
            # Items that were skipped by ITR already have their status set
            return

        if xfail and not has_skip_keyword:
            # XFail tests that fail are recorded skipped by pytest, should be passed instead
            if not item.config.option.runxfail:
                InternalTest.set_tag(test_id, test.RESULT, test.Status.XFAIL.value)
                if xfail_reason_tag is None:
                    InternalTest.set_tag(test_id, XFAIL_REASON, getattr(result, "wasxfail", "XFail"))
                InternalTest.mark_pass(test_id)
                return

        InternalTest.mark_skip(test_id, _extract_reason(call))
        return

    if result.passed:
        if xfail and not has_skip_keyword and not item.config.option.runxfail:
            # XPass (strict=False) are recorded passed by pytest
            if xfail_reason_tag is None:
                InternalTest.set_tag(test_id, XFAIL_REASON, "XFail")
            InternalTest.set_tag(test_id, test.RESULT, test.Status.XPASS.value)

        InternalTest.mark_pass(test_id)
        return

    if xfail and not has_skip_keyword and not item.config.option.runxfail:
        # XPass (strict=True) are recorded failed by pytest, longrepr contains reason
        if xfail_reason_tag is None:
            InternalTest.set_tag(test_id, XFAIL_REASON, getattr(result, "longrepr", "XFail"))
        InternalTest.set_tag(test_id, test.RESULT, test.Status.XPASS.value)
        InternalTest.mark_fail(test_id)
        return

    exc_info = TestExcInfo(call.excinfo.type, call.excinfo.value, call.excinfo.tb) if call.excinfo else None

    InternalTest.mark_fail(test_id, exc_info)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call) -> None:
    """Store outcome for tracing."""
    outcome: pytest.TestReport
    outcome = yield

    if not is_test_visibility_enabled():
        return

    try:
        return _pytest_runtest_makereport(item, call, outcome)
    except Exception:  # noqa: E722
        log.debug("encountered error during makereport", exc_info=True)


def _pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if not is_test_visibility_enabled():
        return

    invoked_by_coverage_run_status = _is_coverage_invoked_by_coverage_run()
    pytest_cov_status = _is_pytest_cov_enabled(session.config)
    if _is_coverage_patched() and (pytest_cov_status or invoked_by_coverage_run_status):
        if invoked_by_coverage_run_status and not pytest_cov_status:
            run_coverage_report()

        lines_pct_value = _coverage_data.get(PCT_COVERED_KEY, None)
        if not isinstance(lines_pct_value, float):
            log.warning("Tried to add total covered percentage to session span but the format was unexpected")
            return
        InternalTestSession.set_covered_lines_pct(lines_pct_value)

    if ModuleCodeCollector.is_installed():
        ModuleCodeCollector.uninstall()

    InternalTestSession.finish(force_finish_children=True)
    disable_test_visibility()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if not is_test_visibility_enabled():
        return

    try:
        _pytest_sessionfinish(session, exitstatus)
    except:  # noqa: E722
        log.debug("encountered error during session finish", exc_info=True)
        # Try, again, to disable CI Visibility just in case
        _disable_ci_visibility()


@pytest.hookimpl(trylast=True)
def pytest_ddtrace_get_item_module_name(item):
    names = _get_names_from_item(item)
    return names.module


@pytest.hookimpl(trylast=True)
def pytest_ddtrace_get_item_suite_name(item):
    """
    Extract suite name from a `pytest.Item` instance.
    If the module path doesn't exist, the suite path will be reported in full.
    """
    names = _get_names_from_item(item)
    return names.suite


@pytest.hookimpl(trylast=True)
def pytest_ddtrace_get_item_test_name(item):
    """Extract name from item, prepending class if desired"""
    names = _get_names_from_item(item)
    return names.test
