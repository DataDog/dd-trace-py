"""IAST request lifecycle and environment helpers.

is_iast_request_enabled is deliberately re-exported here: the taint sinks and handlers import it from
this module. Everything else takes the context-local primitives from their owners directly
(ddtrace.appsec._iast_context, ddtrace.appsec._iast._taint_tracking._context) — do not add new
re-exports, they are what tied this module to its low-level consumers in the first place.
"""

from typing import Optional

from ddtrace.appsec._iast._constants import IAST
from ddtrace.appsec._iast._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast._iast_env import IASTEnvironment
from ddtrace.appsec._iast._iast_env import _get_iast_env
from ddtrace.appsec._iast._overhead_control_engine import oce
from ddtrace.appsec._iast._taint_tracking._context import _num_objects_tainted_in_request
from ddtrace.appsec._iast._taint_tracking._context import finish_request_context
from ddtrace.appsec._iast._taint_tracking._context import get_tainted_object_map_by_ctx_id
from ddtrace.appsec._iast._taint_tracking._context import start_request_context
from ddtrace.appsec._iast.sampling.vulnerability_detection import update_global_vulnerability_limit
from ddtrace.appsec._iast_context import IAST_CONTEXT
from ddtrace.appsec._iast_context import _get_iast_context_id
from ddtrace.appsec._iast_context import is_iast_request_enabled as is_iast_request_enabled
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import SpanData
from ddtrace.internal.settings.asm import config as asm_config


log = get_logger(__name__)


def _set_span_tag_iast_request_tainted(span: SpanData) -> None:
    total_objects_tainted = _num_objects_tainted_in_request()

    if total_objects_tainted > 0:
        span._set_attribute(IAST_SPAN_TAGS.TELEMETRY_REQUEST_TAINTED, total_objects_tainted)


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


def _discard_expired_iast_env(context_id: int) -> None:
    if (env := _get_iast_env()) is not None and env.context_id == context_id:
        env.iast_taint_source_objects.clear()
        core.discard_item(IAST.REQUEST_CONTEXT_KEY)


def _iast_start_request(span: Optional[SpanData] = None) -> Optional[int]:
    """Initialize the IAST request context for the current execution.

    When admission succeeds, an inherited expired identifier is replaced with a
    fresh native context. A live context stays in place without returning a new
    identifier in that case.

    The provided span, when present, is attached to the IAST environment for later
    enrichment and end-of-request processing.
    """
    context_id = None

    if asm_config._iast_enabled:
        if oce.acquire_request(span):
            active_id = _get_iast_context_id()
            # A copied execution context can outlive the request that allocated
            # its native map. It must not suppress admission of a new request.
            if active_id is None or not get_tainted_object_map_by_ctx_id(active_id):
                if active_id is not None:
                    _discard_expired_iast_env(active_id)
                context_id = start_request_context()
                IAST_CONTEXT.set(context_id)
                if context_id is not None:
                    core.set_item(IAST.REQUEST_CONTEXT_KEY, IASTEnvironment(span, context_id))
        elif (context_id := _get_iast_context_id()) is not None:
            expired = not get_tainted_object_map_by_ctx_id(context_id)
            finish_request_context(context_id)
            IAST_CONTEXT.set(None)
            if expired:
                _discard_expired_iast_env(context_id)
            elif env := _get_iast_env():
                env.iast_taint_source_objects.clear()
    return context_id


def _iast_finish_request(span: Optional[SpanData] = None, shoud_update_global_vulnerability_limit: bool = True) -> bool:
    """Finalize the IAST request context and optionally update global limits.

    This function only clears an environment owned by the active context, optionally
    updates global vulnerability optimization data, and releases the native taint context.
    """
    context_id = _get_iast_context_id()
    env = _get_iast_env()
    # Rejected admission can leave another request's environment visible through core.
    if env is not None and (context_id is None or env.context_id != context_id):
        env = None
    if env is not None and env.span is span:
        if shoud_update_global_vulnerability_limit:
            update_global_vulnerability_limit(env)
        core.discard_item(IAST.REQUEST_CONTEXT_KEY)

    if context_id is not None:
        finish_request_context(context_id)
        IAST_CONTEXT.set(None)

    if env is not None:
        env.iast_taint_source_objects.clear()
    return context_id is not None


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
