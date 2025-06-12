from pathlib import Path
import typing as t
from typing import NamedTuple

from ddtrace.ext.test_visibility import api as ext_api
from ddtrace.ext.test_visibility._test_visibility_base import TestSessionId
from ddtrace.ext.test_visibility._utils import _catch_and_log_exceptions
from ddtrace.ext.test_visibility._utils import _is_item_finished
from ddtrace.ext.test_visibility.api import TestExcInfo
from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.internal.codeowners import Codeowners as _Codeowners
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility._atr_mixins import ATRSessionMixin
from ddtrace.internal.test_visibility._atr_mixins import ATRTestMixin
from ddtrace.internal.test_visibility._attempt_to_fix_mixins import AttemptToFixSessionMixin
from ddtrace.internal.test_visibility._attempt_to_fix_mixins import AttemptToFixTestMixin
from ddtrace.internal.test_visibility._benchmark_mixin import BenchmarkTestMixin
from ddtrace.internal.test_visibility._efd_mixins import EFDSessionMixin
from ddtrace.internal.test_visibility._efd_mixins import EFDTestMixin
from ddtrace.internal.test_visibility._internal_item_ids import InternalTestId
from ddtrace.internal.test_visibility._itr_mixins import ITRMixin
from ddtrace.internal.test_visibility._library_capabilities import LibraryCapabilities
from ddtrace.internal.test_visibility._utils import _get_item_span
from ddtrace.trace import Span
from ddtrace.trace import Tracer


log = get_logger(__name__)


