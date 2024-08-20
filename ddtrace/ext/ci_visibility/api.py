"""
Provides the API necessary to interacting with the CI Visibility service.

NOTE: BETA - this API is currently in development and is subject to change.

This API supports the agentless, and agent-proxied (EVP) modes. It does not support the APM protocol.

All functions in this module are meant to be called in a stateless manner. Test runners (or custom implementations) that
rely on this API are not expected to keep CI Visibility-related state for each session, module, suite or test.

Stable values of module, suite, test names, and parameters, are a necessity for this API to function properly.

All types and methods for interacting with the API are provided and documented in this file.
"""
import dataclasses
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import Any
from typing import Dict
from typing import List
from typing import NamedTuple
from typing import Optional
from typing import Tuple
from typing import Type
from typing import Union

from ddtrace import Span
from ddtrace.ext.ci_visibility._ci_visibility_base import CIItemId
from ddtrace.ext.ci_visibility._ci_visibility_base import CISourceFileInfoBase
from ddtrace.ext.ci_visibility._ci_visibility_base import _CISessionId
from ddtrace.ext.ci_visibility._ci_visibility_base import _CIVisibilityAPIBase
from ddtrace.ext.ci_visibility._ci_visibility_base import _CIVisibilityChildItemIdBase
from ddtrace.ext.ci_visibility._ci_visibility_base import _CIVisibilityRootItemIdBase
from ddtrace.ext.ci_visibility._utils import _catch_and_log_exceptions
from ddtrace.ext.ci_visibility._utils import _delete_item_tag
from ddtrace.ext.ci_visibility._utils import _delete_item_tags
from ddtrace.ext.ci_visibility._utils import _get_item_span
from ddtrace.ext.ci_visibility._utils import _get_item_tag
from ddtrace.ext.ci_visibility._utils import _is_item_finished
from ddtrace.ext.ci_visibility._utils import _set_item_tag
from ddtrace.ext.ci_visibility._utils import _set_item_tags
from ddtrace.ext.test import Status as TestStatus
from ddtrace.internal import core
from ddtrace.internal.codeowners import Codeowners
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class CITestStatus(Enum):
    PASS = TestStatus.PASS.value
    FAIL = TestStatus.FAIL.value
    SKIP = TestStatus.SKIP.value
    XFAIL = TestStatus.XFAIL.value
    XPASS = TestStatus.XPASS.value


DEFAULT_SESSION_NAME = "ci_visibility_session"


class DEFAULT_OPERATION_NAMES(Enum):
    SESSION = "ci_visibility.session"
    MODULE = "ci_visibility.module"
    SUITE = "ci_visibility.suite"
    TEST = "ci_visibility.test"


@dataclasses.dataclass(frozen=True)
class CIModuleId(_CIVisibilityRootItemIdBase):
    name: str

    def __repr__(self):
        return "CIModuleId(module={})".format(
            self.name,
        )


@dataclasses.dataclass(frozen=True)
class CISuiteId(_CIVisibilityChildItemIdBase[CIModuleId]):
    def __repr__(self):
        return "CISuiteId(module={}, suite={})".format(self.parent_id.name, self.name)


@dataclasses.dataclass(frozen=True)
class CITestId(_CIVisibilityChildItemIdBase[CISuiteId]):
    parameters: Optional[str] = None  # For hashability, a JSON string of a dictionary of parameters
    retry_number: int = 0

    def __repr__(self):
        return "CITestId(module={}, suite={}, test={}, parameters={}, retry_number={})".format(
            self.parent_id.parent_id.name,
            self.parent_id.name,
            self.name,
            self.parameters,
            self.retry_number,
        )


@dataclasses.dataclass(frozen=True)
class CISourceFileInfo(CISourceFileInfoBase):
    path: Path
    start_line: Optional[int] = None
    end_line: Optional[int] = None


@dataclasses.dataclass(frozen=True)
class CIExcInfo:
    exc_type: Type[BaseException]
    exc_value: BaseException
    exc_traceback: TracebackType


@_catch_and_log_exceptions
def enable_ci_visibility(config: Optional[Any] = None):
    from ddtrace.internal.ci_visibility import CIVisibility

    CIVisibility.enable(config=config)
    if not CIVisibility.enabled:
        log.warning("CI Visibility enabling failed.")


