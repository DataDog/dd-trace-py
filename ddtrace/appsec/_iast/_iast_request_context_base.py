import contextvars
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.appsec._constants import IAST
from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast._iast_env import IASTEnvironment
from ddtrace.appsec._iast._iast_env import _get_iast_env
from ddtrace.appsec._iast._overhead_control_engine import oce
from ddtrace.appsec._iast._taint_tracking._context import debug_debug_num_tainted_objects
from ddtrace.appsec._iast._taint_tracking._context import finish_request_context
from ddtrace.appsec._iast._taint_tracking._context import start_request_context
from ddtrace.appsec._iast.sampling.vulnerability_detection import update_global_vulnerability_limit
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)

# Stopgap module for providing ASM context for the blocking features wrapping some contextvars.

IAST_CONTEXT: "contextvars.ContextVar[Optional[int]]" = contextvars.ContextVar("iast_var", default=None)


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


class IASTContextException(Exception):
    pass


def _iast_start_request(span=None):
    context_id = None

    if asm_config._iast_enabled:
        if oce.acquire_request(span):
            _old_context_id = IAST_CONTEXT.get()
            if _old_context_id:
                raise IASTContextException("Trying to start a new context but an IAST context already present")
            context_id = start_request_context()
            if context_id is not None:
                IAST_CONTEXT.set(context_id)
                core.set_item(IAST.REQUEST_CONTEXT_KEY, IASTEnvironment(span))

    return context_id


def _get_iast_context_id():
    return IAST_CONTEXT.get()


def _iast_finish_request(span: Optional["Span"] = None, shoud_update_global_vulnerability_limit: bool = True):
    env = _get_iast_env()
    if env is not None and env.span is span:
        if shoud_update_global_vulnerability_limit:
            update_global_vulnerability_limit(env)
        core.discard_item(IAST.REQUEST_CONTEXT_KEY)

    context_id = _get_iast_context_id()
    if context_id is not None:
        finish_request_context(context_id)
        IAST_CONTEXT.set(None)


def is_iast_request_enabled():
    print(f"IAST REQUEST CONTEXT! {_get_iast_context_id()}")
    return _get_iast_context_id() is not None


def _num_objects_tainted_in_request():
    return debug_debug_num_tainted_objects(_get_iast_context_id())
