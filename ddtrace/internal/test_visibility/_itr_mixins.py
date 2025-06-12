from pathlib import Path
import typing as t

from ddtrace.ext.test_visibility import api as ext_api
from ddtrace.ext.test_visibility._utils import _catch_and_log_exceptions
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
        from ddtrace.internal.ci_visibility.recorder import on_itr_finish_item_skipped

        on_itr_finish_item_skipped(item_id)

    @staticmethod
    @_catch_and_log_exceptions
    def mark_itr_unskippable(item_id: t.Union[ext_api.TestSuiteId, InternalTestId]):
        log.debug("Marking item %s as unskippable by ITR", item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_itr_mark_unskippable

        on_itr_mark_unskippable(item_id)

    @staticmethod
    @_catch_and_log_exceptions
    def mark_itr_forced_run(item_id: t.Union[ext_api.TestSuiteId, InternalTestId]):
        log.debug("Marking item %s as forced run by ITR", item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_itr_mark_forced_run

        on_itr_mark_forced_run(item_id)

    @staticmethod
    @_catch_and_log_exceptions
    def was_itr_forced_run(item_id: t.Union[ext_api.TestSuiteId, InternalTestId]) -> bool:
        log.debug("Checking if item %s was forced run by ITR", item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_itr_was_forced_run

        return on_itr_was_forced_run(item_id)

    @staticmethod
    @_catch_and_log_exceptions
    def is_itr_skippable(item_id: t.Union[ext_api.TestSuiteId, InternalTestId]) -> bool:
        log.debug("Checking if item %s is skippable by ITR", item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_itr_is_item_skippable

        return on_itr_is_item_skippable(item_id)

    @staticmethod
    @_catch_and_log_exceptions
    def is_itr_unskippable(item_id: t.Union[ext_api.TestSuiteId, InternalTestId]) -> bool:
        log.debug("Checking if item %s is unskippable by ITR", item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_itr_is_item_unskippable

        return on_itr_is_item_unskippable(item_id)

    @staticmethod
    @_catch_and_log_exceptions
    def was_itr_skipped(item_id: t.Union[ext_api.TestSuiteId, InternalTestId]) -> bool:
        log.debug("Checking if item %s was skipped by ITR", item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_itr_was_item_skipped

        return on_itr_was_item_skipped(item_id)

    @staticmethod
    @_catch_and_log_exceptions
    def add_coverage_data(add_coverage_args: "ITRMixin.AddCoverageArgs") -> None:
        log.debug("Adding coverage data for item %s", add_coverage_args.item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_add_coverage_data

        on_add_coverage_data(add_coverage_args)

    @staticmethod
    @_catch_and_log_exceptions
    def get_coverage_data(
        item_id: t.Union[ext_api.TestSuiteId, InternalTestId]
    ) -> t.Optional[t.Dict[Path, CoverageLines]]:
        log.debug("Getting coverage data for item %s", item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_get_coverage_data

        return on_get_coverage_data(item_id)