@_catch_and_log_exceptions
def is_ci_visibility_enabled():
    from ddtrace.internal.ci_visibility import CIVisibility

    return CIVisibility.enabled


@_catch_and_log_exceptions
def disable_ci_visibility():
    from ddtrace.internal.ci_visibility import CIVisibility

    CIVisibility.disable()
    if CIVisibility.enabled:
        log.warning("CI Visibility disabling failed.")


class CIBase(_CIVisibilityAPIBase):
    @staticmethod
    def get_tag(item_id: CIItemId, tag_name: str) -> Any:
        return _get_item_tag(item_id, tag_name)

    @staticmethod
    def set_tag(item_id: CIItemId, tag_name: str, tag_value: Any, recurse: bool = False):
        _set_item_tag(item_id, tag_name, tag_value, recurse)

    @staticmethod
    def set_tags(item_id: CIItemId, tags: Dict[str, Any], recurse: bool = False):
        _set_item_tags(item_id, tags, recurse)

    @staticmethod
    def delete_tag(item_id: CIItemId, tag_name: str, recurse: bool = False):
        _delete_item_tag(item_id, tag_name, recurse)

    @staticmethod
    def delete_tags(item_id: CIItemId, tag_names: List[str], recurse: bool = False):
        _delete_item_tags(item_id, tag_names, recurse)

    @staticmethod
    def get_span(item_id: CIItemId) -> Span:
        return _get_item_span(item_id)

    @staticmethod
    def is_finished(item_id: CIItemId) -> bool:
        return _is_item_finished(item_id)


class CISession(_CIVisibilityAPIBase):
    class DiscoverArgs(NamedTuple):
        test_command: str
        reject_duplicates: bool
        test_framework: str
        test_framework_version: str
        session_operation_name: str
        module_operation_name: str
        suite_operation_name: str
        test_operation_name: str
        root_dir: Optional[Path] = None

    @staticmethod
    @_catch_and_log_exceptions
    def discover(
        test_command: str,
        test_framework: str,
        test_framework_version: str,
        reject_duplicates: bool = True,
        session_operation_name: str = DEFAULT_OPERATION_NAMES.SESSION.value,
        module_operation_name: str = DEFAULT_OPERATION_NAMES.MODULE.value,
        suite_operation_name: str = DEFAULT_OPERATION_NAMES.SUITE.value,
        test_operation_name: str = DEFAULT_OPERATION_NAMES.TEST.value,
        root_dir: Optional[Path] = None,
    ):
        log.debug("Registering session with test command: %s", test_command)
        if not is_ci_visibility_enabled():
            log.debug("CI Visibility is not enabled, session not registered.")
            return

        core.dispatch(
            "ci_visibility.session.discover",
            (
                CISession.DiscoverArgs(
                    test_command,
                    reject_duplicates,
                    test_framework,
                    test_framework_version,
                    session_operation_name,
                    module_operation_name,
                    suite_operation_name,
                    test_operation_name,
                    root_dir,
                ),
            ),
        )

    @staticmethod
    @_catch_and_log_exceptions
    def start():
        log.debug("Starting session")
        core.dispatch("ci_visibility.session.start")

    class FinishArgs(NamedTuple):
        force_finish_children: bool
        override_status: Optional[CITestStatus]

    @staticmethod
    @_catch_and_log_exceptions
    def finish(
        force_finish_children: bool = False,
        override_status: Optional[CITestStatus] = None,
    ):
        log.debug("Finishing session, force_finish_session_modules: %s", force_finish_children)

        core.dispatch("ci_visibility.session.finish", (CISession.FinishArgs(force_finish_children, override_status),))

    @staticmethod
    def get_tag(tag_name: str) -> Any:
        return _get_item_tag(_CISessionId(), tag_name)

    @staticmethod
    def set_tag(tag_name: str, tag_value: Any, recurse: bool = False):
        _set_item_tag(_CISessionId(), tag_name, tag_value, recurse)

    @staticmethod
    def set_tags(tags: Dict[str, Any], recurse: bool = False):
        _set_item_tags(_CISessionId(), tags, recurse)

    @staticmethod
    def delete_tag(tag_name: str, recurse: bool = False):
        _delete_item_tag(_CISessionId(), tag_name, recurse)

    @staticmethod
    def delete_tags(tag_names: List[str], recurse: bool = False):
        _delete_item_tags(_CISessionId(), tag_names, recurse)

    @staticmethod
    def get_span() -> Span:
        return _get_item_span(_CISessionId())

    @staticmethod
    def is_finished() -> bool:
        return _is_item_finished(_CISessionId())

    @staticmethod
    @_catch_and_log_exceptions
    def get_codeowners() -> Optional[Codeowners]:
        log.debug("Getting codeowners object")

        codeowners: Optional[Codeowners] = core.dispatch_with_results(
            "ci_visibility.session.get_codeowners",
        ).codeowners.value
        return codeowners

    @staticmethod
    @_catch_and_log_exceptions
    def get_workspace_path() -> Path:
        log.debug("Getting session workspace path")

        workspace_path: Path = core.dispatch_with_results(
            "ci_visibility.session.get_workspace_path"
        ).workspace_path.value
        return workspace_path

    @staticmethod
    @_catch_and_log_exceptions
    def should_collect_coverage() -> bool:
        log.debug("Checking if coverage should be collected for session")

        _should_collect_coverage = bool(
            core.dispatch_with_results("ci_visibility.session.should_collect_coverage").should_collect_coverage.value
        )
        log.debug("Coverage should be collected: %s", _should_collect_coverage)

        return _should_collect_coverage

    @staticmethod
    @_catch_and_log_exceptions
    def is_test_skipping_enabled() -> bool:
        log.debug("Checking if test skipping is enabled")

        _is_test_skipping_enabled = bool(
            core.dispatch_with_results("ci_visibility.session.is_test_skipping_enabled").is_test_skipping_enabled.value
        )
        log.debug("Test skipping is enabled: %s", _is_test_skipping_enabled)

        return _is_test_skipping_enabled

    @staticmethod
    @_catch_and_log_exceptions
    def get_known_tests():
        pass


