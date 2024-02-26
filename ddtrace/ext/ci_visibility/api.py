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
from typing import Optional

from ddtrace.ext.ci_visibility._ci_visibility_base import _CIVisibilityAPIBase
from ddtrace.ext.ci_visibility._ci_visibility_base import _CIVisibilityItemIdBase
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
class CISessionId(_CIVisibilityItemIdBase):
    session_name: str = DEFAULT_SESSION_NAME

    def get_parent(self):
        raise NotImplementedError("Sessions do not have parents")

    def get_session_id(self):
        raise NotImplementedError("Current id is a session id")


@dataclasses.dataclass(frozen=True)
class CIModuleId(_CIVisibilityItemIdBase):
    session_id: CISessionId
    module_name: str

    def get_parent_id(self):
        return self.session_id

    def get_session_id(self):
        return self.get_parent_id()


@dataclasses.dataclass(frozen=True)
class CISuiteId(_CIVisibilityItemIdBase):
    module_id: CIModuleId
    suite_name: str

    def get_parent_id(self):
        return self.module_id

    def get_session_id(self):
        return self.get_parent_id().get_session_id()


@dataclasses.dataclass(frozen=True)
class CITestId(_CIVisibilityItemIdBase):
    suite_id: CISuiteId
    test_name: str
    test_parameters: Optional[Dict[str, Any]] = None
    retry_number: Optional[int] = 0

    def get_parent_id(self):
        return self.suite_id

    def get_session_id(self):
        return self.get_parent_id().get_session_id()


@dataclasses.dataclass(frozen=True)
class CISourceFileInfo:
    source_file_path: str
    source_file_line_start: int
    source_file_line_end: int


class CISession(_CIVisibilityAPIBase):
    @staticmethod
    def _get_default_session_id() -> CISessionId:
        return CISessionId()

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
                item_id,
                test_command,
                session_operation_name,
                module_operation_name,
                suite_operation_name,
                test_operation_name,
                reject_unknown_items,
                reject_duplicates,
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
        core.dispatch("ci_visibility.session.finish", (item_id, force_finish_children, override_status))

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
        item_id = item_id or CISessionId()
        super().set_tag(tag_name, tag_value, item_id, recurse)

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
    @staticmethod
    @_catch_and_log_exceptions
    def discover(item_id: CIModuleId):
        log.debug("Registered module %s", item_id)

    @staticmethod
    @_catch_and_log_exceptions
    def start(item_id: CIModuleId):
        log.debug("Starting module %s", item_id)

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


class CISuite(_CIVisibilityAPIBase):
    @staticmethod
    @_catch_and_log_exceptions
    def discover(item_id: CISuiteId, source_file_info: Optional[CISourceFileInfo] = None):
        """Registers a test suite with the CI Visibility service."""
        log.debug("Registering suite %s, source: %s", item_id, source_file_info)

    @staticmethod
    @_catch_and_log_exceptions
    def start(item_id: CISuiteId):
        log.debug("Starting suite %s", item_id)

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
            override_status,
            force_finish_children,
        )

    @staticmethod
    @_catch_and_log_exceptions
    def add_coverage_data(item_id: CITestId, coverage_data: Dict[str, any]):
        log.debug("Adding coverage data for suite %s: %s", item_id, coverage_data)


class CITest(_CIVisibilityAPIBase):
    @staticmethod
    @_catch_and_log_exceptions
    def discover(
        item_id: CITestId,
        codeowner: Optional[str] = None,
        source_file_info: Optional[CISourceFileInfo] = None,
    ):
        """Registers a test with the CI Visibility service."""
        log.debug(
            "Registered test %s, codeowner: %s, source file: %s, is_early_flake_detection %s",
            item_id,
            codeowner,
            source_file_info,
        )
        core.dispatch("ci_visibility.test.register", (item_id, codeowner, source_file_info))

    @staticmethod
    def discover_early_flake_retry(item_id: CITestId):
        if item_id.retry_number == 0:
            log.debug("Registered early flake retry for test %s", item_id)
        log.debug("Registered early flake retry for test %s, retry number: %s", item_id, item_id.retry_number)

    @staticmethod
    @_catch_and_log_exceptions
    def start(item_id: CITestId):
        log.debug("Starting test %s", item_id)

    @staticmethod
    @_catch_and_log_exceptions
    def finish(item_id: CITestId, status: CITestStatus, reason: Optional[str] = None, exc_info: Optional[any] = None):
        log.debug("Finishing test %s, status: %s, reason: %s, exception: %s", item_id, status, reason, exc_info)

    @staticmethod
    @_catch_and_log_exceptions
    def add_coverage_data(item_id: CITestId, coverage_data: Dict[str, any]):
        log.debug("Adding coverage data for test %s: %s", item_id, coverage_data)
