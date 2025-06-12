from pathlib import Path
import typing as t

from ddtrace.ext.test_visibility import api as ext_api
from ddtrace.ext.test_visibility._utils import _catch_and_log_exceptions
from ddtrace.internal.ci_visibility.errors import CIVisibilityError
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility._internal_item_ids import InternalTestId
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


log = get_logger(__name__)


class ITRMixin:
    """Mixin class for ITR-related functionality."""

    class AddCoverageArgs(t.NamedTuple):
        item_id: t.Union[ext_api.TestSuiteId, InternalTestId]
        coverage_data: t.Dict[Path, CoverageLines]

    @staticmethod
    @_catch_and_log_exceptions
    def mark_itr_skipped(item_id: t.Union[ext_api.TestSuiteId, InternalTestId]):
        log.debug("Marking item %s as skipped by ITR", item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        if not isinstance(item_id, (ext_api.TestSuiteId, InternalTestId)):
            log.warning("Only suites or tests can be skipped, not %s", type(item_id))
            return
        CIVisibility.get_item_by_id(item_id).finish_itr_skipped()

    @staticmethod
    @_catch_and_log_exceptions
    def mark_itr_unskippable(item_id: t.Union[ext_api.TestSuiteId, InternalTestId]):
        log.debug("Marking item %s as unskippable by ITR", item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        CIVisibility.get_item_by_id(item_id).mark_itr_unskippable()

    @staticmethod
    @_catch_and_log_exceptions
    def mark_itr_forced_run(item_id: t.Union[ext_api.TestSuiteId, InternalTestId]):
        log.debug("Marking item %s as forced run by ITR", item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        CIVisibility.get_item_by_id(item_id).mark_itr_forced_run()

    @staticmethod
    @_catch_and_log_exceptions
    def was_itr_forced_run(item_id: t.Union[ext_api.TestSuiteId, InternalTestId]) -> bool:
        log.debug("Checking if item %s was forced run by ITR", item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        return CIVisibility.get_item_by_id(item_id).was_itr_forced_run()

    @staticmethod
    @_catch_and_log_exceptions
    def is_itr_skippable(item_id: t.Union[ext_api.TestSuiteId, InternalTestId]) -> bool:
        log.debug("Checking if item %s is skippable by ITR", item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        if not isinstance(item_id, (ext_api.TestSuiteId, InternalTestId)):
            log.warning("Only suites or tests can be skippable, not %s", type(item_id))
            return False

        if not CIVisibility.test_skipping_enabled():
            log.debug("Test skipping is not enabled")
            return False

        return CIVisibility.is_item_itr_skippable(item_id)

    @staticmethod
    @_catch_and_log_exceptions
    def is_itr_unskippable(item_id: t.Union[ext_api.TestSuiteId, InternalTestId]) -> bool:
        log.debug("Checking if item %s is unskippable by ITR", item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        if not isinstance(item_id, (ext_api.TestSuiteId, InternalTestId)):
            raise CIVisibilityError("Only suites or tests can be unskippable")
        return CIVisibility.get_item_by_id(item_id).is_itr_unskippable()

    @staticmethod
    @_catch_and_log_exceptions
    def was_itr_skipped(item_id: t.Union[ext_api.TestSuiteId, InternalTestId]) -> bool:
        log.debug("Checking if item %s was skipped by ITR", item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        return CIVisibility.get_item_by_id(item_id).is_itr_skipped()

    @staticmethod
    @_catch_and_log_exceptions
    def add_coverage_data(add_coverage_args: "ITRMixin.AddCoverageArgs") -> None:
        """Adds coverage data to an item, merging with existing coverage data if necessary"""
        item_id = add_coverage_args.item_id
        coverage_data = add_coverage_args.coverage_data

        log.debug("Adding coverage data for item id %s", item_id)

        if not isinstance(item_id, (ext_api.TestSuiteId, InternalTestId)):
            log.warning("Coverage data can only be added to suites and tests, not %s", type(item_id))
            return

        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        CIVisibility.get_item_by_id(item_id).add_coverage_data(coverage_data)

    @staticmethod
    @_catch_and_log_exceptions
    def get_coverage_data(
        item_id: t.Union[ext_api.TestSuiteId, InternalTestId]
    ) -> t.Optional[t.Dict[Path, CoverageLines]]:
        log.debug("Getting coverage data for item %s", item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        return CIVisibility.get_item_by_id(item_id).get_coverage_data()