class CIModule(CIBase):
    class DiscoverArgs(NamedTuple):
        module_id: CIModuleId
        module_path: Optional[Path] = None

    class FinishArgs(NamedTuple):
        module_id: CIModuleId
        override_status: Optional[CITestStatus] = None
        force_finish_children: bool = False

    @staticmethod
    @_catch_and_log_exceptions
    def discover(item_id: CIModuleId, module_path: Optional[Path] = None):
        log.debug("Registered module %s", item_id)
        core.dispatch("ci_visibility.module.discover", (CIModule.DiscoverArgs(item_id, module_path),))

    @staticmethod
    @_catch_and_log_exceptions
    def start(item_id: CIModuleId):
        log.debug("Starting module %s", item_id)
        core.dispatch("ci_visibility.module.start", (item_id,))

    @staticmethod
    @_catch_and_log_exceptions
    def finish(
        item_id: CIModuleId,
        override_status: Optional[CITestStatus] = None,
        force_finish_children: bool = False,
    ):
        log.debug(
            "Finishing module %s, override_status: %s, force_finish_suites: %s",
            item_id,
            override_status,
            force_finish_children,
        )
        core.dispatch(
            "ci_visibility.module.finish", (CIModule.FinishArgs(item_id, override_status, force_finish_children),)
        )


