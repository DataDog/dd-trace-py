from ddtrace.ext.ci_visibility.api import CIModule
from ddtrace.ext.ci_visibility.api import CIModuleId
from ddtrace.ext.ci_visibility.api import CISession
from ddtrace.ext.ci_visibility.api import CISessionId
from ddtrace.ext.ci_visibility.api import CISuite
from ddtrace.ext.ci_visibility.api import CISuiteId
from ddtrace.ext.ci_visibility.api import CITest
from ddtrace.ext.ci_visibility.api import CITestId
from ddtrace.internal import core
from ddtrace.internal.ci_visibility import CIVisibility
from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api.ci_module import CIVisibilityModule
from ddtrace.internal.ci_visibility.api.ci_session import CIVisibilitySession
from ddtrace.internal.ci_visibility.api.ci_suite import CIVisibilitySuite
from ddtrace.internal.ci_visibility.api.ci_test import CIVisibilityTest
from ddtrace.internal.ci_visibility.errors import CIVisibilityDataError
from ddtrace.internal.ci_visibility.errors import CIVisibilityError
from ddtrace.internal.ci_visibility.recorder import log
from ddtrace.internal.core.event_hub import ResultType


def _requires_civisibility_enabled(func):
    def wrapper(*args, **kwargs):
        if not CIVisibility.enabled:
            log.warning("CI Visibility is not enabled")
            raise CIVisibilityError("CI Visibility is not enabled")
        return func(*args, **kwargs)

    return wrapper


@_requires_civisibility_enabled
def _on_discover_session(discover_args: CISession.DiscoverArgs):
    log.error("Handling session discovery")

    # _requires_civisibility_enabled prevents us from getting here, but this makes type checkers happy
    tracer = CIVisibility.get_tracer()
    test_service = CIVisibility.get_service()

    if tracer is None or test_service is None:
        error_msg = "Tracer or test service is None"
        log.warning(error_msg)
        raise CIVisibilityError(error_msg)

    session_settings = CIVisibilitySessionSettings(
        tracer=tracer,
        test_service=test_service,
        test_command=discover_args.test_command,
        reject_unknown_items=discover_args.reject_unknown_items,
        reject_duplicates=discover_args.reject_duplicates,
        test_framework=discover_args.test_framework,
        test_framework_version=discover_args.test_framework_version,
        session_operation_name=discover_args.session_operation_name,
        module_operation_name=discover_args.module_operation_name,
        suite_operation_name=discover_args.suite_operation_name,
        test_operation_name=discover_args.test_operation_name,
    )

    session = CIVisibilitySession(
        discover_args.session_id,
        session_settings,
    )
    CIVisibility.add_session(session)


@_requires_civisibility_enabled
def _on_start_session(session_id: CISessionId):
    log.warning("Handling start for session id %s", session_id)
    session = CIVisibility.get_session_by_id(session_id)
    session.start()


@_requires_civisibility_enabled
def _on_finish_session(finish_args: CISession.FinishArgs):
    log.warning("Handling finish for session id %s", finish_args)
    session = CIVisibility.get_session_by_id(finish_args.session_id)
    session.finish(finish_args.force_finish_children, finish_args.override_status)


def _register_session_handlers():
    log.debug("Registering session handlers")
    core.on("ci_visibility.session.register", _on_discover_session)
    core.on("ci_visibility.session.start", _on_start_session)
    core.on("ci_visibility.session.finish", _on_finish_session)


@_requires_civisibility_enabled
def _on_discover_module(discover_args: CIModule.DiscoverArgs):
    log.warning("Handling discovery for module %s", discover_args.module_id)
    session = CIVisibility.get_session_by_id(discover_args.module_id.get_session_id())

    session.add_child(
        CIVisibilityModule(
            discover_args.module_id,
            CIVisibility.get_session_settings(discover_args.module_id),
        )
    )


@_requires_civisibility_enabled
def _on_start_module(module_id: CIModuleId):
    log.warning("Handling start for module id %s", module_id)
    CIVisibility.get_module_by_id(module_id).start()


@_requires_civisibility_enabled
def _on_finish_module(finish_args: CIModule.FinishArgs):
    log.warning("Handling finish for module id %s", finish_args.module_id)
    CIVisibility.get_module_by_id(finish_args.module_id).finish()


def _register_module_handlers():
    log.debug("Registering module handlers")
    core.on("ci_visibility.module.discover", _on_discover_module)
    core.on("ci_visibility.module.start", _on_start_module)
    core.on("ci_visibility.module.finish", _on_finish_module)


