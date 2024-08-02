from pathlib import Path
import re

import pytest

import ddtrace
from ddtrace.contrib.coverage import patch as patch_coverage
from ddtrace.contrib.coverage.constants import PCT_COVERED_KEY
from ddtrace.contrib.coverage.data import _coverage_data
from ddtrace.contrib.coverage.patch import run_coverage_report
from ddtrace.contrib.coverage.utils import _is_coverage_invoked_by_coverage_run
from ddtrace.contrib.coverage.utils import _is_coverage_patched
from ddtrace.contrib.pytest._plugin_v1 import _extract_reason
from ddtrace.contrib.pytest._plugin_v1 import _is_pytest_cov_enabled
from ddtrace.contrib.pytest.constants import FRAMEWORK
from ddtrace.contrib.pytest.constants import XFAIL_REASON
from ddtrace.contrib.pytest.plugin import is_enabled
from ddtrace.contrib.pytest.utils import _get_names_from_item
from ddtrace.contrib.pytest.utils import _get_session_command
from ddtrace.contrib.pytest.utils import _get_session_id
from ddtrace.contrib.pytest.utils import _get_test_id_from_item
from ddtrace.contrib.unittest import unpatch as unpatch_unittest
from ddtrace.ext import test
from ddtrace.ext.ci_visibility.api import CIExcInfo
from ddtrace.ext.ci_visibility.api import CIModule
from ddtrace.ext.ci_visibility.api import CISession
from ddtrace.ext.ci_visibility.api import CISuite
from ddtrace.ext.ci_visibility.api import CITest
from ddtrace.internal.ci_visibility import CIVisibility
from ddtrace.internal.ci_visibility.constants import SKIPPED_BY_ITR_REASON
from ddtrace.internal.ci_visibility.utils import take_over_logger_stream_handler
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


_NODEID_REGEX = re.compile("^((?P<module>.*)/(?P<suite>[^/]*?))::(?P<name>.*?)$")


