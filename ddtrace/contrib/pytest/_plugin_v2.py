from dataclasses import dataclass
from pathlib import Path
import re
import typing as t

from _pytest.logging import caplog_handler_key
from _pytest.logging import caplog_records_key
from _pytest.runner import CallInfo
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
from ddtrace.ext.test_visibility import ITR_SKIPPING_LEVEL
from ddtrace.ext.test_visibility.api import TestExcInfo
from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.ext.test_visibility.api import disable_test_visibility
from ddtrace.ext.test_visibility.api import enable_test_visibility
from ddtrace.ext.test_visibility.api import is_test_visibility_enabled
from ddtrace.internal import core
from ddtrace.internal.ci_visibility.constants import SKIPPED_BY_ITR_REASON
from ddtrace.internal.ci_visibility.telemetry.coverage import COVERAGE_LIBRARY
from ddtrace.internal.ci_visibility.telemetry.coverage import record_code_coverage_empty
from ddtrace.internal.ci_visibility.telemetry.coverage import record_code_coverage_finished
from ddtrace.internal.ci_visibility.telemetry.coverage import record_code_coverage_started
from ddtrace.internal.ci_visibility.utils import take_over_logger_stream_handler
from ddtrace.internal.coverage.code import ModuleCodeCollector
from ddtrace.internal.coverage.installer import install as install_coverage
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility._efd_mixins import EFDTestStatus
from ddtrace.internal.test_visibility.api import InternalTest
from ddtrace.internal.test_visibility.api import InternalTestModule
from ddtrace.internal.test_visibility.api import InternalTestSession
from ddtrace.internal.test_visibility.api import InternalTestSuite
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


log = get_logger(__name__)


_NODEID_REGEX = re.compile("^((?P<module>.*)/(?P<suite>[^/]*?))::(?P<name>.*?)$")


@dataclass
class __EFD_OUTCOMES:
    ATTEMPT_PASSED = "dd_efd_attempt_passed"
    ATTEMPT_FAILED = "dd_efd_attempt_failed"
    ATTEMPT_SKIPPED = "dd_efd_attempt_skipped"
    FINAL_PASSED = "dd_efd_final_passed"
    FINAL_FAILED = "dd_efd_final_failed"
    FINAL_SKIPPED = "dd_efd_final_skipped"
    FINAL_FLAKY = "dd_efd_final_flaky"


_EFD_OUTCOMES = __EFD_OUTCOMES()
_EFD_FLAKY_OUTCOME = "Flaky"


class _TestOutcome(t.NamedTuple):
    status: t.Optional[TestStatus] = None
    skip_reason: t.Optional[str] = None
    exc_info: t.Optional[TestExcInfo] = None


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
    except Exception:  # noqa: E722
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
        dd_config.test_visibility.itr_skipping_level = ITR_SKIPPING_LEVEL.SUITE
        enable_test_visibility(config=dd_config.pytest)
        if InternalTestSession.should_collect_coverage():
            workspace_path = InternalTestSession.get_workspace_path()
            if workspace_path is None:
                workspace_path = Path.cwd().absolute()
            log.warning("Installing ModuleCodeCollector with include_paths=%s", [workspace_path])
            install_coverage(include_paths=[workspace_path], collect_import_time_coverage=True)
    except Exception:  # noqa: E722
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
    except Exception:  # noqa: E722
        log.warning("encountered error during configure, disabling Datadog CI Visibility", exc_info=True)
        _disable_ci_visibility()


def pytest_unconfigure(config: pytest.Config) -> None:
    if not is_test_visibility_enabled():
        return

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
    except Exception:  # noqa: E722
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

        # NOTE: EFD enablement status is already specified during service enablement
        if InternalTestSession.efd_is_faulty_session():
            log.warning("Early Flake Detection disabled: too many new tests detected")


def pytest_collection_finish(session) -> None:
    if not is_test_visibility_enabled():
        return

    try:
        return _pytest_collection_finish(session)
    except Exception:  # noqa: E722
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
    except Exception:  # noqa: E722
        log.debug("encountered error during pre-test", exc_info=True)

    # Yield control back to pytest to run the test
    yield

    try:
        return _pytest_runtest_protocol_post_yield(item, nextitem, coverage_collector)
    except Exception:  # noqa: E722
        log.debug("encountered error during post-test", exc_info=True)
        return