@_requires_civisibility_enabled
def _on_discover_suite(discover_args: CISuite.DiscoverArgs):
    log.warning("Handling discovery for suite args %s", discover_args)
    module = CIVisibility.get_module_by_id(discover_args.suite_id.parent_id)
    if discover_args.suite_id in module.children:
        log.warning("Suite with id %s already exists", discover_args.suite_id)
        return

    module.add_child(
        CIVisibilitySuite(
            discover_args.suite_id,
            CIVisibility.get_session_settings(discover_args.suite_id),
            discover_args.codeowners,
            discover_args.source_file_info,
        )
    )


@_requires_civisibility_enabled
def _on_start_suite(suite_id: CISuiteId):
    log.warning("Handling start for suite id %s", suite_id)
    CIVisibility.get_suite_by_id(suite_id).start()


@_requires_civisibility_enabled
def _on_finish_suite(finish_args: CISuite.FinishArgs):
    log.warning("Handling finish for suite id %s", finish_args.suite_id)
    CIVisibility.get_suite_by_id(finish_args.suite_id).finish(
        finish_args.force_finish_children, finish_args.override_status, finish_args.is_itr_skipped
    )


def _register_suite_handlers():
    log.debug("Registering suite handlers")
    core.on("ci_visibility.suite.discover", _on_discover_suite)
    core.on("ci_visibility.suite.start", _on_start_suite)
    core.on("ci_visibility.suite.finish", _on_finish_suite)


@_requires_civisibility_enabled
def _on_discover_test(discover_args: CITest.DiscoverArgs):
    log.warning("Handling discovery for test %s", discover_args.test_id)
    suite = CIVisibility.get_suite_by_id(discover_args.test_id.parent_id)
    # breakpoint()
    if discover_args.test_id in suite.children:
        log.warning("Test with id %s already exists", discover_args.test_id)

    suite.add_child(
        CIVisibilityTest(
            discover_args.test_id,
            CIVisibility.get_session_settings(discover_args.test_id),
            discover_args.codeowners,
            discover_args.source_file_info,
        )
    )


@_requires_civisibility_enabled
def _on_discover_test_early_flake_retry(args: CITest.DiscoverEarlyFlakeRetryArgs):
    log.warning("Handling early flake discovery for test %s", args.test_id)
    suite = CIVisibility.get_suite_by_id(args.test_id.parent_id)
    try:
        original_test = suite.get_child_by_id(args.test_id)
    except CIVisibilityDataError:
        log.warning("Cannot find original test %s to register retry number %s", args.test_id, args.retry_number)
        raise

    suite.add_child(CIVisibilityTest.make_early_flake_retry_from_test(original_test, args.retry_number))


@_requires_civisibility_enabled
def _on_start_test(test_id: CITestId):
    log.warning("Handling start for test id %s", test_id)
    CIVisibility.get_test_by_id(test_id).start()


@_requires_civisibility_enabled
def _on_finish_test(finish_args: CITest.FinishArgs):
    log.warning("Handling finish for test id %s, with status %s", finish_args.test_id, finish_args.status)
    CIVisibility.get_test_by_id(finish_args.test_id).finish_test(
        finish_args.status, finish_args.skip_reason, finish_args.exc_info, finish_args.is_itr_skipped
    )


def _register_test_handlers():
    log.debug("Registering test handlers")
    core.on("ci_visibility.test.discover", _on_discover_test)
    core.on("ci_visibility.test.discover_early_flake_retry", _on_discover_test_early_flake_retry)
    core.on("ci_visibility.test.start", _on_start_test)
    core.on("ci_visibility.test.finish", _on_finish_test)


def _on_civisibility_enable():
    CIVisibility.enable()
    if CIVisibility.enabled:
        return ResultType.RESULT_OK
    else:
        return ResultType.RESULT_EXCEPTION


def _on_civisibility_disable():
    if CIVisibility.enabled:
        CIVisibility.disable()
        if CIVisibility.enabled:
            log.warning("CI Visibility is still enabled")
            return ResultType.RESULT_EXCEPTION
        return ResultType.RESULT_OK
    log.warning("CI Visibility is already disabled")
    return ResultType.RESULT_OK


def _register_civisibility_handlers():
    _register_session_handlers()
    _register_module_handlers()
    _register_suite_handlers()
    _register_test_handlers()
    core.on("ci_visibility.enable", _on_civisibility_enable, "ci_visibility.enable")
    core.on("ci_visibility.disable", _on_civisibility_disable, "ci_visibility.disable")
