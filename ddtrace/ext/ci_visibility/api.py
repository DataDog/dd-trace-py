"""
Provides the API necessary to interacting with the CI Visibility service.

All functions in this module are meant to be called in a stateless manner. Test runners (or custom implementations) that
rely on this API are not expected to keep CI Visibility-related state for each session, module, suite or test.

Stable values of module, suite, test names, and parameters, are a necessity for this API to function properly.

All types and methods for interacting with the API are provided and documented in this file.
"""
import dataclasses
from enum import Enum
from typing import Any
from typing import Dict
from typing import NamedTuple
from typing import Optional
from typing import Tuple

from typing_extensions import Unpack

from ddtrace.ext.ci_visibility._ci_visibility_base import _CIVisibilityChildItemIdBase
from ddtrace.ext.ci_visibility._ci_visibility_base import _CIVisibilityAPIBase
from ddtrace.ext.ci_visibility._ci_visibility_base import _CIVisibilityChildItemIdBase
from ddtrace.ext.ci_visibility._ci_visibility_base import _CIVisibilityOrphanItemIdBase
from ddtrace.ext.ci_visibility.util import _catch_and_log_exceptions
from ddtrace.ext.test import Status as TestStatus
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class CITestStatus(Enum):
    PASS = TestStatus.PASS
    FAIL = TestStatus.FAIL
    SKIP = TestStatus.SKIP
    XFAIL = TestStatus.XFAIL
    XPASS = TestStatus.XPASS


DEFAULT_SESSION_NAME = "ci_visibility_session"


@dataclasses.dataclass(frozen=True)
class CISessionId(_CIVisibilityOrphanItemIdBase):
    name: str = DEFAULT_SESSION_NAME

    def __repr__(self):
        return "CISessionId(name={})".format(self.name)


# @dataclasses.dataclass(frozen=True)
# class CISessionId(CISessionId):
#     pass


@dataclasses.dataclass(frozen=True)
class CIModuleId(_CIVisibilityChildItemIdBase[CISessionId]):
    # parent_id: CISessionId

    def __repr__(self):
        return "CIModuleId(session={}, module={})".format(self.get_session_id().name, self.name)


c = CIModuleId(parent_id=CISessionId(), name="module")
p = c.parent_id
s = c.get_session_id()

@dataclasses.dataclass(frozen=True)
class CIModuleIdType(_CIVisibilityChildItemIdBase):
    pass


@dataclasses.dataclass(frozen=True)
class CISuiteId(_CIVisibilityChildItemIdBase[CIModuleIdType]):
    # parent_id: CIModuleIdType

    def __repr__(self):
        return "CISuiteId(session={}, module={}, suite={})".format(
            self.get_session_id().name, self.parent_id.name, self.name
        )


# @dataclasses.dataclass(frozen=True)
# class CISuiteId(CIVisibilityChildItemIdType):
#     pass


@dataclasses.dataclass(frozen=True)
class CITestId(_CIVisibilityChildItemIdBase[CISuiteId]):
    # parent_id: CISuiteId
    test_parameters: Optional[Tuple[Unpack[Tuple[str, Any]]]] = None
    retry_number: int = 0

    def __repr__(self):
        return "CITestId(session={}, module={}, suite={}, test={}, parameters={}, retry_number={})".format(
            self.get_session_id().name,
            self.parent_id.parent_id.name,
            self.parent_id.name,
            self.name,
            self.test_parameters,
            self.retry_number,
        )


# @dataclasses.dataclass(frozen=True)
# class CITestIdType(CIVisibilityChildItemIdType):
#     pass


@dataclasses.dataclass(frozen=True)
class CISourceFileInfo:
    source_file_path: str
    source_file_line_start: int
    source_file_line_end: int


class CISession(_CIVisibilityAPIBase):
    class DiscoverArgs(NamedTuple):
        session_id: CISessionId
        test_command: str
        session_operation_name: Optional[str]
        module_operation_name: Optional[str]
        suite_operation_name: Optional[str]
        test_operation_name: Optional[str]
        reject_unknown_items: bool
        reject_duplicates: bool

    class FinishArgs(NamedTuple):
        session_id: CISessionId
        force_finish_children: bool
        override_status: Optional[CITestStatus]

    @staticmethod
    @_catch_and_log_exceptions
    def discover(
        item_id: Optional[CISessionId],
        test_command: str,
        session_operation_name: Optional[str] = None,
        module_operation_name: Optional[str] = None,
        suite_operation_name: Optional[str] = None,
        test_operation_name: Optional[str] = None,
        reject_unknown_items: bool = True,
        reject_duplicates: bool = True,
    ):
        item_id = item_id or CISessionId()

        log.debug("Registering session %s with test command: %s", item_id, test_command)
        from ddtrace.internal.ci_visibility import CIVisibility

        CIVisibility.enable()
        if not CIVisibility.enabled:
            log.debug("CI Visibility enabling failed, session not registered.")
            return

        core.dispatch(
            "ci_visibility.session.register",
            (
                CISession.DiscoverArgs(
                    item_id,
                    test_command,
                    session_operation_name,
                    module_operation_name,
                    suite_operation_name,
                    test_operation_name,
                    reject_unknown_items,
                    reject_duplicates,
                ),
            ),
        )

    @staticmethod
    @_catch_and_log_exceptions
    def start(item_id: Optional[CISessionId] = None):
        log.debug("Starting session")

        item_id = item_id or CISessionId()
        core.dispatch("ci_visibility.session.start", (item_id,))

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
    def get_skippable_items(item_id: Optional[CISessionId] = None):
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def get_settings(item_id: Optional[CISessionId] = None):
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def get_known_tests(item_id: Optional[CISessionId] = None):
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def set_tag(tag_name: str, tag_value: str, item_id: Optional[CISessionId] = None, recurse: bool = False):
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def set_tags(tags: Dict[str, str], item_id: Optional[CISessionId] = None, recurse: bool = False):
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def delete_tag(tag_name: str, item_id: Optional[CISessionId] = None, recurse: bool = False):
        pass

    @staticmethod
    @_catch_and_log_exceptions
    def delete_tags(tag_name: str, item_id: Optional[CISessionId] = None, recurse: bool = False):
        pass


