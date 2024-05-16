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

from ddtrace.ext.ci_visibility._ci_visibility_base import CIItemId
from ddtrace.ext.ci_visibility._ci_visibility_base import CISourceFileInfoBase
from ddtrace.ext.ci_visibility._ci_visibility_base import _CIVisibilityAPIBase
from ddtrace.ext.ci_visibility._ci_visibility_base import _CIVisibilityChildItemIdBase
from ddtrace.ext.ci_visibility._ci_visibility_base import _CIVisibilityRootItemIdBase
from ddtrace.ext.ci_visibility.util import _catch_and_log_exceptions
from ddtrace.ext.test import Status as TestStatus
from ddtrace.internal import core
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
    UNSET = "ci_visibility.unset"


@dataclasses.dataclass(frozen=True)
class CISessionId(_CIVisibilityRootItemIdBase):
    name: str = DEFAULT_SESSION_NAME

    def __repr__(self):
        return "CISessionId(name={})".format(self.name)


@dataclasses.dataclass(frozen=True)
class CIModuleId(_CIVisibilityChildItemIdBase[CISessionId]):
    def __repr__(self):
        return "CIModuleId(session={}, module={})".format(self.get_session_id().name, self.name)


@dataclasses.dataclass(frozen=True)
class CISuiteId(_CIVisibilityChildItemIdBase[CIModuleId]):
    def __repr__(self):
        return "CISuiteId(session={}, module={}, suite={})".format(
            self.get_session_id().name, self.parent_id.name, self.name
        )


