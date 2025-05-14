import os
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.appsec._constants import IAST
from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast._iast_env import IASTEnvironment
from ddtrace.appsec._iast._iast_env import _get_iast_env
from ddtrace.appsec._iast._overhead_control_engine import oce
from ddtrace.appsec._iast._taint_tracking._context import create_context as create_propagation_context
from ddtrace.appsec._iast._taint_tracking._context import reset_context as reset_propagation_context
from ddtrace.appsec._iast._utils import _num_objects_tainted_in_request
from ddtrace.appsec._iast.sampling.vulnerability_detection import update_global_vulnerability_limit
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import asbool
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)

# Stopgap module for providing ASM context for the blocking features wrapping some contextvars.


def _set_span_tag_iast_request_tainted(span):
    total_objects_tainted = _num_objects_tainted_in_request()

    if total_objects_tainted > 0:
        span.set_tag(IAST_SPAN_TAGS.TELEMETRY_REQUEST_TAINTED, total_objects_tainted)


def start_iast_context(span=None):
    if asm_config._iast_enabled:
        create_propagation_context()
        core.set_item(IAST.REQUEST_CONTEXT_KEY, IASTEnvironment(span))


def end_iast_context(span: Optional["Span"] = None):
    env = _get_iast_env()
    if env is not None and env.span is span:
        update_global_vulnerability_limit(env)
        finalize_iast_env(env)
    reset_propagation_context()


def finalize_iast_env(env: IASTEnvironment) -> None:
    core.discard_item(IAST.REQUEST_CONTEXT_KEY)


def get_iast_stacktrace_reported() -> bool:
    env = _get_iast_env()
    if env:
        return env.iast_stack_trace_reported
    return False


def set_iast_stacktrace_reported(reported: bool) -> None:
    env = _get_iast_env()
    if env:
        env.iast_stack_trace_reported = reported


def get_iast_stacktrace_id() -> int:
    env = _get_iast_env()
    if env:
        env.iast_stack_trace_id += 1
        return env.iast_stack_trace_id
    return 0


def set_iast_request_enabled(request_enabled) -> None:
    env = _get_iast_env()
    if env:
        env.request_enabled = request_enabled
    else:
        log.debug("iast::propagation::context::Trying to set IAST reporter but no context is present")


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


def _move_iast_data_to_root_span():
    return asbool(os.getenv("_DD_IAST_USE_ROOT_SPAN"))


def _iast_start_request(span=None, *args, **kwargs):
    try:
        if asm_config._iast_enabled:
            start_iast_context(span)
            request_iast_enabled = False
            if oce.acquire_request(span):
                request_iast_enabled = True
            set_iast_request_enabled(request_iast_enabled)
    except Exception:
        log.debug("iast::propagation::context::Error starting IAST context", exc_info=True)