class CIModule(_CIVisibilityAPIBase):
    class DiscoverArgs(NamedTuple):
        module_id: CIModuleIdType

    class FinishArgs(NamedTuple):
        module_id: CIModuleIdType
        override_status: Optional[CITestStatus] = None
        force_finish_children: bool = False

    @staticmethod
    @_catch_and_log_exceptions
    def discover(item_id: CIModuleIdType):
        log.debug("Registered module %s", item_id)
        core.dispatch("ci_visibility.module.discover", (CIModule.DiscoverArgs(module_id=item_id),))

    @staticmethod
    @_catch_and_log_exceptions
    def start(item_id: CIModuleIdType):
        log.debug("Starting module %s", item_id)
        core.dispatch("ci_visibility.module.start", (item_id,))

    @staticmethod
    @_catch_and_log_exceptions
    def finish(
        item_id: CIModuleIdType,
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


class CISuite(_CIVisibilityAPIBase):
    class DiscoverArgs(NamedTuple):
        suite_id: CISuiteId
        codeowner: Optional[str] = None
        source_file_info: Optional[CISourceFileInfo] = None

    class FinishArgs(NamedTuple):
        suite_id: CISuiteId
        force_finish_children: bool = False
        override_status: Optional[CITestStatus] = None

    @staticmethod
    @_catch_and_log_exceptions
    def discover(
        item_id: CISuiteId, codeowner: Optional[str] = None, source_file_info: Optional[CISourceFileInfo] = None
    ):
        """Registers a test suite with the CI Visibility service."""
        log.debug("Registering suite %s, source: %s", item_id, source_file_info)
        core.dispatch("ci_visibility.suite.discover", (CISuite.DiscoverArgs(item_id, codeowner, source_file_info),))

    @staticmethod
    @_catch_and_log_exceptions
    def start(item_id: CISuiteId):
        log.debug("Starting suite %s", item_id)
        core.dispatch("ci_visibility.suite.start", (item_id,))

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
            "ci_visibility.suite.finish", (CISuite.FinishArgs(item_id, force_finish_children, override_status),)
        )

    @staticmethod
    @_catch_and_log_exceptions
    def add_coverage_data(item_id: CITestId, coverage_data: Dict[str, Any]):
        log.debug("Adding coverage data for suite %s: %s", item_id, coverage_data)


class CITest(_CIVisibilityAPIBase):
    class DiscoverArgs(NamedTuple):
        test_id: CITestId
        codeowner: Optional[str] = None
        source_file_info: Optional[CISourceFileInfo] = None

    class DiscoverEarlyFlakeRetryArgs(NamedTuple):
        test_id: CITestId
        retry_number: int

    class FinishArgs(NamedTuple):
        test_id: CITestId
        override_status: Optional[CITestStatus] = None

    @staticmethod
    @_catch_and_log_exceptions
    def discover(
        item_id: CITestId,
        codeowner: Optional[str] = None,
        source_file_info: Optional[CISourceFileInfo] = None,
        is_early_flake_detection: bool = False,
    ):
        """Registers a test with the CI Visibility service."""
        log.debug(
            "Discovering test %s, codeowner: %s, source file: %s",
            item_id,
            codeowner,
            source_file_info,
        )
        core.dispatch("ci_visibility.test.discover", (CITest.DiscoverArgs(item_id, codeowner, source_file_info),))

    @staticmethod
    @_catch_and_log_exceptions
    def discover_early_flake_retry(item_id: CITestId):
        if item_id.retry_number <= 0:
            log.warning(
                "Cannot register early flake retry of test %s with retry number %s", item_id, item_id.retry_number
            )
        log.warning("Registered early flake retry for test %s, retry number: %s", item_id, item_id.retry_number)
        original_test_id = CITestId(item_id.name, item_id.parent_id, item_id.test_parameters)
        core.dispatch(
            "ci_visibility.test.discover_early_flake_retry",
            (CITest.DiscoverEarlyFlakeRetryArgs(original_test_id, item_id.retry_number),),
        )

    @staticmethod
    @_catch_and_log_exceptions
    def start(item_id: CITestId):
        log.debug("Starting test %s", item_id)
        core.dispatch("ci_visibility.test.start", (item_id,))

    @staticmethod
    @_catch_and_log_exceptions
    def finish(item_id: CITestId, status: CITestStatus, reason: Optional[str] = None, exc_info: Optional[Any] = None):
        log.debug("Finishing test %s, status: %s, reason: %s", item_id, status, reason, exc_info)
        core.dispatch("ci_visibility.test.finish", (CITest.FinishArgs(item_id, status),))

    @staticmethod
    @_catch_and_log_exceptions
    def add_coverage_data(item_id: CITestId, coverage_data: Dict[str, Any]):
        log.debug("Adding coverage data for test %s: %s", item_id, coverage_data)
