from __future__ import annotations

from collections import defaultdict
from io import StringIO
import json
import logging
import os
from pathlib import Path
import re
import traceback
import typing as t

from _pytest.runner import runtestprotocol
import pluggy
import pytest

from ddtrace.testing.internal.ci import CITag
from ddtrace.testing.internal.errors import SetupError
from ddtrace.testing.internal.git import get_workspace_path
from ddtrace.testing.internal.logging import catch_and_log_exceptions
from ddtrace.testing.internal.logging import setup_logging
from ddtrace.testing.internal.pytest.bdd import BddTestOptPlugin
from ddtrace.testing.internal.pytest.benchmark import BenchmarkData
from ddtrace.testing.internal.pytest.benchmark import get_benchmark_tags_and_metrics
from ddtrace.testing.internal.pytest.hookspecs import TestOptHooks
from ddtrace.testing.internal.pytest.report_links import print_test_report_links
from ddtrace.testing.internal.pytest.utils import item_to_test_ref
from ddtrace.testing.internal.retry_handlers import RetryHandler
from ddtrace.testing.internal.session_manager import SessionManager
from ddtrace.testing.internal.telemetry import TelemetryAPI
from ddtrace.testing.internal.test_data import Test
from ddtrace.testing.internal.test_data import TestModule
from ddtrace.testing.internal.test_data import TestRef
from ddtrace.testing.internal.test_data import TestRun
from ddtrace.testing.internal.test_data import TestSession
from ddtrace.testing.internal.test_data import TestStatus
from ddtrace.testing.internal.test_data import TestSuite
from ddtrace.testing.internal.test_data import TestTag
from ddtrace.testing.internal.tracer_api.context import enable_all_ddtrace_integrations
from ddtrace.testing.internal.tracer_api.context import install_global_trace_filter
from ddtrace.testing.internal.tracer_api.context import trace_context
from ddtrace.testing.internal.tracer_api.coverage import coverage_collection
from ddtrace.testing.internal.tracer_api.coverage import get_coverage_percentage
from ddtrace.testing.internal.tracer_api.coverage import install_coverage
from ddtrace.testing.internal.tracer_api.coverage import install_coverage_percentage
from ddtrace.testing.internal.tracer_api.coverage import uninstall_coverage_percentage
import ddtrace.testing.internal.tracer_api.pytest_hooks
from ddtrace.testing.internal.utils import TestContext
from ddtrace.testing.internal.utils import asbool


if t.TYPE_CHECKING:
    from _pytest.terminal import TerminalReporter


DISABLED_BY_TEST_MANAGEMENT_REASON = "Flaky test is disabled by Datadog"
SKIPPED_BY_ITR_REASON = "Skipped by Datadog Intelligent Test Runner"
ITR_UNSKIPPABLE_REASON = "datadog_itr_unskippable"

SESSION_MANAGER_STASH_KEY = pytest.StashKey[SessionManager]()

TEST_FRAMEWORK = "pytest"

log = logging.getLogger(__name__)


# The tuple pytest expects as the `longrepr` field of reports for failed or skipped tests.
_Longrepr = t.Tuple[
    # 1st field: pathname of the test file
    str,
    # 2nd field: line number.
    int,
    # 3rd field: skip reason.
    str,
]


# The tuple pytest expects as the output of the `pytest_report_teststatus` hook.
_ReportTestStatus = t.Tuple[
    # 1st field: the status category in which the test will be counted in the final stats (X passed, Y failed, etc).
    # Usually this is the same as report.outcome, but does not have to be! For example if a report has report.outcome =
    # "skipped" but `pytest_report_teststatus` returns "quarantined" as the first tuple item here, the test will be
    # counted as "quarantined" in the final stats.
    str,
    # 2nd field: the short (single-character) representation of the test status (e.g., "F" for failed tests).
    str,
    # 3rd field: the long representation of the test status (e.g., "FAILED" for failed tests). It can be either:
    t.Union[
        # - a simple string; or
        str,
        # - a tuple (text, properties_dict), where the properties_dict can contain properties such as {"blue": True}.
        #   These properties are also applied to the short representation.
        t.Tuple[str, t.Dict[str, bool]],
    ],
]
# The `pytest_report_teststatus` hook can return a tuple of empty strings ("", "", ""), in which case the test report is
# not logged at all. On the other hand, if the hook returns `None`, the next hook will be tried (so you can return
# `None` if you want the default pytest log output).

# The tuple stored in the `location` attribute of a `pytest.Item`
_Location = t.Tuple[
    str,  # 1st field: file name
    int,  # 2nd field: line number
    str,  # 3rd field: test name
]


def _get_module_path_from_item(item: pytest.Item) -> Path:
    try:
        item_path = getattr(item, "path", None)
        if item_path is not None:
            return item.path.absolute().parent
        return Path(item.module.__file__).absolute().parent
    except Exception:  # noqa: E722
        return Path.cwd()


