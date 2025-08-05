"""
Provides the API necessary to interacting with the Test Visibility service.

NOTE: BETA - this API is currently in development and is subject to change.

This API supports the agentless, and agent-proxied (EVP) modes. It does not support the APM protocol.

All functions in this module are meant to be called in a stateless manner. Test runners (or custom implementations) that
rely on this API are not expected to keep Test Visibility-related state for each session, module, suite or test.

Stable values of module, suite, test names, and parameters, are a necessity for this API to function properly.

All types and methods for interacting with the API are provided and documented in this file.
"""
from enum import Enum
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace._trace.context import Context
from ddtrace.ext.test_visibility._test_visibility_base import TestId
from ddtrace.ext.test_visibility._test_visibility_base import TestModuleId
from ddtrace.ext.test_visibility._test_visibility_base import TestSessionId
from ddtrace.ext.test_visibility._test_visibility_base import TestSuiteId
from ddtrace.ext.test_visibility._test_visibility_base import TestVisibilityItemId
from ddtrace.ext.test_visibility._test_visibility_base import _TestVisibilityAPIBase
from ddtrace.ext.test_visibility._utils import _catch_and_log_exceptions
from ddtrace.ext.test_visibility.status import TestExcInfo
from ddtrace.ext.test_visibility.status import TestSourceFileInfo
from ddtrace.ext.test_visibility.status import TestStatus
from ddtrace.internal.ci_visibility.service_registry import require_ci_visibility_service
from ddtrace.internal.logger import get_logger as _get_logger


def _get_item_tag(item_id: TestVisibilityItemId, tag_name: str) -> Any:
    return require_ci_visibility_service().get_item_by_id(item_id).get_tag(tag_name)


def _set_item_tag(item_id: TestVisibilityItemId, tag_name: str, tag_value: Any) -> None:
    require_ci_visibility_service().get_item_by_id(item_id).set_tag(tag_name, tag_value)


def _set_item_tags(item_id: TestVisibilityItemId, tags: Dict[str, Any]) -> None:
    require_ci_visibility_service().get_item_by_id(item_id).set_tags(tags)


def _delete_item_tag(item_id: TestVisibilityItemId, tag_name: str) -> None:
    require_ci_visibility_service().get_item_by_id(item_id).delete_tag(tag_name)


def _delete_item_tags(item_id: TestVisibilityItemId, tag_names: List[str]) -> None:
    require_ci_visibility_service().get_item_by_id(item_id).delete_tags(tag_names)


def _is_item_finished(item_id: TestVisibilityItemId) -> bool:
    return require_ci_visibility_service().get_item_by_id(item_id).is_finished()


log = _get_logger(__name__)

# this triggers the registration of trace handlers after civis startup
import ddtrace._trace.trace_handlers  # noqa: F401, E402


DEFAULT_SESSION_NAME = "test_visibility_session"


class DEFAULT_OPERATION_NAMES(Enum):
    SESSION = "test_visibility.session"
    MODULE = "test_visibility.module"
    SUITE = "test_visibility.suite"
    TEST = "test_visibility.test"


@_catch_and_log_exceptions
def enable_test_visibility(config: Optional[Any] = None):
    log.debug("Enabling Test Visibility with config: %s", config)
    from ddtrace.internal.ci_visibility.recorder import CIVisibility

    CIVisibility.enable(config=config)

    if not is_test_visibility_enabled():
        log.warning("Failed to enable Test Visibility")


@_catch_and_log_exceptions
def is_test_visibility_enabled():
    try:
        return require_ci_visibility_service().enabled
    except RuntimeError:
        return False


@_catch_and_log_exceptions
def disable_test_visibility():
    log.debug("Disabling Test Visibility")
    ci_visibility_instance = require_ci_visibility_service()
    ci_visibility_instance.disable()
    if ci_visibility_instance.enabled:
        log.warning("Failed to disable Test Visibility")


class TestBase(_TestVisibilityAPIBase):
    @staticmethod
    def get_tag(item_id: TestVisibilityItemId, tag_name: str) -> Any:
        return _get_item_tag(item_id, tag_name)

    @staticmethod
    def set_tag(item_id: TestVisibilityItemId, tag_name: str, tag_value: Any):
        _set_item_tag(item_id, tag_name, tag_value)

    @staticmethod
    def set_tags(item_id: TestVisibilityItemId, tags: Dict[str, Any]):
        _set_item_tags(item_id, tags)

    @staticmethod
    def delete_tag(item_id: TestVisibilityItemId, tag_name: str):
        _delete_item_tag(item_id, tag_name)

    @staticmethod
    def delete_tags(item_id: TestVisibilityItemId, tag_names: List[str]):
        _delete_item_tags(item_id, tag_names)

    @staticmethod
    def is_finished(item_id: TestVisibilityItemId) -> bool:
        return _is_item_finished(item_id)


