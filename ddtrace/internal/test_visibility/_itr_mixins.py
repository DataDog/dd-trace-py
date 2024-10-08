from pathlib import Path
import typing as t

from ddtrace.ext.test_visibility import api as ext_api
from ddtrace.ext.test_visibility._utils import _catch_and_log_exceptions
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility._internal_item_ids import InternalTestId
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


log = get_logger(__name__)


class ITRMixin:
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