class TestPhase:
    SETUP = "setup"
    CALL = "call"
    TEARDOWN = "teardown"
    __test__ = False


_ReportGroup = t.Dict[str, pytest.TestReport]


class TestOptPlugin:
    """
    pytest plugin for test optimization.
    """

    __test__ = False

    def __init__(self, session_manager: SessionManager) -> None:
        # DEV: If one day we become a separate package usable independently from ddtrace, installing the trace filter
        # can be made optional. For now, it has to always be enabled to ensure any non-test traces generated by ddtrace
        # during tests are captured by us (and not sent to the APM agent, for instance).
        self.enable_ddtrace_trace_filter = True

        self.enable_all_ddtrace_integrations = False
        self.reports_by_nodeid: t.Dict[str, _ReportGroup] = defaultdict(lambda: {})
        self.excinfo_by_report: t.Dict[pytest.TestReport, t.Optional[pytest.ExceptionInfo[t.Any]]] = {}
        self.benchmark_data_by_nodeid: t.Dict[str, BenchmarkData] = {}
        self.tests_by_nodeid: t.Dict[str, Test] = {}
        self.is_xdist_worker = False

        self.manager = session_manager
        self.session = self.manager.session

        self.extra_failed_reports: t.List[pytest.TestReport] = []

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        if xdist_worker_input := getattr(session.config, "workerinput", None):
            if session_id := xdist_worker_input.get("dd_session_id"):
                self.session.set_session_id(session_id)
                self.is_xdist_worker = True

        if session.config.getoption("ddtrace-patch-all"):
            self.enable_all_ddtrace_integrations = True

        self.session.start()
        self.manager.start()

        TelemetryAPI.get().record_session_created(
            test_framework=TEST_FRAMEWORK,
            has_codeowners=self.manager.has_codeowners(),
            is_unsupported_ci=(self.manager.env_tags.get(CITag.PROVIDER_NAME) is None),
        )

        if self.enable_ddtrace_trace_filter:
            install_global_trace_filter(self.manager.writer)

        if self.enable_all_ddtrace_integrations:
            enable_all_ddtrace_integrations()

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        # With xdist, the main process does not execute tests, so we cannot rely on the normal `session.get_status()`
        # behavior of determining the status based on the status of the children. Instead, we set the status manually
        # based on the exit status reported by pytest.
        self.session.set_status(
            TestStatus.FAIL if session.exitstatus == pytest.ExitCode.TESTS_FAILED else TestStatus.PASS
        )

        if self.is_xdist_worker and hasattr(session.config, "workeroutput"):
            # Propagate number of skipped tests to the main process.
            session.config.workeroutput["tests_skipped_by_itr"] = self.session.tests_skipped_by_itr

        coverage_percentage = get_coverage_percentage(_is_pytest_cov_enabled(session.config))
        if coverage_percentage is not None:
            self.session.metrics[TestTag.CODE_COVERAGE_LINES_PCT] = coverage_percentage
            uninstall_coverage_percentage()

        self.session.finish()

        TelemetryAPI.get().record_session_finished(
            test_framework=TEST_FRAMEWORK,
            has_codeowners=self.manager.has_codeowners(),
            is_unsupported_ci=(self.manager.env_tags.get(CITag.PROVIDER_NAME) is None),
            efd_abort_reason=self.session.get_early_flake_detection_abort_reason(),
        )

        if not self.is_xdist_worker:
            # When running with xdist, only the main process writes the session event.
            self.manager.writer.put_item(self.session)

        self.manager.finish()

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        """
        Discover modules, suites, and tests that have been selected by pytest.

        NOTE: Using pytest_collection_finish instead of pytest_collection_modifyitems allows us to capture only the
        tests that pytest has selection for run (eg: with the use of -k as an argument).
        """
        for item in session.items:
            test_ref = item_to_test_ref(item)
            test_module, test_suite, test = self._discover_test(item, test_ref)

        self.manager.finish_collection()

    def _discover_test(self, item: pytest.Item, test_ref: TestRef) -> t.Tuple[TestModule, TestSuite, Test]:
        """
        Return the module, suite and test objects for a given test item, creating them if necessary.
        """

        def _on_new_module(module: TestModule) -> None:
            module.set_location(module_path=_get_module_path_from_item(item))

        def _on_new_suite(suite: TestSuite) -> None:
            pass

        def _on_new_test(test: Test) -> None:
            path, start_line, _test_name = item.reportinfo()
            test.set_location(path=path, start_line=start_line or 0)

            if parameters := _get_test_parameters_json(item):
                test.set_parameters(parameters)

            if _is_test_unskippable(item):
                test.mark_unskippable()

            if custom_tags := _get_test_custom_tags(item):
                test.set_tags(custom_tags)

        return self.manager.discover_test(
            test_ref,
            on_new_module=_on_new_module,
            on_new_suite=_on_new_suite,
            on_new_test=_on_new_test,
        )

    @pytest.hookimpl(tryfirst=True, hookwrapper=True, specname="pytest_runtest_protocol")
    def pytest_runtest_protocol_wrapper(
        self, item: pytest.Item, nextitem: t.Optional[pytest.Item]
    ) -> t.Generator[None, None, None]:
        test_ref = item_to_test_ref(item)
        next_test_ref = item_to_test_ref(nextitem) if nextitem else None

        test_module, test_suite, test = self._discover_test(item, test_ref)

        if not test_module.is_started():
            test_module.start()
            TelemetryAPI.get().record_module_created(test_framework=TEST_FRAMEWORK)

        if not test_suite.is_started():
            test_suite.start()
            TelemetryAPI.get().record_suite_created(test_framework=TEST_FRAMEWORK)

        test.start()

        self.tests_by_nodeid[item.nodeid] = test

        self._handle_itr(item, test_ref, test)

        if test.is_disabled() and not test.is_attempt_to_fix():
            item.add_marker(pytest.mark.skip(reason=DISABLED_BY_TEST_MANAGEMENT_REASON))
        elif test.is_quarantined() or (test.is_disabled() and test.is_attempt_to_fix()):
            # A test that is disabled and attempt-to-fix will run, but a failure does not break the pipeline (i.e., it
            # is effectively quarantined). We may want to present it in a different way in the output though.
            item.user_properties += [("dd_quarantined", True)]

        with trace_context(self.enable_ddtrace_trace_filter) as context:
            TelemetryAPI.get().record_coverage_started(test_framework=TEST_FRAMEWORK, coverage_library="ddtrace")
            with coverage_collection() as coverage_data:
                yield
            TelemetryAPI.get().record_coverage_finished(test_framework=TEST_FRAMEWORK, coverage_library="ddtrace")

        if not test.test_runs:
            # No test runs: our pytest_runtest_protocol did not run. This can happen if some other plugin (such as
            # `flaky` or `rerunfailures`) did it instead, or if there is a user-defined `pytest_runtest_protocol` in
            # `conftest.py`. In this case, we create a test run now with the test results of the plugin run as a
            # fallback, but we are unable to do retries in this case.
            log.debug(
                "Test Optimization pytest_runtest_protocol did not run for %s; "
                "perhaps some plugin or conftest.py has overridden it",
                item.nodeid,
            )
            test_run = test.make_test_run()
            test_run.start(start_ns=test.start_ns)
            self._set_test_run_data(test_run, item, context)
            test_run.finish()
            test.set_status(test_run.get_status())
            self.manager.writer.put_item(test_run)

        test.finish()

        self.manager.coverage_writer.put_coverage(
            test.last_test_run, coverage_data.get_coverage_bitmaps(relative_to=self.manager.workspace_path)
        )

        if not next_test_ref or test_ref.suite != next_test_ref.suite:
            test_suite.finish()
            self.manager.writer.put_item(test_suite)
            TelemetryAPI.get().record_suite_finished(test_framework=TEST_FRAMEWORK)

        if not next_test_ref or test_ref.suite.module != next_test_ref.suite.module:
            test_module.finish()
            self.manager.writer.put_item(test_module)
            TelemetryAPI.get().record_module_finished(test_framework=TEST_FRAMEWORK)

    @catch_and_log_exceptions()
    def pytest_runtest_protocol(self, item: pytest.Item, nextitem: t.Optional[pytest.Item]) -> bool:
        item.ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
        self._do_test_runs(item, nextitem)
        item.ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
        return True  # Do not run other pytest_runtest_protocol hooks after this one.

    def _do_one_test_run(
        self, item: pytest.Item, nextitem: t.Optional[pytest.Item], context: TestContext
    ) -> t.Tuple[TestRun, _ReportGroup]:
        test = self.tests_by_nodeid[item.nodeid]
        test_run = test.make_test_run()
        test_run.start()

        TelemetryAPI.get().record_test_created(test_framework=TEST_FRAMEWORK, test_run=test_run)

        reports = _make_reports_dict(runtestprotocol(item, nextitem=nextitem, log=False))
        self._set_test_run_data(test_run, item, context)

        TelemetryAPI.get().record_test_finished(
            test_framework=TEST_FRAMEWORK,
            test_run=test_run,
            ci_provider_name=self.manager.env_tags.get(CITag.PROVIDER_NAME),
            is_auto_injected=self.manager.is_auto_injected,
        )

        return test_run, reports

    def _do_test_runs(self, item: pytest.Item, nextitem: t.Optional[pytest.Item]) -> None:
        test = self.tests_by_nodeid[item.nodeid]
        retry_handler = self._check_applicable_retry_handlers(test)

        with trace_context(self.enable_ddtrace_trace_filter) as context:
            test_run, reports = self._do_one_test_run(item, nextitem, context)

        if not test.is_skipped_by_itr() and retry_handler and retry_handler.should_retry(test):
            self._do_retries(item, nextitem, test, retry_handler, reports)
        else:
            if test.is_quarantined() or test.is_disabled():
                self._mark_quarantined_test_report_group_as_skipped(item, reports)
            self._log_test_reports(item, reports)
            test_run.finish()
            test.set_status(test_run.get_status())
            self.manager.writer.put_item(test_run)

    def _set_test_run_data(self, test_run: TestRun, item: pytest.Item, context: TestContext) -> None:
        status, tags = self._get_test_outcome(item.nodeid)
        test_run.set_status(status)
        test_run.set_tags(tags)
        test_run.set_context(context)

        if benchmark_data := self.benchmark_data_by_nodeid.pop(item.nodeid, None):
            test_run.set_tags(benchmark_data.tags)
            test_run.set_metrics(benchmark_data.metrics)
            test_run.mark_benchmark()

    def _do_retries(
        self,
        item: pytest.Item,
        nextitem: t.Optional[pytest.Item],
        test: Test,
        retry_handler: RetryHandler,
        reports: _ReportGroup,
    ) -> None:
        retry_reports = RetryReports()

        # Log initial attempt.
        self._mark_test_reports_as_retry(reports, retry_handler)
        retry_reports.log_test_report(item, reports, TestPhase.SETUP)
        # The call report may not exist if setup failed or skipped.
        retry_reports.log_test_report(item, reports, TestPhase.CALL)

        test_run = test.last_test_run
        retry_handler.set_tags_for_test_run(test_run)
        test_run.finish()

        should_retry = True

        while should_retry:
            with trace_context(self.enable_ddtrace_trace_filter) as context:
                test_run, reports = self._do_one_test_run(item, nextitem, context)

            should_retry = retry_handler.should_retry(test)
            retry_handler.set_tags_for_test_run(test_run)
            self._mark_test_reports_as_retry(reports, retry_handler)

            # Even though we run setup, call, teardown for each retry, we only log _one_ report per retry, as various
            # pytest plugins generally expect only one teardown to be logged for a test (as they run test finish actions
            # on teardown). If the call report is available, we log that, otherwise we log the setup report. (Logging
            # multiple setups for a test does not seem to cause an issue with junitxml, at least.)
            if not retry_reports.log_test_report(item, reports, TestPhase.CALL):
                retry_reports.log_test_report(item, reports, TestPhase.SETUP)

            test_run.finish()

        final_status = retry_handler.get_final_status(test)
        test.set_status(final_status)

        for test_run in test.test_runs:
            self.manager.writer.put_item(test_run)

        # Log final status.
        final_report = retry_reports.make_final_report(test, item, final_status)

        if extra_failed_report := retry_reports.get_extra_failed_report(test, final_status):
            self.extra_failed_reports.append(extra_failed_report)

        if test.is_quarantined() or test.is_disabled():
            self._mark_quarantined_test_report_as_skipped(item, final_report)

        item.ihook.pytest_runtest_logreport(report=final_report)

        # Log teardown. There should be just one teardown logged for all of the retries, because the junitxml plugin
        # closes the <testcase> element when teardown is logged.
        teardown_report = reports.get(TestPhase.TEARDOWN)
        if test.is_quarantined() or test.is_disabled():
            self._mark_quarantined_test_report_as_skipped(item, teardown_report)
        item.ihook.pytest_runtest_logreport(report=teardown_report)

    def _check_applicable_retry_handlers(self, test: Test) -> t.Optional[RetryHandler]:
        for handler in self.manager.retry_handlers:
            if handler.should_apply(test):
                return handler

        return None

    def _extract_longrepr(self, reports: _ReportGroup) -> t.Tuple[t.Any, t.Any]:
        """
        Extract the most relevant report `longrepr` for a report group.

        Errors that happened during the call phase have more useful information, so we try to use that if available.

        Also return the corresponding `wasxfail` attribute if present, for correct indication of xfail/xpass tests.
        """
        for when in (TestPhase.CALL, TestPhase.SETUP, TestPhase.TEARDOWN):
            if report := reports.get(when):
                if report.longrepr:
                    return report.longrepr, getattr(report, "wasxfail", None)

        return None, None

    def _mark_test_reports_as_retry(self, reports: _ReportGroup, retry_handler: RetryHandler) -> None:
        if not self._mark_test_report_as_retry(reports, retry_handler, TestPhase.CALL):
            self._mark_test_report_as_retry(reports, retry_handler, TestPhase.SETUP)

    def _mark_quarantined_test_report_as_skipped(
        self, item: pytest.Item, report: t.Optional[pytest.TestReport]
    ) -> None:
        """
        Modify a test report for a quarantined test to make it look like it was skipped.
        """
        # For junitxml, probably the least confusing way to report a quarantined test is as skipped.
        # In `pytest_runtest_logreport`, we can still identify the test as quarantined via the `dd_quarantined`
        # user property.
        if report is None:
            return

        if report.when == TestPhase.TEARDOWN:
            report.outcome = "passed"
        else:
            # TODO: distinguish quarantine vs disabled
            line_number = item.location[1] or 0
            longrepr: _Longrepr = (str(item.path), line_number, "Quarantined")
            report.longrepr = longrepr
            report.outcome = "skipped"

    def _mark_quarantined_test_report_group_as_skipped(self, item: pytest.Item, reports: _ReportGroup) -> None:
        """
        Modify the test reports for a quarantined test to make it look like it was skipped.
        """
        if call_report := reports.get(TestPhase.CALL):
            self._mark_quarantined_test_report_as_skipped(item, call_report)
            reports[TestPhase.SETUP].outcome = "passed"
            reports[TestPhase.TEARDOWN].outcome = "passed"
        else:
            setup_report = reports.get(TestPhase.SETUP)
            self._mark_quarantined_test_report_as_skipped(item, setup_report)
            reports[TestPhase.TEARDOWN].outcome = "passed"

    def _mark_test_report_as_retry(self, reports: _ReportGroup, retry_handler: RetryHandler, when: str) -> bool:
        if call_report := reports.get(when):
            call_report.user_properties += [
                ("dd_retry_outcome", call_report.outcome),
                ("dd_retry_reason", retry_handler.get_pretty_name()),
            ]
            call_report.outcome = "dd_retry"
            return True

        return False

    def _log_test_reports(self, item: pytest.Item, reports: _ReportGroup) -> None:
        for when in (TestPhase.SETUP, TestPhase.CALL, TestPhase.TEARDOWN):
            if report := reports.get(when):
                item.ihook.pytest_runtest_logreport(report=report)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(
        self, item: pytest.Item, call: pytest.CallInfo[t.Any]
    ) -> t.Generator[None, pluggy.Result[t.Any], None]:
        """
        Save report and exception information for later use.
        """
        outcome = yield
        report: pytest.TestReport = outcome.get_result()
        self.reports_by_nodeid[item.nodeid][call.when] = report
        self.excinfo_by_report[report] = call.excinfo

        if call.when == TestPhase.TEARDOWN:
            # We need to extract pytest-benchmark data _before_ the fixture teardown.
            if benchmark_data := get_benchmark_tags_and_metrics(item):
                self.benchmark_data_by_nodeid[item.nodeid] = benchmark_data

    def pytest_report_teststatus(self, report: pytest.TestReport) -> t.Optional[_ReportTestStatus]:
        if retry_outcome := _get_user_property(report, "dd_retry_outcome"):
            retry_reason = _get_user_property(report, "dd_retry_reason")
            return ("dd_retry", "R", f"RETRY {retry_outcome.upper()} ({retry_reason})")

        if _get_user_property(report, "dd_quarantined"):
            if report.when == TestPhase.TEARDOWN:
                return ("quarantined", "Q", ("QUARANTINED", {"blue": True}))
            else:
                return ("", "", "")

        if _get_user_property(report, "dd_flaky"):
            return ("flaky", "K", ("FLAKY", {"yellow": True}))

        return None

    def _get_test_outcome(self, nodeid: str) -> t.Tuple[TestStatus, t.Dict[str, str]]:
        """
        Return test status and tags with exception/skip information for a given executed test.

        This methods consumes the test reports and exception information for the specified test, and removes them from
        the dictionaries.
        """
        status = TestStatus.PASS
        tags = {}

        reports_dict = self.reports_by_nodeid.pop(nodeid, {})

        for phase in (TestPhase.SETUP, TestPhase.CALL, TestPhase.TEARDOWN):
            report = reports_dict.get(phase)
            if not report:
                continue

            if wasxfail := getattr(report, "wasxfail", None):
                tags[TestTag.XFAIL_REASON] = str(wasxfail)
                tags[TestTag.TEST_RESULT] = "xpass" if report.passed else "xfail"

            excinfo = self.excinfo_by_report.pop(report, None)

            if report.failed:
                status = TestStatus.FAIL
                tags.update(_get_exception_tags(excinfo))
                break

            if report.skipped:
                status = TestStatus.SKIP
                reason = str(excinfo.value) if excinfo else "Unknown skip reason"
                tags[TestTag.SKIP_REASON] = reason
                break

        return status, tags

    def _handle_itr(self, item: pytest.Item, test_ref: TestRef, test: Test) -> None:
        if not self.manager.is_skippable_test(test_ref):
            return

        if test.is_unskippable():
            test.mark_forced_run()
            return

        if test.is_attempt_to_fix():
            # if the test is an attempt-to-fix, behave as it if were not selected for skipping.
            return

        item.add_marker(pytest.mark.skip(reason=SKIPPED_BY_ITR_REASON))
        test.mark_skipped_by_itr()

    @pytest.hookimpl(tryfirst=True, hookwrapper=True)
    def pytest_terminal_summary(
        self, terminalreporter: TerminalReporter, exitstatus: int, config: pytest.Config
    ) -> t.Generator[None, None, None]:
        """
        Modify terminal summary before letting pytest emit it.

        During the test session, all retry attempt reports are logged with a 'dd_retry' category. We remove this
        category here so it doesn't show up in the final stat counts.

        To make the extra failed reports collected during retries (see `get_extra_failed_report` for details) show up
        with the rest of the failure exception reports, we modify them to look like normal failures, and append them to
        the failed reports. After they have been shown by pytest, we undo the change so tha the final count of failed
        tests is not affected.
        """
        # Do not show dd_retry in final stats.
        terminalreporter.stats.pop("dd_retry", None)

        original_failed_reports = terminalreporter.stats.get("failed", [])

        # Make extra failed reports look like normal failed reports.
        for report in self.extra_failed_reports:
            report.outcome = "failed"
            report.user_properties = [(k, v) for (k, v) in report.user_properties if k != "dd_retry_outcome"]

        terminalreporter.stats["failed"] = original_failed_reports + self.extra_failed_reports

        yield

        terminalreporter.stats["failed"] = original_failed_reports
        if not terminalreporter.stats["failed"]:
            del terminalreporter.stats["failed"]

        print_test_report_links(terminalreporter, self.manager)