def _efd_run_when(item, nextitem, when):
    hooks = {
        "setup": item.ihook.pytest_runtest_setup,
        "call": item.ihook.pytest_runtest_call,
        "teardown": item.ihook.pytest_runtest_teardown,
    }
    hook = hooks[when]
    # NOTE: we use nextitem=item here to make sure that logs don't generate a new line
    if when == "teardown":
        call = CallInfo.from_call(
            lambda: hook(item=item, nextitem=pytest.Class.from_parent(item.session, name="forced_teardown")), when=when
        )
    else:
        call = CallInfo.from_call(lambda: hook(item=item), when=when)
    report = pytest.TestReport.from_item_and_call(item=item, call=call)
    if report.outcome == "passed":
        report.outcome = _EFD_OUTCOMES.ATTEMPT_PASSED
    elif report.outcome == "failed" or report.outcome == "error":
        report.outcome = _EFD_OUTCOMES.ATTEMPT_FAILED
    elif report.outcome == "skipped":
        report.outcome = _EFD_OUTCOMES.ATTEMPT_SKIPPED
    # Only log for actual test calls, or failures
    if when == "call" or "passed" not in report.outcome:
        item.ihook.pytest_runtest_logreport(report=report)
    return call, report


def _efd_get_outcome_from_item(
    item: pytest.Item, nextitem: t.Optional[pytest.Item], force_teardown: bool = False
) -> _TestOutcome:
    _outcome_status: t.Optional[TestStatus] = None
    _outcome_skip_reason: t.Optional[str] = None
    _outcome_exc_info: t.Optional[TestExcInfo] = None

    if force_teardown:
        # Clear any previous fixtures:
        t_call = pytest.CallInfo.from_call(
            lambda: item.ihook.pytest_runtest_teardown(
                item=item, nextitem=pytest.Class.from_parent(item.session, name="Fakeboi")
            ),
            when="teardown",
        )
        if t_call.excinfo is not None:
            log.debug("Error during initial EFD teardoown", exc_info=True)
            return _TestOutcome(
                status=TestStatus.FAIL,
                exc_info=TestExcInfo(t_call.excinfo.type, t_call.excinfo.value, t_call.excinfo.tb),
            )

    # _initrequest() needs to be called first because the test has already executed once
    item._initrequest()

    # Setup
    setup_call, setup_report = _efd_run_when(item, nextitem, "setup")
    if setup_report.failed:
        _outcome_status = TestStatus.FAIL
        if setup_call.excinfo is not None:
            _outcome_exc_info = TestExcInfo(setup_call.excinfo.type, setup_call.excinfo.value, setup_call.excinfo.tb)
            item.stash[caplog_records_key] = {}
            item.stash[caplog_handler_key] = {}
    # Call
    if setup_report.outcome == _EFD_OUTCOMES.ATTEMPT_PASSED:
        call_call, call_report = _efd_run_when(item, nextitem, "call")
        if call_report.outcome == _EFD_OUTCOMES.ATTEMPT_FAILED:
            _outcome_status = TestStatus.FAIL
            if call_call.excinfo is not None:
                _outcome_exc_info = TestExcInfo(call_call.excinfo.type, call_call.excinfo.value, call_call.excinfo.tb)
                item.stash[caplog_records_key] = {}
                item.stash[caplog_handler_key] = {}
        elif call_report.skipped:
            # A test that skips during retries is considered a failure
            _outcome_status = TestStatus.FAIL
        elif call_report.outcome == _EFD_OUTCOMES.ATTEMPT_PASSED:
            _outcome_status = TestStatus.PASS

    # Teardown
    teardown_call, teardown_report = _efd_run_when(item, nextitem, "teardown")
    # Only override the outcome if the teardown failed, otherwise defer to either setup or call outcome
    if teardown_report.outcome == _EFD_OUTCOMES.ATTEMPT_FAILED:
        _outcome_status = TestStatus.FAIL
        if teardown_call.excinfo is not None:
            _outcome_exc_info = TestExcInfo(
                teardown_call.excinfo.type, teardown_call.excinfo.value, teardown_call.excinfo.tb
            )
            item.stash[caplog_records_key] = {}
            item.stash[caplog_handler_key] = {}

    item._initrequest()

    return _TestOutcome(status=_outcome_status, skip_reason=_outcome_skip_reason, exc_info=_outcome_exc_info)


