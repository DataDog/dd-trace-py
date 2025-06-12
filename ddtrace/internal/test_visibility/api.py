from pathlib import Path
import typing as t
from typing import NamedTuple

from ddtrace.ext.test_visibility import api as ext_api
from ddtrace.ext.test_visibility._test_visibility_base import TestSessionId
from ddtrace.ext.test_visibility._utils import _catch_and_log_exceptions
from ddtrace.internal.test_visibility._utils import _get_item_span
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
        from ddtrace.internal.ci_visibility.recorder import on_item_stash_set
        on_item_stash_set(item_id, key, value)

    @staticmethod
    @_catch_and_log_exceptions
    def stash_get(item_id: ext_api.TestVisibilityItemId, key: str):
        log.debug("Getting stashed value for key %s in item %s", key, item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_item_stash_get
        stash_value = on_item_stash_get(item_id, key)
        log.debug("Got stashed value %s for key %s in item %s", stash_value, key, item_id)
        return stash_value

    @staticmethod
    @_catch_and_log_exceptions
    def stash_delete(item_id: ext_api.TestVisibilityItemId, key: str):
        log.debug("Deleting stashed value for key %s in item %s", key, item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_item_stash_delete
        on_item_stash_delete(item_id, key)


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
        log.debug("Getting codeowners object")
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_session_get_codeowners
        codeowners: t.Optional[_Codeowners] = on_session_get_codeowners()
        return codeowners

    @staticmethod
    @_catch_and_log_exceptions
    def get_tracer() -> t.Optional[Tracer]:
        log.debug("Getting test session tracer")
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_session_get_tracer
        tracer: t.Optional[Tracer] = on_session_get_tracer()
        log.debug("Got test session tracer: %s", tracer)
        return tracer

    @staticmethod
    @_catch_and_log_exceptions
    def get_workspace_path() -> t.Optional[Path]:
        log.debug("Getting session workspace path")
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_session_get_workspace_path
        workspace_path: Path = on_session_get_workspace_path()
        return workspace_path

    @staticmethod
    @_catch_and_log_exceptions
    def should_collect_coverage() -> bool:
        log.debug("Checking if coverage should be collected for session")
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_session_should_collect_coverage
        _should_collect_coverage = bool(on_session_should_collect_coverage())
        log.debug("Coverage should be collected: %s", _should_collect_coverage)
        return _should_collect_coverage

    @staticmethod
    @_catch_and_log_exceptions
    def is_test_skipping_enabled() -> bool:
        log.debug("Checking if test skipping is enabled")
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_session_is_test_skipping_enabled
        _is_test_skipping_enabled = bool(on_session_is_test_skipping_enabled())
        log.debug("Test skipping is enabled: %s", _is_test_skipping_enabled)
        return _is_test_skipping_enabled

    @staticmethod
    @_catch_and_log_exceptions
    def set_covered_lines_pct(coverage_pct: float):
        log.debug("Setting covered lines percentage for session to %s", coverage_pct)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_session_set_covered_lines_pct
        on_session_set_covered_lines_pct(coverage_pct)

    @staticmethod
    @_catch_and_log_exceptions
    def get_path_codeowners(path: Path) -> t.Optional[t.List[str]]:
        log.debug("Getting codeowners object for path %s", path)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_session_get_path_codeowners
        path_codeowners: t.Optional[t.List[str]] = on_session_get_path_codeowners(path)
        return path_codeowners

    @staticmethod
    @_catch_and_log_exceptions
    def set_library_capabilities(capabilities: LibraryCapabilities) -> None:
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_session_set_library_capabilities
        on_session_set_library_capabilities(capabilities)

    @staticmethod
    @_catch_and_log_exceptions
    def set_itr_tags(skipped_count: int):
        log.debug("Setting itr session tags: %d", skipped_count)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_session_set_itr_skipped_count
        on_session_set_itr_skipped_count(skipped_count)


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
        from ddtrace.internal.ci_visibility.recorder import on_finish_test
        from ddtrace.ext.test_visibility.api import Test
        on_finish_test(Test.FinishArgs(item_id, status, reason, exc_info))

    @staticmethod
    @_catch_and_log_exceptions
    def is_new_test(item_id: InternalTestId) -> bool:
        log.debug("Checking if test %s is new", item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_is_new_test
        is_new = bool(on_is_new_test(item_id))
        log.debug("Test %s is new: %s", item_id, is_new)
        return is_new

    @staticmethod
    @_catch_and_log_exceptions
    def is_quarantined_test(item_id: InternalTestId) -> bool:
        log.debug("Checking if test %s is quarantined", item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_is_quarantined_test
        is_quarantined = bool(on_is_quarantined_test(item_id))
        log.debug("Test %s is quarantined: %s", item_id, is_quarantined)
        return is_quarantined

    @staticmethod
    @_catch_and_log_exceptions
    def is_disabled_test(item_id: InternalTestId) -> bool:
        log.debug("Checking if test %s is disabled", item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_is_disabled_test
        is_disabled = bool(on_is_disabled_test(item_id))
        log.debug("Test %s is disabled: %s", item_id, is_disabled)
        return is_disabled

    @staticmethod
    @_catch_and_log_exceptions
    def is_attempt_to_fix(item_id: InternalTestId) -> bool:
        log.debug("Checking if test %s is attempt to fix", item_id)
        # Lazy import to avoid circular dependency
        from ddtrace.internal.ci_visibility.recorder import on_is_attempt_to_fix
        is_attempt_to_fix = bool(on_is_attempt_to_fix(item_id))
        log.debug("Test %s is attempt to fix: %s", item_id, is_attempt_to_fix)
        return is_attempt_to_fix

    class OverwriteAttributesArgs(NamedTuple):
        test_id: InternalTestId
        name: t.Optional[str] = None
        suite_name: t.Optional[str] = None
        parameters: t.Optional[str] = None
        codeowners: t.Optional[t.List[str]] = None

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