class _PytestDDTracePluginV2:
    """Plugin hooks (and some convenience methods) for the pytest plugin

    This plugin leverages the external CI Visibility API which behaves in a stateless way, where IDs are passed to the
    API calls, so each method is independent of all others, and may re-compute item ids.

    Methods are ordered in this class mostly in the order they are called during a test run.
    """

    @staticmethod
    def pytest_configure(config: pytest.Config) -> None:
        unpatch_unittest()
        # config.addinivalue_line("markers", "dd_tags(**kwargs): add tags to current span")
        if is_enabled(config):
            take_over_logger_stream_handler()
            CIVisibility.enable(config=ddtrace.config.pytest)
        if _is_pytest_cov_enabled(config):
            patch_coverage()

    @staticmethod
    def pytest_sessionstart(session: pytest.Session) -> None:
        if not CIVisibility.enabled:
            return
        log.debug("CI Visibility enabled - starting test session")

        session_id = _get_session_id(session)
        command = _get_session_command(session)

        CISession.discover(
            session_id,
            test_command=command,
            test_framework=FRAMEWORK,
            test_framework_version=pytest.__version__,
            session_operation_name="pytest.test_session",
            module_operation_name="pytest.test_module",
            suite_operation_name="pytest.test_suite",
            test_operation_name="pytest.test",
            reject_duplicates=False,
            reject_unknown_items=True,
            root_dir=Path(CIVisibility.get_workspace_path() or session.config.rootpath),  # TODO rootdir for pytest <6.1
        )

        CISession.start(session_id)

    @staticmethod
    def pytest_collection_modifyitems(session, config, items):
        if not CIVisibility.enabled:
            return

        for item in items:
            test_id = _get_test_id_from_item(item)
            suite_id = test_id.parent_id
            module_id = suite_id.parent_id

            # TODO: don't rediscover modules and suites if already discovered
            CIModule.discover(module_id)
            CISuite.discover(suite_id)

            CITest.discover(test_id)

            # If ITR test skipping is not enabled, no need to follow the rest of the tests
            if not CIVisibility.test_skipping_enabled():
                continue

            # TODO (obviously) support test skipping

    @staticmethod
    @pytest.hookimpl(tryfirst=True, hookwrapper=True)
    def pytest_runtest_protocol(item, nextitem):
        if not CIVisibility.enabled:
            yield
            return

        test_id = _get_test_id_from_item(item)
        suite_id = test_id.parent_id
        module_id = suite_id.parent_id

        CIModule.start(module_id)
        CISuite.start(suite_id)
        CITest.start(test_id)

        yield

        # We rely on the CI Visibility product to not finish items that have discovered but unfinished items
        CISuite.finish(suite_id)
        CIModule.finish(module_id)

        return

    @staticmethod
    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(item, call):
        """Store outcome for tracing."""
        outcome = yield

        if not CIVisibility.enabled:
            return

        test_id = _get_test_id_from_item(item)
        suite_id = test_id.parent_id

        # Setup and teardown only impact results if an exception occurred
        is_setup_or_teardown = call.when == "setup" or call.when == "teardown"
        has_exception = call.excinfo is not None

        if is_setup_or_teardown and not has_exception:
            return

        result = outcome.get_result()
        xfail = hasattr(result, "wasxfail") or "xfail" in result.keywords
        has_skip_keyword = any(x in result.keywords for x in ["skip", "skipif", "skipped"])

        # If run with --runxfail flag, tests behave as if they were not marked with xfail,
        # that's why no XFAIL_REASON or test.RESULT tags will be added.
        if result.skipped:
            if xfail and not has_skip_keyword:
                # XFail tests that fail are recorded skipped by pytest, should be passed instead
                if not item.config.option.runxfail:
                    CITest.set_tag(test_id, test.RESULT, test.Status.XFAIL.value)
                    CITest.set_tag(test_id, XFAIL_REASON, getattr(result, "wasxfail", "XFail"))
                    CITest.mark_pass(test_id)
                    return

            reason = _extract_reason(call)
            if reason is not None:
                if str(reason) == SKIPPED_BY_ITR_REASON:
                    CITest.mark_itr_skipped(test_id)
                    if CIVisibility._instance._suite_skipping_mode:
                        CISuite.mark_itr_skipped(suite_id)
                    return

            CITest.mark_skip(test_id, reason)
            return

        if result.passed:
            if xfail and not has_skip_keyword and not item.config.option.runxfail:
                # XPass (strict=False) are recorded passed by pytest
                CITest.set_tag(test_id, XFAIL_REASON, getattr(result, "wasxfail", "XFail"))
                CITest.set_tag(test_id, test.RESULT, test.Status.XPASS.value)

            CITest.mark_pass(test_id)
            return

        if xfail and not has_skip_keyword and not item.config.option.runxfail:
            # XPass (strict=True) are recorded failed by pytest, longrepr contains reason
            CITest.set_tag(test_id, XFAIL_REASON, getattr(result, "longrepr", "XFail"))
            CITest.set_tag(test_id, test.RESULT, test.Status.XPASS.value)

        exc_info = CIExcInfo(call.excinfo.type, call.excinfo.value, call.excinfo.tb) if call.excinfo else None

        CITest.mark_fail(test_id, exc_info)

    @staticmethod
    def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
        if not CIVisibility.enabled:
            return

        session_id = _get_session_id(session)

        # TODO: make coverage officially part of test session object so we don't use set_tag() directly.
        invoked_by_coverage_run_status = _is_coverage_invoked_by_coverage_run()
        pytest_cov_status = _is_pytest_cov_enabled(session.config)
        if _is_coverage_patched() and (pytest_cov_status or invoked_by_coverage_run_status):
            if invoked_by_coverage_run_status and not pytest_cov_status:
                run_coverage_report()

            lines_pct_value = _coverage_data.get(PCT_COVERED_KEY, None)
            if not isinstance(lines_pct_value, float):
                log.warning("Tried to add total covered percentage to session span but the format was unexpected")
                return
            CISession.set_tag(session_id, test.TEST_LINES_PCT, lines_pct_value)

        CISession.finish(session_id, force_finish_children=True)
        CIVisibility.disable()

    @staticmethod
    @pytest.hookimpl(trylast=True)
    def pytest_ddtrace_get_item_module_name(item):
        names = _get_names_from_item(item)
        return names.module

    @staticmethod
    @pytest.hookimpl(trylast=True)
    def pytest_ddtrace_get_item_suite_name(item):
        """
        Extract suite name from a `pytest.Item` instance.
        If the module path doesn't exist, the suite path will be reported in full.
        """
        names = _get_names_from_item(item)
        return names.suite

    @staticmethod
    @pytest.hookimpl(trylast=True)
    def pytest_ddtrace_get_item_test_name(item):
        """Extract name from item, prepending class if desired"""
        names = _get_names_from_item(item)
        return names.test