class RetryReports:
    """
    Collect and manage reports for the retries of a single test.
    """

    def __init__(self):
        self.reports_by_outcome = defaultdict(lambda: [])

    def log_test_report(self, item: pytest.Item, reports: _ReportGroup, when: str) -> bool:
        """
        Collect and log the test report for a given test phase, if it exists.

        Returns True if the report exists, and False if not.
        Tests that fail or skip during setup do not have the call phase report.
        """
        if report := reports.get(when):
            item.ihook.pytest_runtest_logreport(report=report)
            outcome = _get_user_property(report, "dd_retry_outcome") or report.outcome
            self.reports_by_outcome[outcome].append(report)
            return True

        return False

    def make_final_report(self, test: Test, item: pytest.Item, final_status: TestStatus) -> pytest.TestReport:
        """
        Make the final report for the retries of a single test.

        The final status is provided by the retry handler. We assume that for a test to have a given outcome, at least
        one attempt must have had that outcome (e.g., for a test to have a 'failed' outcome, at least one retry must
        have failed).

        The attributes of the final report will be copied from the first retry report that had the same outcome. For
        instance, if the final report outcome is 'failed', it will have the same `longrepr` and `wasxfail` features as
        the first failed report.

        Additionally, if a test is marked as flaky (e.g., it both passed and failed during EFD), the final report will
        contain the `dd_flaky` user property. This allows us to identify the test as 'flaky' during logging, even though
        the final outcome is still one of 'passed', 'failed', or 'skipped'.
        """

        outcomes = {
            TestStatus.PASS: "passed",
            TestStatus.FAIL: "failed",
            TestStatus.SKIP: "skipped",
        }

        outcome = outcomes.get(final_status, str(final_status))

        try:
            source_report = self.reports_by_outcome[outcome][0]
            longrepr = source_report.longrepr
            wasxfail = getattr(source_report, "wasxfail", None)
        except IndexError:
            log.warning("Test %s has final outcome %r, but no retry had this outcome; this should never happen", test)
            longrepr = None
            wasxfail = None

        extra_user_properties = []
        if test.is_flaky_run():
            extra_user_properties += [("dd_flaky", True)]

        final_report = pytest.TestReport(
            nodeid=item.nodeid,
            location=item.location,
            keywords={k: 1 for k in item.keywords},
            when=TestPhase.CALL,
            longrepr=longrepr,
            outcome=outcome,
            user_properties=item.user_properties + extra_user_properties,
        )
        if wasxfail is not None:
            setattr(final_report, "wasxfail", wasxfail)

        return final_report

    def get_extra_failed_report(self, test: Test, final_status: TestStatus) -> t.Optional[pytest.TestReport]:
        """
        Get an extra failed report to log at the end of the test session.

        If a test is retried and all retries failed, the final report will have a 'failed' status and contain the
        `longrepr` of one of the failures, and so the failure exception will be automatically logged at the end of the
        test session by pytest. In this case, this function returns None.

        But if some retries pass and others fail, the test will have a 'passed' final status, and no failure exception
        will be logged for the test. In this case, this function returns one of the failed reports, which is saved for
        logging at the end of the test session. This ensures we provide some failure log to the user.

        If the test is quarantined or disabled, and not attempt-to-fix, the failed report is not returned. If the test
        is attempt-to-fix, the failed report is returned, even if the test is quarantined or disabled: if the user is
        attempting to fix the test, and the attempt fails, we need to provide some feedback on the failure.

        Note that we only report _one_ failure per test (either the one embedded in the 'failed' final report, or the
        one retured by this function), even if the test failed multiple times. This is to avoid spamming the test output
        with multiple copies of the same error.
        """
        suppress_errors = (test.is_quarantined() or test.is_disabled()) and not test.is_attempt_to_fix()
        if suppress_errors:
            return None

        if self.reports_by_outcome["failed"] and final_status != TestStatus.FAIL:
            return self.reports_by_outcome["failed"][0]

        return None