def _handle_efd_retries(item, original_outcome: _TestOutcome, force_teardown: bool = False) -> EFDTestStatus:
    test_id = _get_test_id_from_item(item)

    while InternalTest.efd_should_retry(test_id):
        retry_num = InternalTest.efd_add_retry(test_id, start_immediately=True)

        with core.context_with_data(f"pytest-efd-retry-{item.nodeid}") as ctx:
            ctx.set_item("efd_retry_num", retry_num)
            retry_outcome = _efd_get_outcome_from_item(item, None, force_teardown)

        InternalTest.efd_finish_retry(
            test_id, retry_num, retry_outcome.status, retry_outcome.skip_reason, retry_outcome.exc_info
        )

    return InternalTest.efd_get_final_status(test_id)


def _process_result(item, call, result) -> _TestOutcome:
    test_id = _get_test_id_from_item(item)

    has_exception = call.excinfo is not None

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
        return _TestOutcome()

    xfail = hasattr(result, "wasxfail") or "xfail" in result.keywords
    xfail_reason_tag = InternalTest.get_tag(test_id, XFAIL_REASON) if xfail else None
    has_skip_keyword = any(x in result.keywords for x in ["skip", "skipif", "skipped"])

    # If run with --runxfail flag, tests behave as if they were not marked with xfail,
    # that's why no XFAIL_REASON or test.RESULT tags will be added.
    if result.skipped:
        if InternalTest.was_skipped_by_itr(test_id):
            # Items that were skipped by ITR already have their status and reason set
            return _TestOutcome()

        if xfail and not has_skip_keyword:
            # XFail tests that fail are recorded skipped by pytest, should be passed instead
            if not item.config.option.runxfail:
                InternalTest.set_tag(test_id, test.RESULT, test.Status.XFAIL.value)
                if xfail_reason_tag is None:
                    InternalTest.set_tag(test_id, XFAIL_REASON, getattr(result, "wasxfail", "XFail"))
                return _TestOutcome(TestStatus.PASS)

        return _TestOutcome(TestStatus.SKIP, _extract_reason(call))

    if result.passed:
        if xfail and not has_skip_keyword and not item.config.option.runxfail:
            # XPass (strict=False) are recorded passed by pytest
            if xfail_reason_tag is None:
                InternalTest.set_tag(test_id, XFAIL_REASON, "XFail")
            InternalTest.set_tag(test_id, test.RESULT, test.Status.XPASS.value)

        return _TestOutcome(TestStatus.PASS)

    if xfail and not has_skip_keyword and not item.config.option.runxfail:
        # XPass (strict=True) are recorded failed by pytest, longrepr contains reason
        if xfail_reason_tag is None:
            InternalTest.set_tag(test_id, XFAIL_REASON, getattr(result, "longrepr", "XFail"))
        InternalTest.set_tag(test_id, test.RESULT, test.Status.XPASS.value)
        return _TestOutcome(TestStatus.FAIL)

    # NOTE: for EFD purposes, we need to know if the test failed during setup or teardown.
    if call.when == "setup" and result.failed:
        InternalTest.set_tag(test_id, "_dd.ci.efd_setup_failed", True)
    elif call.when == "teardown" and result.failed:
        InternalTest.set_tag(test_id, "_dd.ci.efd_teardown_failed", True)

    exc_info = TestExcInfo(call.excinfo.type, call.excinfo.value, call.excinfo.tb) if call.excinfo else None

    return _TestOutcome(status=TestStatus.FAIL, exc_info=exc_info)