class CIITRMixin(CIBase):
    """Mixin class for ITR-related functionality."""

    @staticmethod
    @_catch_and_log_exceptions
    def mark_itr_skipped(item_id: Union[CISuiteId, CITestId]):
        log.debug("Marking item %s as skipped by ITR", item_id)
        core.dispatch("ci_visibility.itr.finish_skipped_by_itr", (item_id,))

    @staticmethod
    @_catch_and_log_exceptions
    def mark_itr_unskippable(item_id: Union[CISuiteId, CITestId]):
        log.debug("Marking item %s as unskippable by ITR", item_id)
        core.dispatch("ci_visibility.itr.mark_unskippable", (item_id,))

    @staticmethod
    @_catch_and_log_exceptions
    def mark_itr_forced_run(item_id: Union[CISuiteId, CITestId]):
        log.debug("Marking item %s as unskippable by ITR", item_id)
        core.dispatch("ci_visibility.itr.mark_forced_run", (item_id,))

    @staticmethod
    @_catch_and_log_exceptions
    def was_forced_run(item_id: Union[CISuiteId, CITestId]) -> bool:
        """Skippable items are not currently tied to a test session, so no session ID is passed"""
        log.debug("Checking if item %s was forced to run", item_id)
        _was_forced_run = bool(
            core.dispatch_with_results("ci_visibility.itr.was_forced_run", (item_id,)).was_forced_run.value
        )
        log.debug("Item %s was forced run: %s", item_id, _was_forced_run)
        return _was_forced_run

    @staticmethod
    @_catch_and_log_exceptions
    def is_item_itr_skippable(item_id: Union[CISuiteId, CITestId]) -> bool:
        """Skippable items are not currently tied to a test session, so no session ID is passed"""
        log.debug("Checking if item %s is skippable", item_id)
        is_item_skippable = bool(
            core.dispatch_with_results("ci_visibility.itr.is_item_skippable", (item_id,)).is_item_skippable.value
        )
        log.debug("Item %s is skippable: %s", item_id, is_item_skippable)

        return is_item_skippable

    @staticmethod
    @_catch_and_log_exceptions
    def is_item_itr_unskippable(item_id: Union[CISuiteId, CITestId]) -> bool:
        """Skippable items are not currently tied to a test session, so no session ID is passed"""
        log.debug("Checking if item %s is unskippable", item_id)
        is_item_unskippable = bool(
            core.dispatch_with_results("ci_visibility.itr.is_item_unskippable", (item_id,)).is_item_unskippable.value
        )
        log.debug("Item %s is unskippable: %s", item_id, is_item_unskippable)

        return is_item_unskippable

    @staticmethod
    @_catch_and_log_exceptions
    def was_item_skipped_by_itr(item_id: Union[CISuiteId, CITestId]) -> bool:
        """Skippable items are not currently tied to a test session, so no session ID is passed"""
        log.debug("Checking if item %s was skipped by ITR", item_id)
        was_item_skipped = bool(
            core.dispatch_with_results("ci_visibility.itr.was_item_skipped", (item_id,)).was_item_skipped.value
        )
        log.debug("Item %s was skipped by ITR: %s", item_id, was_item_skipped)
        return was_item_skipped

    class AddCoverageArgs(NamedTuple):
        item_id: Union[_CIVisibilityChildItemIdBase, _CIVisibilityRootItemIdBase]
        coverage_data: Dict[Path, List[Tuple[int, int]]]

    @staticmethod
    @_catch_and_log_exceptions
    def add_coverage_data(item_id: Union[CISuiteId, CITestId], coverage_data: Dict[Path, List[Tuple[int, int]]]):
        log.debug("Adding coverage data for item %s: %s", item_id, coverage_data)
        core.dispatch("ci_visibility.item.add_coverage_data", (CIITRMixin.AddCoverageArgs(item_id, coverage_data),))


class CISuite(CIITRMixin, CIBase):
    class DiscoverArgs(NamedTuple):
        suite_id: CISuiteId
        codeowners: Optional[List[str]] = None
        source_file_info: Optional[CISourceFileInfo] = None

    @staticmethod
    @_catch_and_log_exceptions
    def discover(
        item_id: CISuiteId,
        codeowners: Optional[List[str]] = None,
        source_file_info: Optional[CISourceFileInfo] = None,
    ):
        """Registers a test suite with the CI Visibility service."""
        log.debug("Registering suite %s, source: %s", item_id, source_file_info)
        core.dispatch("ci_visibility.suite.discover", (CISuite.DiscoverArgs(item_id, codeowners, source_file_info),))

    @staticmethod
    @_catch_and_log_exceptions
    def start(item_id: CISuiteId):
        log.debug("Starting suite %s", item_id)
        core.dispatch("ci_visibility.suite.start", (item_id,))

    class FinishArgs(NamedTuple):
        suite_id: CISuiteId
        force_finish_children: bool = False
        override_status: Optional[CITestStatus] = None

    @staticmethod
    @_catch_and_log_exceptions
    def finish(
        item_id: CISuiteId,
        force_finish_children: bool = False,
        override_status: Optional[CITestStatus] = None,
    ):
        log.debug(
            "Finishing suite %s, override_status: %s, force_finish_children: %s",
            item_id,
            force_finish_children,
            override_status,
        )
        core.dispatch(
            "ci_visibility.suite.finish",
            (CISuite.FinishArgs(item_id, force_finish_children, override_status),),
        )


