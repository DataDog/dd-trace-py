from pathlib import Path
import typing as t

from ddtrace.ext.test_visibility import api as ext_api
from ddtrace.ext.test_visibility._utils import _catch_and_log_exceptions
from ddtrace.internal.ci_visibility.errors import CIVisibilityError
from ddtrace.internal.ci_visibility.service_registry import require_ci_visibility_service
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


log = get_logger(__name__)


class ITRMixin:
    """Mixin class for ITR-related functionality."""

    @staticmethod
    @_catch_and_log_exceptions
    def mark_itr_skipped(item_id: t.Union[ext_api.TestSuiteId, ext_api.TestId]):
        log.debug("Marking item %s as skipped by ITR", item_id)

        if not isinstance(item_id, (ext_api.TestSuiteId, ext_api.TestId)):
            log.warning("Only suites or tests can be skipped, not %s", type(item_id))
            return
        require_ci_visibility_service().get_item_by_id(item_id).finish_itr_skipped()

    @staticmethod
    @_catch_and_log_exceptions
    def mark_itr_unskippable(item_id: t.Union[ext_api.TestSuiteId, ext_api.TestId]):
        log.debug("Marking item %s as unskippable by ITR", item_id)

        require_ci_visibility_service().get_item_by_id(item_id).mark_itr_unskippable()

    @staticmethod
    @_catch_and_log_exceptions
    def mark_itr_forced_run(item_id: t.Union[ext_api.TestSuiteId, ext_api.TestId]):
        log.debug("Marking item %s as forced run by ITR", item_id)

        require_ci_visibility_service().get_item_by_id(item_id).mark_itr_forced_run()

    @staticmethod
    @_catch_and_log_exceptions
    def was_itr_forced_run(item_id: t.Union[ext_api.TestSuiteId, ext_api.TestId]) -> bool:
        log.debug("Checking if item %s was forced run by ITR", item_id)

        return require_ci_visibility_service().get_item_by_id(item_id).was_itr_forced_run()

    @staticmethod
    @_catch_and_log_exceptions
    def is_itr_skippable(item_id: t.Union[ext_api.TestSuiteId, ext_api.TestId]) -> bool:
        log.debug("Checking if item %s is skippable by ITR", item_id)
        ci_visibility_instance = require_ci_visibility_service()

        if not isinstance(item_id, (ext_api.TestSuiteId, ext_api.TestId)):
            log.warning("Only suites or tests can be skippable, not %s", type(item_id))
            return False

        if not ci_visibility_instance.test_skipping_enabled():
            log.debug("Test skipping is not enabled")
            return False

        return ci_visibility_instance.is_item_itr_skippable(item_id)

    @staticmethod
    @_catch_and_log_exceptions
    def is_itr_unskippable(item_id: t.Union[ext_api.TestSuiteId, ext_api.TestId]) -> bool:
        log.debug("Checking if item %s is unskippable by ITR", item_id)

        if not isinstance(item_id, (ext_api.TestSuiteId, ext_api.TestId)):
            raise CIVisibilityError("Only suites or tests can be unskippable")
        return require_ci_visibility_service().get_item_by_id(item_id).is_itr_unskippable()

    @staticmethod
    @_catch_and_log_exceptions
    def was_itr_skipped(item_id: t.Union[ext_api.TestSuiteId, ext_api.TestId]) -> bool:
        log.debug("Checking if item %s was skipped by ITR", item_id)

        return require_ci_visibility_service().get_item_by_id(item_id).is_itr_skipped()

    @staticmethod
    @_catch_and_log_exceptions
    def add_coverage_data(item_id, coverage_data) -> None:
        """Adds coverage data to an item, merging with existing coverage data if necessary"""
        log.debug("Adding coverage data for item id %s", item_id)

        if not isinstance(item_id, (ext_api.TestSuiteId, ext_api.TestId)):
            log.warning("Coverage data can only be added to suites and tests, not %s", type(item_id))
            return

        require_ci_visibility_service().get_item_by_id(item_id).add_coverage_data(coverage_data)

    @staticmethod
    @_catch_and_log_exceptions
    def get_coverage_data(
        item_id: t.Union[ext_api.TestSuiteId, ext_api.TestId]
    ) -> t.Optional[t.Dict[Path, CoverageLines]]:
        log.debug("Getting coverage data for item %s", item_id)

        return require_ci_visibility_service().get_item_by_id(item_id).get_coverage_data()
