from pathlib import Path
import typing as t

from ddtrace.ext.test_visibility import api as ext_api
from ddtrace.ext.test_visibility._test_visibility_base import TestSessionId
from ddtrace.ext.test_visibility._utils import _catch_and_log_exceptions
from ddtrace.internal.ci_visibility.service_registry import require_ci_visibility_service
from ddtrace.internal.codeowners import Codeowners as _Codeowners
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility._atr_mixins import ATRSessionMixin
from ddtrace.internal.test_visibility._atr_mixins import ATRTestMixin
from ddtrace.internal.test_visibility._attempt_to_fix_mixins import AttemptToFixSessionMixin
from ddtrace.internal.test_visibility._attempt_to_fix_mixins import AttemptToFixTestMixin
from ddtrace.internal.test_visibility._benchmark_mixin import BenchmarkTestMixin
from ddtrace.internal.test_visibility._efd_mixins import EFDSessionMixin
from ddtrace.internal.test_visibility._efd_mixins import EFDTestMixin
from ddtrace.internal.test_visibility._itr_mixins import ITRMixin
from ddtrace.internal.test_visibility._library_capabilities import LibraryCapabilities
from ddtrace.trace import Span
from ddtrace.trace import Tracer


log = get_logger(__name__)


def _get_item_span(item_id: t.Union[ext_api.TestVisibilityItemId, ext_api.TestId]) -> Span:
    return require_ci_visibility_service().get_item_by_id(item_id).get_span()


class InternalTestBase(ext_api.TestBase):
    @staticmethod
    @_catch_and_log_exceptions
    def get_span(item_id: t.Union[ext_api.TestVisibilityItemId, ext_api.TestId]) -> Span:
        return _get_item_span(item_id)

    @staticmethod
    @_catch_and_log_exceptions
    def stash_set(item_id, key: str, value: object):
        log.debug("Stashing value %s for key %s in item %s", value, key, item_id)

        require_ci_visibility_service().get_item_by_id(item_id).stash_set(key, value)

    @staticmethod
    @_catch_and_log_exceptions
    def stash_get(item_id: ext_api.TestVisibilityItemId, key: str) -> t.Optional[object]:
        log.debug("Getting stashed value for key %s in item %s", key, item_id)

        return require_ci_visibility_service().get_item_by_id(item_id).stash_get(key)

    @staticmethod
    @_catch_and_log_exceptions
    def stash_delete(item_id: ext_api.TestVisibilityItemId, key: str):
        log.debug("Deleting stashed value for key %s in item %s", key, item_id)

        require_ci_visibility_service().get_item_by_id(item_id).stash_delete(key)

    @staticmethod
    @_catch_and_log_exceptions
    def overwrite_attributes(
        item_id: ext_api.TestId,
        name: t.Optional[str] = None,
        suite_name: t.Optional[str] = None,
        parameters: t.Optional[str] = None,
        codeowners: t.Optional[t.List[str]] = None,
    ) -> None:
        log.debug("Overwriting attributes for: %s", item_id)

        require_ci_visibility_service().get_test_by_id(item_id).overwrite_attributes(
            name,
            suite_name,
            parameters,
            codeowners,
        )