class CITest(CIITRMixin, CIBase):
    class DiscoverArgs(NamedTuple):
        test_id: CITestId
        codeowners: Optional[List[str]] = None
        source_file_info: Optional[CISourceFileInfo] = None
        resource: Optional[str] = None

    @staticmethod
    @_catch_and_log_exceptions
    def discover(
        item_id: CITestId,
        codeowners: Optional[List[str]] = None,
        source_file_info: Optional[CISourceFileInfo] = None,
        is_early_flake_detection: bool = False,
        resource: Optional[str] = None,
    ):
        """Registers a test with the CI Visibility service."""
        log.debug(
            "Discovering test %s, codeowners: %s, source file: %s, resource: %s",
            item_id,
            codeowners,
            source_file_info,
            resource,
        )
        core.dispatch(
            "ci_visibility.test.discover", (CITest.DiscoverArgs(item_id, codeowners, source_file_info, resource),)
        )

    class DiscoverEarlyFlakeRetryArgs(NamedTuple):
        test_id: CITestId
        retry_number: int

    @staticmethod
    @_catch_and_log_exceptions
    def discover_early_flake_retry(item_id: CITestId):
        if item_id.retry_number <= 0:
            log.warning(
                "Cannot register early flake retry of test %s with retry number %s", item_id, item_id.retry_number
            )
        log.debug("Registered early flake retry for test %s, retry number: %s", item_id, item_id.retry_number)
        original_test_id = CITestId(item_id.parent_id, item_id.name, item_id.parameters)
        core.dispatch(
            "ci_visibility.test.discover_early_flake_retry",
            (CITest.DiscoverEarlyFlakeRetryArgs(original_test_id, item_id.retry_number),),
        )

    @staticmethod
    @_catch_and_log_exceptions
    def start(item_id: CITestId):
        log.debug("Starting test %s", item_id)
        core.dispatch("ci_visibility.test.start", (item_id,))

    class FinishArgs(NamedTuple):
        test_id: CITestId
        status: CITestStatus
        skip_reason: Optional[str] = None
        exc_info: Optional[CIExcInfo] = None

    @staticmethod
    @_catch_and_log_exceptions
    def finish(
        item_id: CITestId,
        status: CITestStatus,
        skip_reason: Optional[str] = None,
        exc_info: Optional[CIExcInfo] = None,
    ):
        log.debug(
            "Finishing test %s, status: %s, skip_reason: %s, exc_info: %s",
            item_id,
            status,
            skip_reason,
            exc_info,
        )
        core.dispatch(
            "ci_visibility.test.finish",
            (CITest.FinishArgs(item_id, status, skip_reason=skip_reason, exc_info=exc_info),),
        )

    @staticmethod
    @_catch_and_log_exceptions
    def mark_unskippable():
        log.debug("Marking test as unskippable")

    @staticmethod
    @_catch_and_log_exceptions
    def mark_pass(item_id: CITestId):
        log.debug("Marking test %s as passed", item_id)
        CITest.finish(item_id, CITestStatus.PASS)

    @staticmethod
    @_catch_and_log_exceptions
    def mark_fail(item_id: CITestId, exc_info: Optional[CIExcInfo] = None):
        log.debug("Marking test %s as failed, exc_info: %s", item_id, exc_info)
        CITest.finish(item_id, CITestStatus.FAIL, exc_info=exc_info)

    @staticmethod
    @_catch_and_log_exceptions
    def mark_skip(item_id: CITestId, skip_reason: Optional[str] = None):
        log.debug("Marking test %s as skipped, skip reason: %s", item_id, skip_reason)
        CITest.finish(item_id, CITestStatus.SKIP, skip_reason=skip_reason)