class TestSession(_TestVisibilityAPIBase):
    @staticmethod
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

        from ddtrace.internal.ci_visibility.recorder import on_discover_session

        on_discover_session(
            test_command=test_command,
            reject_duplicates=reject_duplicates,
            test_framework=test_framework,
            test_framework_version=test_framework_version,
            session_operation_name=session_operation_name,
            module_operation_name=module_operation_name,
            suite_operation_name=suite_operation_name,
            test_operation_name=test_operation_name,
            root_dir=root_dir,
        )

    @staticmethod
    @_catch_and_log_exceptions
    def start(distributed_children: bool = False, context: Optional[Context] = None):
        log.debug("Starting session")
        session = require_ci_visibility_service().get_session()
        session.start(context)
        if distributed_children:
            session.set_distributed_children()

    @staticmethod
    @_catch_and_log_exceptions
    def finish(
        force_finish_children: bool = False,
        override_status: Optional[TestStatus] = None,
    ):
        log.debug("Finishing session, force_finish_session_modules: %s", force_finish_children)

        session = require_ci_visibility_service().get_session()
        session.finish(force=force_finish_children, override_status=override_status)

    @staticmethod
    def get_tag(tag_name: str) -> Any:
        return _get_item_tag(TestSessionId(), tag_name)

    @staticmethod
    def set_tag(tag_name: str, tag_value: Any):
        _set_item_tag(TestSessionId(), tag_name, tag_value)

    @staticmethod
    def set_tags(tags: Dict[str, Any]):
        _set_item_tags(TestSessionId(), tags)

    @staticmethod
    def delete_tag(tag_name: str):
        _delete_item_tag(TestSessionId(), tag_name)

    @staticmethod
    def delete_tags(tag_names: List[str]):
        _delete_item_tags(TestSessionId(), tag_names)


class TestModule(TestBase):
    @staticmethod
    @_catch_and_log_exceptions
    def discover(item_id: TestModuleId, module_path: Optional[Path] = None):
        from ddtrace.internal.ci_visibility.api._module import TestVisibilityModule

        log.debug("Registered module %s", item_id)
        ci_visibility_instance = require_ci_visibility_service()
        session = ci_visibility_instance.get_session()

        session.add_child(
            item_id,
            TestVisibilityModule(
                item_id.name,
                ci_visibility_instance.get_session_settings(),
                module_path,
            ),
        )

    @staticmethod
    @_catch_and_log_exceptions
    def start(item_id: TestModuleId, *args, **kwargs):
        log.debug("Starting module %s", item_id)
        require_ci_visibility_service().get_module_by_id(item_id).start()

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

        require_ci_visibility_service().get_module_by_id(item_id).finish(
            force=force_finish_children, override_status=override_status
        )


class TestSuite(TestBase):
    @staticmethod
    @_catch_and_log_exceptions
    def discover(
        item_id: TestSuiteId,
        codeowners: Optional[List[str]] = None,
        source_file_info: Optional[TestSourceFileInfo] = None,
    ):
        """Registers a test suite with the Test Visibility service."""
        log.debug("Registering suite %s, source: %s", item_id, source_file_info)
        from ddtrace.internal.ci_visibility.api._suite import TestVisibilitySuite

        ci_visibility_instance = require_ci_visibility_service()
        module = ci_visibility_instance.get_module_by_id(item_id.parent_id)

        module.add_child(
            item_id,
            TestVisibilitySuite(
                item_id.name,
                ci_visibility_instance.get_session_settings(),
                codeowners,
                source_file_info,
            ),
        )

    @staticmethod
    @_catch_and_log_exceptions
    def start(item_id: TestSuiteId):
        log.debug("Starting suite %s", item_id)
        require_ci_visibility_service().get_suite_by_id(item_id).start()

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

        require_ci_visibility_service().get_suite_by_id(item_id).finish(
            force=force_finish_children, override_status=override_status
        )


class Test(TestBase):
    @staticmethod
    @_catch_and_log_exceptions
    def discover(
        item_id: TestId,
        codeowners: Optional[List[str]] = None,
        source_file_info: Optional[TestSourceFileInfo] = None,
        resource: Optional[str] = None,
    ):
        """Registers a test with the Test Visibility service."""
        from ddtrace.internal.ci_visibility._api_client import TestProperties
        from ddtrace.internal.ci_visibility.api._test import TestVisibilityTest

        log.debug(
            "Discovering test %s, codeowners: %s, source file: %s, resource: %s",
            item_id,
            codeowners,
            source_file_info,
            resource,
        )

        log.debug("Handling discovery for test %s", item_id)
        ci_visibility_instance = require_ci_visibility_service()
        suite = ci_visibility_instance.get_suite_by_id(item_id.parent_id)

        # New tests are currently only considered for EFD:
        # - if known tests were fetched properly (enforced by is_known_test)
        # - if they have no parameters
        if ci_visibility_instance.is_known_tests_enabled() and item_id.parameters is None:
            is_new = not ci_visibility_instance.is_known_test(item_id)
        else:
            is_new = False

        test_properties = None
        if ci_visibility_instance.is_test_management_enabled():
            test_properties = ci_visibility_instance.get_test_properties(item_id)

        if not test_properties:
            test_properties = TestProperties()

        suite.add_child(
            item_id,
            TestVisibilityTest(
                item_id.name,
                ci_visibility_instance.get_session_settings(),
                parameters=item_id.parameters,
                codeowners=codeowners,
                source_file_info=source_file_info,
                resource=resource,
                is_new=is_new,
                is_quarantined=test_properties.quarantined,
                is_disabled=test_properties.disabled,
                is_attempt_to_fix=test_properties.attempt_to_fix,
            ),
        )

    @staticmethod
    @_catch_and_log_exceptions
    def start(item_id: TestId):
        log.debug("Starting test %s", item_id)

        require_ci_visibility_service().get_test_by_id(item_id).start()

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

        require_ci_visibility_service().get_test_by_id(item_id).finish_test(
            status=status, skip_reason=skip_reason, exc_info=exc_info
        )

    @staticmethod
    @_catch_and_log_exceptions
    def set_parameters(item_id: TestId, params: str):
        log.debug("Setting test %s parameters to %s", item_id, params)

        require_ci_visibility_service().get_test_by_id(item_id).set_parameters(parameters=params)

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