@dataclasses.dataclass(frozen=True)
class CITestId(_CIVisibilityChildItemIdBase[CISuiteId]):
    parameters: Optional[str] = None  # For hashability, a JSON string of a dictionary of parameters
    retry_number: int = 0

    def __repr__(self):
        return "CITestId(session={}, module={}, suite={}, test={}, parameters={}, retry_number={})".format(
            self.get_session_id().name,
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
def enable_ci_visibility():
    from ddtrace.internal.ci_visibility import CIVisibility

    CIVisibility.enable()
    if not CIVisibility.enabled:
        log.warning("CI Visibility enabling failed.")


@_catch_and_log_exceptions
def disable_ci_visibility():
    from ddtrace.internal.ci_visibility import CIVisibility

    CIVisibility.disable()
    if CIVisibility.enabled:
        log.warning("CI Visibility disabling failed.")


class CIBase(_CIVisibilityAPIBase):
    @staticmethod
    def set_tag(item_id: CIItemId, tag_name: str, tag_value: Any, recurse: bool = False):
        log.debug("Setting tag for item %s: %s=%s", item_id, tag_name, tag_value)
        core.dispatch("ci_visibility.item.set_tag", (CIBase.SetTagArgs(item_id, tag_name, tag_value),))

    @staticmethod
    def set_tags(item_id: CIItemId, tags: Dict[str, Any], recurse: bool = False):
        log.debug("Setting tags for item %s: %s", item_id, tags)
        core.dispatch("ci_visibility.item.set_tags", (CIBase.SetTagsArgs(item_id, tags),))

    @staticmethod
    def delete_tag(item_id: CIItemId, tag_name: str, recurse: bool = False):
        log.debug("Deleting tag for item %s: %s", item_id, tag_name)
        core.dispatch("ci_visibility.item.delete_tag", (CIBase.DeleteTagArgs(item_id, tag_name),))

    @staticmethod
    def delete_tags(item_id: CIItemId, tag_names: List[str], recurse: bool = False):
        log.debug("Deleting tags for item %s: %s", item_id, tag_names)
        core.dispatch("ci_visibility.item.delete_tags", (CIBase.DeleteTagsArgs(item_id, tag_names),))


class CISession(CIBase):
    class DiscoverArgs(NamedTuple):
        session_id: CISessionId
        test_command: str
        reject_unknown_items: bool
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
        item_id: Optional[CISessionId],
        test_command: str,
        test_framework: str,
        test_framework_version: str,
        reject_unknown_items: bool = True,
        reject_duplicates: bool = True,
        session_operation_name: str = DEFAULT_OPERATION_NAMES.SESSION.value,
        module_operation_name: str = DEFAULT_OPERATION_NAMES.MODULE.value,
        suite_operation_name: str = DEFAULT_OPERATION_NAMES.SUITE.value,
        test_operation_name: str = DEFAULT_OPERATION_NAMES.TEST.value,
        root_dir: Optional[Path] = None,
    ):
        item_id = item_id or CISessionId()

        log.debug("Registering session %s with test command: %s", item_id, test_command)
        from ddtrace.internal.ci_visibility import CIVisibility

        CIVisibility.enable()
        if not CIVisibility.enabled:
            log.debug("CI Visibility enabling failed, session not registered.")
            return

        core.dispatch(
            "ci_visibility.session.discover",
            (
                CISession.DiscoverArgs(
                    item_id,
                    test_command,
                    reject_unknown_items,
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
    def start(item_id: Optional[CISessionId] = None):
        log.debug("Starting session")

        item_id = item_id or CISessionId()
        core.dispatch("ci_visibility.session.start", (item_id,))

    class FinishArgs(NamedTuple):
        session_id: CISessionId
        force_finish_children: bool
        override_status: Optional[CITestStatus]

    @staticmethod
    @_catch_and_log_exceptions
    def finish(
        item_id: Optional[CISessionId] = None,
        force_finish_children: bool = False,
        override_status: Optional[CITestStatus] = None,
    ):
        log.debug("Finishing session, force_finish_session_modules: %s", force_finish_children)

        item_id = item_id or CISessionId()
        core.dispatch(
            "ci_visibility.session.finish", (CISession.FinishArgs(item_id, force_finish_children, override_status),)
        )

    @staticmethod
    @_catch_and_log_exceptions
    def get_known_tests(item_id: Optional[CISessionId] = None):
        pass


class CIModule(CIBase):
    class DiscoverArgs(NamedTuple):
        module_id: CIModuleId

    class FinishArgs(NamedTuple):
        module_id: CIModuleId
        override_status: Optional[CITestStatus] = None
        force_finish_children: bool = False

    @staticmethod
    @_catch_and_log_exceptions
    def discover(item_id: CIModuleId):
        log.debug("Registered module %s", item_id)
        core.dispatch("ci_visibility.module.discover", (CIModule.DiscoverArgs(item_id),))

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
        log.debug("Marking test %s as skipped by ITR", item_id)
        core.dispatch("ci_visibility.itr.finish_skipped_by_itr", (item_id,))

    @staticmethod
    @_catch_and_log_exceptions
    def mark_itr_unskippable(item_id: Union[CISuiteId, CITestId]):
        log.debug("Marking test %s as unskippable by ITR", item_id)
        core.dispatch("ci_visibility.itr.mark_unskippable", (item_id,))

    @staticmethod
    @_catch_and_log_exceptions
    def mark_itr_forced_run(item_id: Union[CISuiteId, CITestId]):
        log.debug("Marking test %s as unskippable by ITR", item_id)
        core.dispatch("ci_visibility.itr.mark_forced_run", (item_id,))

    @staticmethod
    @_catch_and_log_exceptions
    def is_item_itr_skippable(item_id: Union[CISuiteId, CITestId]) -> bool:
        """Skippable items are not currently tied to a test session, so no session ID is passed"""
        log.debug("Getting skippable items")
        is_item_skippable: Optional[bool] = core.dispatch_with_results(
            "ci_visibility.itr.is_item_skippable", (item_id,)
        ).is_item_skippable.value
        return bool(is_item_skippable)

    class AddCoverageArgs(NamedTuple):
        item_id: Union[_CIVisibilityChildItemIdBase, _CIVisibilityRootItemIdBase]
        coverage_data: Dict[Path, List[Tuple[int, int]]]

    @staticmethod
    @_catch_and_log_exceptions
    def add_coverage_data(item_id: Union[CISuiteId, CITestId], coverage_data: Dict[Path, List[Tuple[int, int]]]):
        log.debug("Adding coverage data for test %s: %s", item_id, coverage_data)
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

    @staticmethod
    @_catch_and_log_exceptions
    def discover(
        item_id: CITestId,
        codeowners: Optional[List[str]] = None,
        source_file_info: Optional[CISourceFileInfo] = None,
        is_early_flake_detection: bool = False,
    ):
        """Registers a test with the CI Visibility service."""
        log.debug(
            "Discovering test %s, codeowners: %s, source file: %s",
            item_id,
            codeowners,
            source_file_info,
        )
        core.dispatch("ci_visibility.test.discover", (CITest.DiscoverArgs(item_id, codeowners, source_file_info),))

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