class InternalTestBase(ext_api.TestBase):
    @staticmethod
    @_catch_and_log_exceptions
    def get_span(item_id: t.Union[ext_api.TestVisibilityItemId, InternalTestId]) -> Span:
        return _get_item_span(item_id)

    @staticmethod
    @_catch_and_log_exceptions
    def stash_set(item_id, key: str, value: object):
        log.debug("Stashing value %s for key %s in item %s", value, key, item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        CIVisibility.get_item_by_id(item_id).stash_set(key, value)

    @staticmethod
    @_catch_and_log_exceptions
    def stash_get(item_id: ext_api.TestVisibilityItemId, key: str) -> t.Optional[object]:
        log.debug("Getting stashed value for key %s in item %s", key, item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        return CIVisibility.get_item_by_id(item_id).stash_get(key)

    @staticmethod
    @_catch_and_log_exceptions
    def stash_delete(item_id: ext_api.TestVisibilityItemId, key: str):
        log.debug("Deleting stashed value for key %s in item %s", key, item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        CIVisibility.get_item_by_id(item_id).stash_delete(key)

    @staticmethod
    @_catch_and_log_exceptions
    def overwrite_attributes(overwrite_attribute_args: "InternalTest.OverwriteAttributesArgs") -> None:
        log.debug("Overwriting attributes: %s", overwrite_attribute_args)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        CIVisibility.get_test_by_id(overwrite_attribute_args.test_id).overwrite_attributes(
            overwrite_attribute_args.name,
            overwrite_attribute_args.suite_name,
            overwrite_attribute_args.parameters,
            overwrite_attribute_args.codeowners,
        )


class InternalTestSession(ext_api.TestSession, EFDSessionMixin, ATRSessionMixin, AttemptToFixSessionMixin):
    @staticmethod
    def get_span() -> Span:
        return _get_item_span(TestSessionId())

    @staticmethod
    def is_finished() -> bool:
        return _is_item_finished(TestSessionId())

    @staticmethod
    @_catch_and_log_exceptions
    def get_codeowners() -> t.Optional[_Codeowners]:
        log.debug("Getting codeowners")
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        return CIVisibility.get_codeowners()

    @staticmethod
    @_catch_and_log_exceptions
    def get_tracer() -> t.Optional[Tracer]:
        log.debug("Getting tracer")
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        return CIVisibility.get_tracer()

    @staticmethod
    @_catch_and_log_exceptions
    def get_workspace_path() -> t.Optional[Path]:
        log.debug("Getting workspace path")
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        path_str = CIVisibility.get_workspace_path()
        return Path(path_str) if path_str is not None else None

    @staticmethod
    @_catch_and_log_exceptions
    def should_collect_coverage() -> bool:
        log.debug("Checking if should collect coverage")
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        return CIVisibility.should_collect_coverage()

    @staticmethod
    @_catch_and_log_exceptions
    def is_test_skipping_enabled() -> bool:
        log.debug("Checking if test skipping is enabled")
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        return CIVisibility.test_skipping_enabled()

    @staticmethod
    @_catch_and_log_exceptions
    def set_covered_lines_pct(coverage_pct: float) -> None:
        log.debug("Setting coverage percentage for session to %s", coverage_pct)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        CIVisibility.get_session().set_covered_lines_pct(coverage_pct)

    @staticmethod
    @_catch_and_log_exceptions
    def get_path_codeowners(path: Path) -> t.Optional[t.List[str]]:
        log.debug("Getting codeowners for path %s", path)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        codeowners = CIVisibility.get_codeowners()
        if codeowners is None:
            return None
        return codeowners.of(str(path))

    @staticmethod
    @_catch_and_log_exceptions
    def set_library_capabilities(capabilities: LibraryCapabilities) -> None:
        log.debug("Setting library capabilities")
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        CIVisibility.set_library_capabilities(capabilities)

    @staticmethod
    @_catch_and_log_exceptions
    def set_itr_skipped_count(skipped_count: int) -> None:
        log.debug("Setting skipped count: %d", skipped_count)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        CIVisibility.get_session().set_skipped_count(skipped_count)


class InternalTestModule(ext_api.TestModule, InternalTestBase):
    pass


class InternalTestSuite(ext_api.TestSuite, InternalTestBase, ITRMixin):
    pass


class InternalTest(
    ext_api.Test, InternalTestBase, ITRMixin, EFDTestMixin, ATRTestMixin, AttemptToFixTestMixin, BenchmarkTestMixin
):
    class FinishArgs(NamedTuple):
        """InternalTest allows finishing with an overridden finish time (for EFD and other retry purposes)"""

        test_id: InternalTestId
        status: t.Optional[TestStatus] = None
        skip_reason: t.Optional[str] = None
        exc_info: t.Optional[TestExcInfo] = None
        override_finish_time: t.Optional[float] = None

    @staticmethod
    @_catch_and_log_exceptions
    def finish(
        item_id: InternalTestId,
        status: t.Optional[ext_api.TestStatus] = None,
        reason: t.Optional[str] = None,
        exc_info: t.Optional[ext_api.TestExcInfo] = None,
        override_finish_time: t.Optional[float] = None,
    ):
        log.debug("Finishing test with status: %s, reason: %s", status, reason)
        # Lazy import to avoid circular dependency
        from ddtrace.ext.test_visibility.api import Test
        from ddtrace.internal.ci_visibility.recorder import on_finish_test

        # Test.FinishArgs requires a non-optional status, so provide a default if None
        final_status = status if status is not None else ext_api.TestStatus.PASS
        on_finish_test(Test.FinishArgs(item_id, final_status, reason, exc_info))

    @staticmethod
    @_catch_and_log_exceptions
    def is_new(test_id: t.Union[ext_api.TestId, InternalTestId]) -> bool:
        log.debug("Checking if test %s is new", test_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        return CIVisibility.get_test_by_id(test_id).is_new()

    @staticmethod
    @_catch_and_log_exceptions
    def is_quarantined(test_id: t.Union[ext_api.TestId, InternalTestId]) -> bool:
        log.debug("Checking if test %s is quarantined", test_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        return CIVisibility.get_test_by_id(test_id).is_quarantined()

    @staticmethod
    @_catch_and_log_exceptions
    def is_disabled(test_id: t.Union[ext_api.TestId, InternalTestId]) -> bool:
        log.debug("Checking if test %s is disabled", test_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        return CIVisibility.get_test_by_id(test_id).is_disabled()

    @staticmethod
    @_catch_and_log_exceptions
    def is_attempt_to_fix(test_id: t.Union[ext_api.TestId, InternalTestId]) -> bool:
        log.debug("Checking if test %s is attempt to fix", test_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        return CIVisibility.get_test_by_id(test_id).is_attempt_to_fix()

    class OverwriteAttributesArgs(NamedTuple):
        test_id: InternalTestId
        name: t.Optional[str] = None
        suite_name: t.Optional[str] = None
        parameters: t.Optional[str] = None
        codeowners: t.Optional[t.List[str]] = None

    class FinishTestArgs(NamedTuple):
        test_id: InternalTestId
        status: t.Optional[TestStatus] = None
        skip_reason: t.Optional[str] = None
        exc_info: t.Optional[TestExcInfo] = None

    @staticmethod
    @_catch_and_log_exceptions
    def overwrite_attributes(
        item_id: InternalTestId,
        name: t.Optional[str] = None,
        suite_name: t.Optional[str] = None,
        parameters: t.Optional[str] = None,
        codeowners: t.Optional[t.List[str]] = None,
    ):
        log.debug("Overwriting attributes for test %s", item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_test_overwrite_attributes

        on_test_overwrite_attributes(
            InternalTest.OverwriteAttributesArgs(item_id, name, suite_name, parameters, codeowners)
        )

    @staticmethod
    @_catch_and_log_exceptions
    def finish_test(finish_args: "InternalTest.FinishTestArgs") -> None:
        log.debug("Finishing test %s with status %s", finish_args.test_id, finish_args.status)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import CIVisibility

        CIVisibility.get_test_by_id(finish_args.test_id).finish_test(
            finish_args.status, finish_args.skip_reason, finish_args.exc_info
        )
