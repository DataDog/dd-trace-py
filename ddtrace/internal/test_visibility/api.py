import dataclasses
from pathlib import Path
import typing as t

from ddtrace import Span
from ddtrace.ext.test_visibility import api as ext_api
from ddtrace.ext.test_visibility._test_visibility_base import TestSessionId
from ddtrace.ext.test_visibility._utils import _catch_and_log_exceptions
from ddtrace.ext.test_visibility._utils import _is_item_finished
from ddtrace.internal import core
from ddtrace.internal.codeowners import Codeowners as _Codeowners
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility._utils import _get_item_span
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


log = get_logger(__name__)


@dataclasses.dataclass(frozen=True)
class InternalTestId(ext_api.TestId):
    retry_number: int = 0

    def __repr__(self):
        return "TestId(module={}, suite={}, test={}, parameters={}, retry_number={})".format(
            self.parent_id.parent_id.name,
            self.parent_id.name,
            self.name,
            self.parameters,
            self.retry_number,
        )


class InternalTestBase(ext_api.TestBase):
    @staticmethod
    @_catch_and_log_exceptions
    def get_span(item_id: t.Union[ext_api.TestVisibilityItemId, InternalTestId]) -> Span:
        return _get_item_span(item_id)


class ITRMixin(ext_api.TestBase):
    """Mixin class for ITR-related functionality."""

    @staticmethod
    @_catch_and_log_exceptions
    def mark_itr_skipped(item_id: t.Union[ext_api.TestSuiteId, InternalTestId]):
        log.debug("Marking item %s as skipped by ITR", item_id)
        core.dispatch("test_visibility.itr.finish_skipped_by_itr", (item_id,))

    @staticmethod
    @_catch_and_log_exceptions
    def mark_itr_unskippable(item_id: t.Union[ext_api.TestSuiteId, InternalTestId]):
        log.debug("Marking item %s as unskippable by ITR", item_id)
        core.dispatch("test_visibility.itr.mark_unskippable", (item_id,))

    @staticmethod
    @_catch_and_log_exceptions
    def mark_itr_forced_run(item_id: t.Union[ext_api.TestSuiteId, InternalTestId]):
        log.debug("Marking item %s as unskippable by ITR", item_id)
        core.dispatch("test_visibility.itr.mark_forced_run", (item_id,))

    @staticmethod
    @_catch_and_log_exceptions
    def was_forced_run(item_id: t.Union[ext_api.TestSuiteId, InternalTestId]) -> bool:
        """Skippable items are not currently tied to a test session, so no session ID is passed"""
        log.debug("Checking if item %s was forced to run", item_id)
        _was_forced_run = bool(
            core.dispatch_with_results("test_visibility.itr.was_forced_run", (item_id,)).was_forced_run.value
        )
        log.debug("Item %s was forced run: %s", item_id, _was_forced_run)
        return _was_forced_run

    @staticmethod
    @_catch_and_log_exceptions
    def is_itr_skippable(item_id: t.Union[ext_api.TestSuiteId, InternalTestId]) -> bool:
        """Skippable items are not currently tied to a test session, so no session ID is passed"""
        log.debug("Checking if item %s is skippable", item_id)
        is_item_skippable = bool(
            core.dispatch_with_results("test_visibility.itr.is_item_skippable", (item_id,)).is_item_skippable.value
        )
        log.debug("Item %s is skippable: %s", item_id, is_item_skippable)

        return is_item_skippable

    @staticmethod
    @_catch_and_log_exceptions
    def is_itr_unskippable(item_id: t.Union[ext_api.TestSuiteId, InternalTestId]) -> bool:
        """Skippable items are not currently tied to a test session, so no session ID is passed"""
        log.debug("Checking if item %s is unskippable", item_id)
        is_item_unskippable = bool(
            core.dispatch_with_results("test_visibility.itr.is_item_unskippable", (item_id,)).is_item_unskippable.value
        )
        log.debug("Item %s is unskippable: %s", item_id, is_item_unskippable)

        return is_item_unskippable

    @staticmethod
    @_catch_and_log_exceptions
    def was_skipped_by_itr(item_id: t.Union[ext_api.TestSuiteId, InternalTestId]) -> bool:
        """Skippable items are not currently tied to a test session, so no session ID is passed"""
        log.debug("Checking if item %s was skipped by ITR", item_id)
        was_item_skipped = bool(
            core.dispatch_with_results("test_visibility.itr.was_item_skipped", (item_id,)).was_item_skipped.value
        )
        log.debug("Item %s was skipped by ITR: %s", item_id, was_item_skipped)
        return was_item_skipped

    class AddCoverageArgs(t.NamedTuple):
        item_id: t.Union[ext_api.TestSuiteId, ext_api.TestId]
        coverage_data: t.Dict[Path, CoverageLines]

    @staticmethod
    @_catch_and_log_exceptions
    def add_coverage_data(
        item_id: t.Union[ext_api.TestSuiteId, InternalTestId], coverage_data: t.Dict[Path, CoverageLines]
    ):
        log.debug("Adding coverage data for item %s: %s", item_id, coverage_data)
        core.dispatch("test_visibility.item.add_coverage_data", (ITRMixin.AddCoverageArgs(item_id, coverage_data),))

    @staticmethod
    @_catch_and_log_exceptions
    def get_coverage_data(
        item_id: t.Union[ext_api.TestSuiteId, InternalTestId]
    ) -> t.Optional[t.Dict[Path, CoverageLines]]:
        log.debug("Getting coverage data for item %s", item_id)
        coverage_data = core.dispatch_with_results(
            "test_visibility.item.get_coverage_data", (item_id,)
        ).coverage_data.value
        log.debug("Coverage data for item %s: %s", item_id, coverage_data)
        return coverage_data