class XdistTestOptPlugin:
    def __init__(self, main_plugin: TestOptPlugin) -> None:
        self.main_plugin = main_plugin

    @pytest.hookimpl
    def pytest_configure_node(self, node: t.Any) -> None:
        """
        Pass test session id from the main process to xdist workers.
        """
        node.workerinput["dd_session_id"] = self.main_plugin.session.item_id

    @pytest.hookimpl
    def pytest_testnodedown(self, node: t.Any, error: t.Any) -> None:
        """
        Collect count of tests skipped by ITR from a worker node and add it to the main process' session.
        """
        if not hasattr(node, "workeroutput"):
            return

        if tests_skipped_by_itr := node.workeroutput.get("tests_skipped_by_itr"):
            self.main_plugin.session.tests_skipped_by_itr += tests_skipped_by_itr


def _make_reports_dict(reports: t.List[pytest.TestReport]) -> _ReportGroup:
    return {report.when: report for report in reports}


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add ddtrace options."""
    group = parser.getgroup("ddtrace")

    group.addoption(
        "--ddtrace",
        action="store_true",
        dest="ddtrace",
        default=False,
        help="Enable Datadog Test Optimization",
    )

    group.addoption(
        "--no-ddtrace",
        action="store_true",
        dest="no-ddtrace",
        default=False,
        help="Disable Datadog Test Optimization (overrides --ddtrace)",
    )

    group.addoption(
        "--ddtrace-patch-all",
        action="store_true",
        dest="ddtrace-patch-all",
        default=False,
        help="Enable all integrations with ddtrace",
    )

    parser.addini("ddtrace", "Enable Datadog Test Optimization", type="bool")
    parser.addini("no-ddtrace", "Disable Datadog Test Optimization (overrides 'ddtrace')", type="bool")
    parser.addini("ddtrace-patch-all", "Enable all integrations with ddtrace", type="bool")

    ddtrace.testing.internal.tracer_api.pytest_hooks.pytest_addoption(parser)


def _is_test_optimization_disabled_by_kill_switch() -> bool:
    return not asbool(os.environ.get("DD_CIVISIBILITY_ENABLED", "true"))


def _is_enabled_early(early_config: pytest.Config, args: t.List[str]) -> bool:
    if _is_test_optimization_disabled_by_kill_switch():
        return False

    if _is_option_true("no-ddtrace", early_config, args):
        return False

    return _is_option_true("ddtrace", early_config, args)


def _is_option_true(option: str, early_config: pytest.Config, args: t.List[str]) -> bool:
    return early_config.getoption(option) or early_config.getini(option) or f"--{option}" in args


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_load_initial_conftests(
    early_config: pytest.Config, parser: pytest.Parser, args: t.List[str]
) -> t.Generator[None, None, None]:
    if not _is_enabled_early(early_config, args):
        yield
        return

    setup_logging()

    session = TestSession(name=TEST_FRAMEWORK)
    session.set_attributes(
        test_command=_get_test_command(early_config),
        test_framework=TEST_FRAMEWORK,
        test_framework_version=pytest.__version__,
    )

    try:
        session_manager = SessionManager(session=session)
    except SetupError as e:
        log.error("%s", e)
        yield
        return

    early_config.stash[SESSION_MANAGER_STASH_KEY] = session_manager

    if session_manager.settings.coverage_enabled:
        setup_coverage_collection()

    yield


def setup_coverage_collection() -> None:
    workspace_path = get_workspace_path()
    install_coverage(workspace_path)


def pytest_configure(config: pytest.Config) -> None:
    # We register the marker even if the kill switch is on, to avoid "Unknown pytest.mark.dd_tags" warnings in tests
    # when the plugin is disabled. This is similar to command line arguments, which are also registered in
    # `pytest_addoption` regardless of the kill switch, to avoid breaking pytest invocations using --ddtrace and other
    # options.
    config.addinivalue_line("markers", "dd_tags(**kwargs): add tags to current span")

    if _is_test_optimization_disabled_by_kill_switch():
        return

    session_manager = config.stash.get(SESSION_MANAGER_STASH_KEY, None)
    if not session_manager:
        log.debug("Session manager not initialized (plugin was not enabled)")
        return

    try:
        plugin = TestOptPlugin(session_manager=session_manager)
    except Exception:
        log.exception("Error setting up Test Optimization plugin")
        return

    config.pluginmanager.register(plugin)
    config.pluginmanager.add_hookspecs(TestOptHooks)

    if config.pluginmanager.hasplugin("xdist"):
        config.pluginmanager.register(XdistTestOptPlugin(plugin))

    if config.pluginmanager.hasplugin("pytest-bdd"):
        config.pluginmanager.register(BddTestOptPlugin(plugin))

    ddtrace.testing.internal.tracer_api.pytest_hooks.pytest_configure(config)

    if _is_pytest_cov_enabled(config):
        install_coverage_percentage()


def _get_test_command(config: pytest.Config) -> str:
    """Extract and re-create pytest session command from pytest config."""
    command = "pytest"
    if invocation_params := getattr(config, "invocation_params", None):
        command += " {}".format(" ".join(invocation_params.args))
    if addopts := os.environ.get("PYTEST_ADDOPTS"):
        command += " {}".format(addopts)
    return command


def _get_exception_tags(excinfo: t.Optional[pytest.ExceptionInfo[t.Any]]) -> t.Dict[str, str]:
    if excinfo is None:
        return {}

    max_entries = 30
    buf = StringIO()
    # TODO: handle MAX_SPAN_META_VALUE_LEN
    traceback.print_exception(excinfo.type, excinfo.value, excinfo.tb, limit=-max_entries, file=buf)

    return {
        TestTag.ERROR_STACK: buf.getvalue(),
        TestTag.ERROR_TYPE: "%s.%s" % (excinfo.type.__module__, excinfo.type.__name__),
        TestTag.ERROR_MESSAGE: str(excinfo.value),
    }


def _get_user_property(report: pytest.TestReport, user_property: str) -> t.Optional[t.Any]:
    user_properties = getattr(report, "user_properties", [])  # pytest.CollectReport does not have `user_properties`.

    for key, value in user_properties:
        if key == user_property:
            return value

    return None


def _get_test_parameters_json(item: pytest.Item) -> t.Optional[str]:
    callspec: t.Optional[pytest.python.CallSpec2] = getattr(item, "callspec", None)

    if callspec is None:
        return None

    parameters: t.Dict[str, t.Dict[str, str]] = {"arguments": {}, "metadata": {}}
    for param_name, param_val in item.callspec.params.items():
        try:
            parameters["arguments"][param_name] = _encode_test_parameter(param_val)
        except Exception:
            parameters["arguments"][param_name] = "Could not encode"
            log.warning("Failed to encode %r", param_name, exc_info=True)

    try:
        return json.dumps(parameters, sort_keys=True)
    except TypeError:
        log.warning("Failed to serialize parameters for test %s", item, exc_info=True)
        return None


def _encode_test_parameter(parameter: t.Any) -> str:
    param_repr = repr(parameter)
    # if the representation includes an id() we'll remove it
    # because it isn't constant across executions
    return re.sub(r" at 0[xX][0-9a-fA-F]+", "", param_repr)


def _get_skipif_condition(marker: pytest.Mark) -> t.Any:
    # DEV: pytest allows the condition to be a string to be evaluated. We currently don't support this.
    if marker.args:
        condition = marker.args[0]
    elif marker.kwargs:
        condition = marker.kwargs.get("condition")
    else:
        condition = True  # `skipif` with no condition is equivalent to plain `skip`.

    return condition


def _is_test_unskippable(item: pytest.Item) -> bool:
    return any(
        (_get_skipif_condition(marker) is False and marker.kwargs.get("reason") == ITR_UNSKIPPABLE_REASON)
        for marker in item.iter_markers(name="skipif")
    )


def _get_test_custom_tags(item: pytest.Item) -> t.Dict[str, str]:
    tags: t.Dict[str, str] = {}

    for marker in item.iter_markers(name="dd_tags"):
        for key, value in marker.kwargs.items():
            tags[key] = str(value)

    return tags


def _is_pytest_cov_enabled(config) -> bool:
    if not config.pluginmanager.get_plugin("pytest_cov"):
        return False
    cov_option = config.getoption("--cov", default=False)
    nocov_option = config.getoption("--no-cov", default=False)
    if nocov_option is True:
        return False
    if isinstance(cov_option, list) and cov_option == [True] and not nocov_option:
        return True
    return cov_option


@pytest.fixture(scope="session")
def ddtracer():
    """Return the current tracer instance."""
    import ddtrace

    return ddtrace.tracer


@pytest.fixture(scope="function")
def ddspan(ddtracer):
    """Return the current root span."""
    if ddtracer is None:
        return None

    return ddtracer.current_root_span()
