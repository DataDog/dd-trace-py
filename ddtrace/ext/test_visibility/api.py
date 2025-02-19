"""
Provides the API necessary to interacting with the Test Visibility service.

NOTE: BETA - this API is currently in development and is subject to change.

This API supports the agentless, and agent-proxied (EVP) modes. It does not support the APM protocol.

All functions in this module are meant to be called in a stateless manner. Test runners (or custom implementations) that
rely on this API are not expected to keep Test Visibility-related state for each session, module, suite or test.

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
from typing import Type

from ddtrace.ext.test import Status as _TestStatus
from ddtrace.ext.test_visibility._item_ids import TestId
from ddtrace.ext.test_visibility._item_ids import TestModuleId
from ddtrace.ext.test_visibility._item_ids import TestSuiteId
from ddtrace.ext.test_visibility._test_visibility_base import TestSessionId
from ddtrace.ext.test_visibility._test_visibility_base import TestSourceFileInfoBase
from ddtrace.ext.test_visibility._test_visibility_base import TestVisibilityItemId
from ddtrace.ext.test_visibility._test_visibility_base import _TestVisibilityAPIBase
from ddtrace.ext.test_visibility._utils import _catch_and_log_exceptions
from ddtrace.ext.test_visibility._utils import _delete_item_tag
from ddtrace.ext.test_visibility._utils import _delete_item_tags
from ddtrace.ext.test_visibility._utils import _get_item_tag
from ddtrace.ext.test_visibility._utils import _is_item_finished
from ddtrace.ext.test_visibility._utils import _set_item_tag
from ddtrace.ext.test_visibility._utils import _set_item_tags
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger as _get_logger


log = _get_logger(__name__)

# this triggers the registration of trace handlers after civis startup
import ddtrace._trace.trace_handlers  # noqa: F401, E402


class TestStatus(Enum):
    __test__ = False
    PASS = _TestStatus.PASS.value
    FAIL = _TestStatus.FAIL.value
    SKIP = _TestStatus.SKIP.value
    XFAIL = _TestStatus.XFAIL.value
    XPASS = _TestStatus.XPASS.value


DEFAULT_SESSION_NAME = "test_visibility_session"


class DEFAULT_OPERATION_NAMES(Enum):
    SESSION = "test_visibility.session"
    MODULE = "test_visibility.module"
    SUITE = "test_visibility.suite"
    TEST = "test_visibility.test"


@dataclasses.dataclass(frozen=True)
class TestSourceFileInfo(TestSourceFileInfoBase):
    path: Path
    start_line: Optional[int] = None
    end_line: Optional[int] = None


@dataclasses.dataclass(frozen=True)
class TestExcInfo:
    __test__ = False
    exc_type: Type[BaseException]
    exc_value: BaseException
    exc_traceback: TracebackType


@_catch_and_log_exceptions
def enable_test_visibility(config: Optional[Any] = None):
    log.debug("Enabling Test Visibility with config: %s", config)
    core.dispatch("test_visibility.enable", (config,))

    if not is_test_visibility_enabled():
        log.warning("Failed to enable Test Visibility")


@_catch_and_log_exceptions
def is_test_visibility_enabled():
    return core.dispatch_with_results("test_visibility.is_enabled").is_enabled.value


@_catch_and_log_exceptions
def disable_test_visibility():
    log.debug("Disabling Test Visibility")
    core.dispatch("test_visibility.disable")
    if is_test_visibility_enabled():
        log.warning("Failed to disable Test Visibility")


class TestBase(_TestVisibilityAPIBase):
    @staticmethod
    def get_tag(item_id: TestVisibilityItemId, tag_name: str) -> Any:
        return _get_item_tag(item_id, tag_name)

    @staticmethod
    def set_tag(item_id: TestVisibilityItemId, tag_name: str, tag_value: Any, recurse: bool = False):
        _set_item_tag(item_id, tag_name, tag_value, recurse)

    @staticmethod
    def set_tags(item_id: TestVisibilityItemId, tags: Dict[str, Any], recurse: bool = False):
        _set_item_tags(item_id, tags, recurse)

    @staticmethod
    def delete_tag(item_id: TestVisibilityItemId, tag_name: str, recurse: bool = False):
        _delete_item_tag(item_id, tag_name, recurse)

    @staticmethod
    def delete_tags(item_id: TestVisibilityItemId, tag_names: List[str], recurse: bool = False):
        _delete_item_tags(item_id, tag_names, recurse)

    @staticmethod
    def is_finished(item_id: TestVisibilityItemId) -> bool:
        return _is_item_finished(item_id)


class TestSession(_TestVisibilityAPIBase):
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
        if not is_test_visibility_enabled():
            log.debug("Test Visibility is not enabled, session not registered.")
            return

        core.dispatch(
            "test_visibility.session.discover",
            (
                TestSession.DiscoverArgs(
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
        core.dispatch("test_visibility.session.start")

    class FinishArgs(NamedTuple):
        force_finish_children: bool
        override_status: Optional[TestStatus]

    @staticmethod
    @_catch_and_log_exceptions
    def finish(
        force_finish_children: bool = False,
        override_status: Optional[TestStatus] = None,
    ):
        log.debug("Finishing session, force_finish_session_modules: %s", force_finish_children)

        core.dispatch(
            "test_visibility.session.finish", (TestSession.FinishArgs(force_finish_children, override_status),)
        )

    @staticmethod
    def get_tag(tag_name: str) -> Any:
        return _get_item_tag(TestSessionId(), tag_name)

    @staticmethod
    def set_tag(tag_name: str, tag_value: Any, recurse: bool = False):
        _set_item_tag(TestSessionId(), tag_name, tag_value, recurse)

    @staticmethod
    def set_tags(tags: Dict[str, Any], recurse: bool = False):
        _set_item_tags(TestSessionId(), tags, recurse)

    @staticmethod
    def delete_tag(tag_name: str, recurse: bool = False):
        _delete_item_tag(TestSessionId(), tag_name, recurse)

    @staticmethod
    def delete_tags(tag_names: List[str], recurse: bool = False):
        _delete_item_tags(TestSessionId(), tag_names, recurse)


class TestModule(TestBase):
    class DiscoverArgs(NamedTuple):
        module_id: TestModuleId
        module_path: Optional[Path] = None

    class FinishArgs(NamedTuple):
        module_id: TestModuleId
        override_status: Optional[TestStatus] = None
        force_finish_children: bool = False

    @staticmethod
    @_catch_and_log_exceptions
    def discover(item_id: TestModuleId, module_path: Optional[Path] = None):
        log.debug("Registered module %s", item_id)
        core.dispatch("test_visibility.module.discover", (TestModule.DiscoverArgs(item_id, module_path),))

    @staticmethod
    @_catch_and_log_exceptions
    def start(item_id: TestModuleId):
        log.debug("Starting module %s", item_id)
        core.dispatch("test_visibility.module.start", (item_id,))

    @staticmethod
    @_catch_and_log_exceptions
    def finish(
        item_id: TestModuleId,
        override_status: Optional[TestStatus] = None,
        force_finish_children: bool = False,
    ):
        log.debug(
            "Finishing module %s, override_status: %s, force_finish_suites: %s",
            item_id,
            override_status,
            force_finish_children,
        )
        core.dispatch(
            "test_visibility.module.finish", (TestModule.FinishArgs(item_id, override_status, force_finish_children),)
        )


class TestSuite(TestBase):
    class DiscoverArgs(NamedTuple):
        suite_id: TestSuiteId
        codeowners: Optional[List[str]] = None
        source_file_info: Optional[TestSourceFileInfo] = None

    @staticmethod
    @_catch_and_log_exceptions
    def discover(
        item_id: TestSuiteId,
        codeowners: Optional[List[str]] = None,
        source_file_info: Optional[TestSourceFileInfo] = None,
    ):
        """Registers a test suite with the Test Visibility service."""
        log.debug("Registering suite %s, source: %s", item_id, source_file_info)
        core.dispatch(
            "test_visibility.suite.discover", (TestSuite.DiscoverArgs(item_id, codeowners, source_file_info),)
        )

    @staticmethod
    @_catch_and_log_exceptions
    def start(item_id: TestSuiteId):
        log.debug("Starting suite %s", item_id)
        core.dispatch("test_visibility.suite.start", (item_id,))

    class FinishArgs(NamedTuple):
        suite_id: TestSuiteId
        force_finish_children: bool = False
        override_status: Optional[TestStatus] = None

    @staticmethod
    @_catch_and_log_exceptions
    def finish(
        item_id: TestSuiteId,
        force_finish_children: bool = False,
        override_status: Optional[TestStatus] = None,
    ):
        log.debug(
            "Finishing suite %s, override_status: %s, force_finish_children: %s",
            item_id,
            force_finish_children,
            override_status,
        )
        core.dispatch(
            "test_visibility.suite.finish",
            (TestSuite.FinishArgs(item_id, force_finish_children, override_status),),
        )


class Test(TestBase):
    class DiscoverArgs(NamedTuple):
        test_id: TestId
        codeowners: Optional[List[str]] = None
        source_file_info: Optional[TestSourceFileInfo] = None
        resource: Optional[str] = None

    @staticmethod
    @_catch_and_log_exceptions
    def discover(
        item_id: TestId,
        codeowners: Optional[List[str]] = None,
        source_file_info: Optional[TestSourceFileInfo] = None,
        resource: Optional[str] = None,
    ):
        """Registers a test with the Test Visibility service."""
        log.debug(
            "Discovering test %s, codeowners: %s, source file: %s, resource: %s",
            item_id,
            codeowners,
            source_file_info,
            resource,
        )
        core.dispatch(
            "test_visibility.test.discover", (Test.DiscoverArgs(item_id, codeowners, source_file_info, resource),)
        )

    @staticmethod
    @_catch_and_log_exceptions
    def start(item_id: TestId):
        log.debug("Starting test %s", item_id)
        core.dispatch("test_visibility.test.start", (item_id,))

    class FinishArgs(NamedTuple):
        test_id: TestId
        status: TestStatus
        skip_reason: Optional[str] = None
        exc_info: Optional[TestExcInfo] = None

    @staticmethod
    @_catch_and_log_exceptions
    def finish(
        item_id: TestId,
        status: TestStatus,
        skip_reason: Optional[str] = None,
        exc_info: Optional[TestExcInfo] = None,
    ):
        log.debug(
            "Finishing test %s, status: %s, skip_reason: %s, exc_info: %s",
            item_id,
            status,
            skip_reason,
            exc_info,
        )
        core.dispatch(
            "test_visibility.test.finish",
            (Test.FinishArgs(item_id, status, skip_reason=skip_reason, exc_info=exc_info),),
        )

    @staticmethod
    @_catch_and_log_exceptions
    def set_parameters(item_id: TestId, params: str):
        log.debug("Setting test %s parameters to %s", item_id, params)
        core.dispatch("test_visibility.test.set_parameters", (item_id, params))

    @staticmethod
    @_catch_and_log_exceptions
    def mark_pass(item_id: TestId):
        log.debug("Marking test %s as passed", item_id)
        Test.finish(item_id, TestStatus.PASS)

    @staticmethod
    @_catch_and_log_exceptions
    def mark_fail(item_id: TestId, exc_info: Optional[TestExcInfo] = None):
        log.debug("Marking test %s as failed, exc_info: %s", item_id, exc_info)
        Test.finish(item_id, TestStatus.FAIL, exc_info=exc_info)

    @staticmethod
    @_catch_and_log_exceptions
    def mark_skip(item_id: TestId, skip_reason: Optional[str] = None):
        log.debug("Marking test %s as skipped, skip reason: %s", item_id, skip_reason)
        Test.finish(item_id, TestStatus.SKIP, skip_reason=skip_reason)
