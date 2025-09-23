import contextvars
from typing import Optional

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast._iast_env import IASTEnvironment
from ddtrace.appsec._iast._iast_env import _get_iast_env
from ddtrace.appsec._iast._overhead_control_engine import oce
from ddtrace.appsec._iast._taint_tracking._context import debug_num_tainted_objects
from ddtrace.appsec._iast._taint_tracking._context import finish_request_context
from ddtrace.appsec._iast._taint_tracking._context import start_request_context
from ddtrace.appsec._iast.sampling.vulnerability_detection import update_global_vulnerability_limit
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)

# Stopgap module for providing ASM context for the blocking features wrapping some contextvars.

IAST_CONTEXT: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar("iast_var", default=None)


def _set_span_tag_iast_request_tainted(span):
    total_objects_tainted = _num_objects_tainted_in_request()

    if total_objects_tainted > 0:
        span.set_tag(IAST_SPAN_TAGS.TELEMETRY_REQUEST_TAINTED, total_objects_tainted)


def get_iast_stacktrace_reported() -> bool:
    env = _get_iast_env()
    if env:
        return env.iast_stack_trace_reported
    return False


def set_iast_stacktrace_reported(reported: bool) -> None:
    env = _get_iast_env()
    if env:
        env.iast_stack_trace_reported = reported


def set_iast_request_endpoint(method, route) -> None:
    if asm_config._iast_enabled:
        env = _get_iast_env()
        if env:
            if method:
                env.endpoint_method = method
            if route:
                env.endpoint_route = route
        else:
            log.debug("iast::propagation::context::Trying to set IAST request endpoint but no context is present")


def _iast_start_request(span=None) -> Optional[int]:
    """Initialize the IAST request context for the current execution.

    This function acquires the IAST request budget via the Overhead Control Engine,
    creates a new native taint context, and stores its identifier in a ContextVar so
    subsequent IAST operations can locate the request-local taint map. If a context
    is already active, the existing identifier is reused.

    The provided span, when present, is attached to the IAST environment for later
    enrichment and end-of-request processing.
    """
    context_id = None

    if asm_config._iast_enabled:
        if oce.acquire_request(span):
            if not is_iast_request_enabled():
                context_id = start_request_context()
                IAST_CONTEXT.set(context_id)
                if context_id is not None:
                    core.set_item(IAST.REQUEST_CONTEXT_KEY, IASTEnvironment(span))
        elif (context_id := _get_iast_context_id()) is not None:
            finish_request_context(context_id)
            IAST_CONTEXT.set(None)
    return context_id


def _get_iast_context_id() -> Optional[int]:
    """Retrieve the current IAST context identifier from the ContextVar."""
    return IAST_CONTEXT.get()


def _iast_finish_request(span=None, shoud_update_global_vulnerability_limit: bool = True) -> bool:
    """Finalize the IAST request context and optionally update global limits.

    This function discards the per-request IAST environment, optionally updates the
    global vulnerability optimization data, and releases the native taint context
    associated with the active request.
    """
    env = _get_iast_env()
    if env is not None and env.span is span:
        if shoud_update_global_vulnerability_limit:
            update_global_vulnerability_limit(env)
        core.discard_item(IAST.REQUEST_CONTEXT_KEY)

    context_id = _get_iast_context_id()
    if context_id is not None:
        finish_request_context(context_id)
        IAST_CONTEXT.set(None)
        return True

    return False


def is_iast_request_enabled() -> bool:
    """Check whether IAST is currently operating within an active request context."""
    return _get_iast_context_id() is not None


def _num_objects_tainted_in_request() -> int:
    """Get the count of tainted objects tracked in the active IAST request context.

    Useful for span metrics and internal telemetry.
    """
    context_id = _get_iast_context_id()
    if context_id is not None:
        return debug_num_tainted_objects(context_id)
    return 0


def get_hash_object_tracking_len():
    env = _get_iast_env()
    if env:
        return len(env.iast_hash_object_tracking)
    return 0


def get_hash_object_tracking(obj):
    env = _get_iast_env()
    if env:
        return env.iast_hash_object_tracking.get(id(obj), False)
    return False


def set_hash_object_tracking(result, value):
    env = _get_iast_env()
    if env:
        env.iast_hash_object_tracking[id(result)] = value


def clear_hash_object_tracking():
    env = _get_iast_env()
    if env:
        env.iast_hash_object_tracking.clear()