class InternalTestSession(ext_api.TestSession):
    @staticmethod
    def get_span() -> Span:
        return _get_item_span(TestSessionId())

    @staticmethod
    def is_finished() -> bool:
        return _is_item_finished(TestSessionId())

    @staticmethod
    @_catch_and_log_exceptions
    def get_codeowners() -> t.Optional[_Codeowners]:
        log.debug("Getting codeowners object")

        codeowners: t.Optional[_Codeowners] = core.dispatch_with_results(
            "test_visibility.session.get_codeowners",
        ).codeowners.value
        return codeowners

    @staticmethod
    @_catch_and_log_exceptions
    def get_workspace_path() -> Path:
        log.debug("Getting session workspace path")

        workspace_path: Path = core.dispatch_with_results(
            "test_visibility.session.get_workspace_path"
        ).workspace_path.value
        return workspace_path

    @staticmethod
    @_catch_and_log_exceptions
    def should_collect_coverage() -> bool:
        log.debug("Checking if coverage should be collected for session")

        _should_collect_coverage = bool(
            core.dispatch_with_results("test_visibility.session.should_collect_coverage").should_collect_coverage.value
        )
        log.debug("Coverage should be collected: %s", _should_collect_coverage)

        return _should_collect_coverage

    @staticmethod
    @_catch_and_log_exceptions
    def is_test_skipping_enabled() -> bool:
        log.debug("Checking if test skipping is enabled")

        _is_test_skipping_enabled = bool(
            core.dispatch_with_results(
                "test_visibility.session.is_test_skipping_enabled"
            ).is_test_skipping_enabled.value
        )
        log.debug("Test skipping is enabled: %s", _is_test_skipping_enabled)

        return _is_test_skipping_enabled

    @staticmethod
    @_catch_and_log_exceptions
    def set_covered_lines_pct(coverage_pct: float):
        log.debug("Setting covered lines percentage for session to %s", coverage_pct)

        core.dispatch("test_visibility.session.set_covered_lines_pct", (coverage_pct,))

    @staticmethod
    @_catch_and_log_exceptions
    def get_path_codeowners(path: Path) -> t.Optional[t.List[str]]:
        log.debug("Getting codeowners object for path %s", path)

        path_codeowners: t.Optional[t.List[str]] = core.dispatch_with_results(
            "test_visibility.session.get_path_codeowners", (path,)
        ).path_codeowners.value
        return path_codeowners


class InternalTestModule(ext_api.TestModule, InternalTestBase):
    pass


class InternalTestSuite(ext_api.TestSuite, InternalTestBase, ITRMixin):
    pass


class InternalTest(ext_api.Test, InternalTestBase, ITRMixin):
    class DiscoverEarlyFlakeRetryArgs(t.NamedTuple):
        test_id: InternalTestId
        retry_number: int

    @staticmethod
    @_catch_and_log_exceptions
    def discover_early_flake_retry(item_id: InternalTestId):
        if item_id.retry_number <= 0:
            log.warning(
                "Cannot register early flake retry of test %s with retry number %s", item_id, item_id.retry_number
            )
        log.debug("Registered early flake retry for test %s, retry number: %s", item_id, item_id.retry_number)
        original_test_id = InternalTestId(item_id.parent_id, item_id.name, item_id.parameters)
        core.dispatch(
            "test_visibility.test.discover_early_flake_retry",
            (InternalTest.DiscoverEarlyFlakeRetryArgs(original_test_id, item_id.retry_number),),
        )

    @staticmethod
    @_catch_and_log_exceptions
    def mark_unskippable():
        log.debug("Marking test as unskippable")