def _pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo, outcome: pytest.TestReport) -> None:
    # When EFD retries are active, we do not want makereport to generate results, but we want to record exceptions
    # since they are not available in the output of runtest_protocol
    with core.context_with_data(f"pytest-efd-retry-{item.nodeid}") as ctx:
        if ctx.get_item("efd_retry_num") is not None:
            return

    original_result = outcome.get_result()

    test_id = _get_test_id_from_item(item)

    test_outcome = _process_result(item, call, original_result)

    # A None value for test_outcome.status implies the test has not finished yet
    # Only continue to finishing the test if the test has finished, or if tearing down the test
    if test_outcome.status is None and call.when != "teardown":
        return

    # Record a result if we haven't already recorded it:
    if not InternalTest.is_finished(test_id):
        InternalTest.finish(test_id, test_outcome.status, test_outcome.skip_reason, test_outcome.exc_info)

    # EFD retries tests only if their teardown succeeded to ensure the best chance they will succeed
    if InternalTest.efd_should_retry(test_id):
        # Don't mark the original test's call as failed if it will be EFD-retried to avoid failing the session
        # pytest_terminal_summary will then update the counts according to the test's final status
        if call.when == "call" and test_outcome.status == TestStatus.FAIL:
            original_result.outcome = _EFD_OUTCOMES.ATTEMPT_FAILED
            return
        if InternalTest.get_tag(test_id, "_dd.ci.efd_setup_failed"):
            log.debug("Test item %s failed during setup, will not be retried for Early Flake Detection")
            return
        if InternalTest.get_tag(test_id, "_dd.ci.efd_teardown_failed"):
            # NOTE: tests that passed their call but failed during teardown are not retried
            log.debug("Test item %s failed during teardown, will not be retried for Early Flake Detection")
            return

        original_result.outcome = _EFD_OUTCOMES.ATTEMPT_PASSED
        efd_outcome = _handle_efd_retries(item, test_outcome)
        final_outcomes = {
            EFDTestStatus.ALL_PASS: _EFD_OUTCOMES.FINAL_PASSED,
            EFDTestStatus.ALL_FAIL: _EFD_OUTCOMES.FINAL_FAILED,
            EFDTestStatus.ALL_SKIP: _EFD_OUTCOMES.FINAL_SKIPPED,
            EFDTestStatus.FLAKY: _EFD_OUTCOMES.FINAL_FLAKY,
        }
        final_report = pytest.TestReport(
            nodeid=item.nodeid,
            location=item.location,
            keywords=item.keywords,
            when="call",
            longrepr=None,
            outcome=final_outcomes[efd_outcome],
        )
        item.ihook.pytest_runtest_logreport(report=final_report)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> None:
    """Store outcome for tracing."""
    outcome: pytest.TestReport
    outcome = yield

    if not is_test_visibility_enabled():
        return

    try:
        return _pytest_runtest_makereport(item, call, outcome)
    except Exception:  # noqa: E722
        log.debug("encountered error during makereport", exc_info=True)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Report flaky or failed tests"""
    # Before yield gives us a chance to have short reports of failures:
    initial_size = len(terminalreporter.stats.get("failed", []))
    # Fill in attempt failures so the terminal reports them properly
    for failed_report in terminalreporter.getreports(_EFD_OUTCOMES.ATTEMPT_FAILED):
        failed_report.outcome = "failed"
        terminalreporter.stats.setdefault("failed", []).append(failed_report)

    yield

    # After yield gives us a chance to:
    # - print our flaky test status summary
    # - modify the total counts

    # Reset the failed attempts so they don't count towards the final total
    if initial_size == 0:
        terminalreporter.stats.pop("failed", None)
    else:
        terminalreporter.stats["failed"] = terminalreporter.stats["failed"][:initial_size]

    if InternalTestSession.efd_enabled():
        terminalreporter.write_sep("=", "Datadog Early Flake Detection", purple=True, bold=True)

    # Print summary info
    passed_reports = terminalreporter.getreports(_EFD_OUTCOMES.FINAL_PASSED)
    if passed_reports:
        terminalreporter.write_sep("_", "PASSED", green=True, bold=True)
        for passed_report in passed_reports:
            line = f"{terminalreporter._tw.markup('PASSED', green=True)} {passed_report.nodeid}"
            terminalreporter.write_line(line)
        del terminalreporter.stats[_EFD_OUTCOMES.FINAL_PASSED]

    failed_reports = terminalreporter.getreports(_EFD_OUTCOMES.FINAL_FAILED)
    if failed_reports:
        terminalreporter.write_sep("_", "FAILED", red=True, bold=True)
        for failed_report in failed_reports:
            line = f"{terminalreporter._tw.markup('FAILED', red=True)} {failed_report.nodeid}"
            terminalreporter.write_line(line)
        del terminalreporter.stats[_EFD_OUTCOMES.FINAL_FAILED]

    skipped_reports = terminalreporter.getreports(_EFD_OUTCOMES.FINAL_SKIPPED)
    if skipped_reports:
        terminalreporter.write_sep("_", "SKIPPED", yellow=True, bold=True)
        for skipped_report in skipped_reports:
            line = f"{terminalreporter._tw.markup('SKIPPED', yellow=True)} {skipped_report.nodeid}"
            terminalreporter.write_line(line)
        del terminalreporter.stats[_EFD_OUTCOMES.FINAL_SKIPPED]

    flaky_reports = terminalreporter.getreports(_EFD_FLAKY_OUTCOME)
    if flaky_reports:
        terminalreporter.write_sep("_", "FLAKY", yellow=True, bold=True)
        for flaky_report in flaky_reports:
            line = f"{terminalreporter._tw.markup('FLAKY', yellow=True)} {flaky_report.nodeid}"
            terminalreporter.write_line(line)

    # Recategorize failed, passed, and skipped into their final counts so they show up in the correct summary
    for status in ["failed", "passed", "skipped"]:
        reports = terminalreporter.stats.pop(f"dd_efd_attempt_{status}", [])
        if reports:
            terminalreporter.stats.setdefault(status, []).extend(reports)

    return


def _pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if not is_test_visibility_enabled():
        return

    if InternalTestSession.efd_has_failed_tests():
        session.exitstatus = pytest.ExitCode.TESTS_FAILED

    invoked_by_coverage_run_status = _is_coverage_invoked_by_coverage_run()
    pytest_cov_status = _is_pytest_cov_enabled(session.config)
    if _is_coverage_patched() and (pytest_cov_status or invoked_by_coverage_run_status):
        if invoked_by_coverage_run_status and not pytest_cov_status:
            run_coverage_report()

        lines_pct_value = _coverage_data.get(PCT_COVERED_KEY, None)
        if not isinstance(lines_pct_value, float):
            log.warning("Tried to add total covered percentage to session span but the format was unexpected")
        else:
            InternalTestSession.set_covered_lines_pct(lines_pct_value)

    if ModuleCodeCollector.is_installed():
        ModuleCodeCollector.uninstall()

    InternalTestSession.finish(force_finish_children=True)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if not is_test_visibility_enabled():
        return

    try:
        _pytest_sessionfinish(session, exitstatus)
    except Exception:  # noqa: E722
        log.debug("encountered error during session finish", exc_info=True)


def _efd_get_retry_num(nodeid) -> t.Optional[int]:
    with core.context_with_data(f"pytest-efd-retry-{nodeid}") as ctx:
        return ctx.get_item("efd_retry_num")


def _efd_get_attempt_string(nodeid) -> str:
    retry_number = _efd_get_retry_num(nodeid)
    return "ATTEMPT {} ".format(retry_number) if retry_number is not None else "INITIAL ATTEMPT "


def pytest_report_teststatus(
    report: pytest.TestReport,
) -> t.Optional[pytest.TestShortLogReport]:
    if report.outcome == _EFD_OUTCOMES.ATTEMPT_PASSED:
        return pytest.TestShortLogReport(
            _EFD_OUTCOMES.ATTEMPT_PASSED,
            "r",
            (f"EFD RETRY {_efd_get_attempt_string(report.nodeid)}PASSED", {"green": True}),
        )
    if report.outcome == _EFD_OUTCOMES.ATTEMPT_FAILED:
        return pytest.TestShortLogReport(
            _EFD_OUTCOMES.ATTEMPT_FAILED,
            "R",
            (f"EFD RETRY {_efd_get_attempt_string(report.nodeid)}FAILED", {"yellow": True}),
        )
    if report.outcome == _EFD_OUTCOMES.ATTEMPT_SKIPPED:
        return pytest.TestShortLogReport(
            _EFD_OUTCOMES.ATTEMPT_SKIPPED,
            "s",
            (f"EFD RETRY {_efd_get_attempt_string(report.nodeid)}SKIPPED", {"yellow": True}),
        )
    if report.outcome == _EFD_OUTCOMES.FINAL_PASSED:
        return pytest.TestShortLogReport(_EFD_OUTCOMES.FINAL_PASSED, ".", ("EFD FINAL STATUS: PASSED", {"green": True}))
    if report.outcome == _EFD_OUTCOMES.FINAL_FAILED:
        return pytest.TestShortLogReport(_EFD_OUTCOMES.FINAL_FAILED, "F", ("EFD FINAL STATUS: FAILED", {"red": True}))
    if report.outcome == _EFD_OUTCOMES.FINAL_SKIPPED:
        return pytest.TestShortLogReport(
            _EFD_OUTCOMES.FINAL_SKIPPED, "S", ("EFD FINAL STATUS: SKIPPED", {"yellow": True})
        )
    if report.outcome == _EFD_OUTCOMES.FINAL_FLAKY:
        # Flaky tests are the only one that have a pretty string because they are intended to be displayed in the final
        # count of terminal summary
        return pytest.TestShortLogReport(_EFD_FLAKY_OUTCOME, "K", ("EFD FINAL STATUS: FLAKY", {"yellow": True}))
    return None


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