class InternalTestSession(ext_api.TestSession, EFDSessionMixin, ATRSessionMixin, AttemptToFixSessionMixin):
    @staticmethod
    def get_span() -> Span:
        return _get_item_span(TestSessionId())

    @staticmethod
    def is_finished() -> bool:
        return ext_api._is_item_finished(TestSessionId())

    @staticmethod
    @_catch_and_log_exceptions
    def get_codeowners() -> t.Optional[_Codeowners]:
        log.debug("Getting codeowners")

        return require_ci_visibility_service().get_codeowners()

    @staticmethod
    @_catch_and_log_exceptions
    def get_tracer() -> t.Optional[Tracer]:
        log.debug("Getting tracer")

        return require_ci_visibility_service().get_tracer()

    @staticmethod
    @_catch_and_log_exceptions
    def get_workspace_path() -> t.Optional[Path]:
        log.debug("Getting workspace path")

        path_str = require_ci_visibility_service().get_workspace_path()
        return Path(path_str) if path_str is not None else None

    @staticmethod
    @_catch_and_log_exceptions
    def should_collect_coverage() -> bool:
        log.debug("Checking if should collect coverage")

        return require_ci_visibility_service().should_collect_coverage()

    @staticmethod
    @_catch_and_log_exceptions
    def is_test_skipping_enabled() -> bool:
        log.debug("Checking if test skipping is enabled")

        return require_ci_visibility_service().test_skipping_enabled()

    @staticmethod
    @_catch_and_log_exceptions
    def set_covered_lines_pct(coverage_pct: float) -> None:
        log.debug("Setting coverage percentage for session to %s", coverage_pct)

        require_ci_visibility_service().get_session().set_covered_lines_pct(coverage_pct)

    @staticmethod
    @_catch_and_log_exceptions
    def get_path_codeowners(path: Path) -> t.Optional[t.List[str]]:
        log.debug("Getting codeowners for path %s", path)

        codeowners = require_ci_visibility_service().get_codeowners()
        if codeowners is None:
            return None
        return codeowners.of(str(path))

    @staticmethod
    @_catch_and_log_exceptions
    def set_library_capabilities(capabilities: LibraryCapabilities) -> None:
        log.debug("Setting library capabilities")

        require_ci_visibility_service().set_library_capabilities(capabilities)

    @staticmethod
    @_catch_and_log_exceptions
    def set_itr_skipped_count(skipped_count: int) -> None:
        log.debug("Setting skipped count: %d", skipped_count)

        require_ci_visibility_service().get_session().set_skipped_count(skipped_count)


class InternalTestModule(ext_api.TestModule, InternalTestBase):
    pass


class InternalTestSuite(ext_api.TestSuite, InternalTestBase, ITRMixin):
    pass


class InternalTest(
    ext_api.Test, InternalTestBase, ITRMixin, EFDTestMixin, ATRTestMixin, AttemptToFixTestMixin, BenchmarkTestMixin
):
    @staticmethod
    @_catch_and_log_exceptions
    def finish(
        item_id: ext_api.TestId,
        status: t.Optional[ext_api.TestStatus] = None,
        skip_reason: t.Optional[str] = None,
        exc_info: t.Optional[ext_api.TestExcInfo] = None,
        override_finish_time: t.Optional[float] = None,
    ):
        log.debug("Finishing test with status: %s, skip_reason: %s", status, skip_reason)
        require_ci_visibility_service().get_test_by_id(item_id).finish_test(
            status=status, skip_reason=skip_reason, exc_info=exc_info, override_finish_time=override_finish_time
        )

    @staticmethod
    @_catch_and_log_exceptions
    def is_new_test(test_id: ext_api.TestId) -> bool:
        log.debug("Checking if test %s is new", test_id)

        return require_ci_visibility_service().get_test_by_id(test_id).is_new()

    @staticmethod
    @_catch_and_log_exceptions
    def is_quarantined_test(test_id: ext_api.TestId) -> bool:
        log.debug("Checking if test %s is quarantined", test_id)

        return require_ci_visibility_service().get_test_by_id(test_id).is_quarantined()

    @staticmethod
    @_catch_and_log_exceptions
    def is_disabled_test(test_id: ext_api.TestId) -> bool:
        log.debug("Checking if test %s is disabled", test_id)

        return require_ci_visibility_service().get_test_by_id(test_id).is_disabled()

    @staticmethod
    @_catch_and_log_exceptions
    def is_attempt_to_fix(test_id: ext_api.TestId) -> bool:
        log.debug("Checking if test %s is attempt to fix", test_id)

        return require_ci_visibility_service().get_test_by_id(test_id).is_attempt_to_fix()

    @staticmethod
    @_catch_and_log_exceptions
    def overwrite_attributes(
        item_id: ext_api.TestId,
        name: t.Optional[str] = None,
        suite_name: t.Optional[str] = None,
        parameters: t.Optional[str] = None,
        codeowners: t.Optional[t.List[str]] = None,
    ):
        log.debug("Overwriting attributes for test %s", item_id)

        require_ci_visibility_service().get_test_by_id(item_id).overwrite_attributes(
            name, suite_name, parameters, codeowners
        )
