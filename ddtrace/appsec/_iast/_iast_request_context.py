import sys
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast import _is_iast_enabled
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._handlers import _on_django_func_wrapped
from ddtrace.appsec._iast._handlers import _on_django_patch
from ddtrace.appsec._iast._handlers import _on_flask_patch
from ddtrace.appsec._iast._handlers import _on_grpc_response
from ddtrace.appsec._iast._handlers import _on_request_init
from ddtrace.appsec._iast._handlers import _on_set_http_meta_iast
from ddtrace.appsec._iast._handlers import _on_wsgi_environ
from ddtrace.appsec._iast._metrics import _set_metric_iast_request_tainted
from ddtrace.appsec._iast._metrics import _set_span_tag_iast_executed_sink
from ddtrace.appsec._iast._metrics import _set_span_tag_iast_request_tainted
from ddtrace.appsec._iast.reporter import IastSpanReporter
from ddtrace.appsec._trace_utils import _asm_manual_keep
from ddtrace.constants import ORIGIN_KEY
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

# Stopgap module for providing ASM context for the blocking features wrapping some contextvars.

if sys.version_info >= (3, 8):
    from typing import Literal  # noqa:F401
else:
    from typing_extensions import Literal  # noqa:F401

_IAST_CONTEXT: Literal["_iast_env"] = "_iast_env"


class IASTEnvironment:
    """
    an object of this class contains all asm data (waf and telemetry)
    for a single request. It is bound to a single asm request context.
    It is contained into a ContextVar.
    """

    def __init__(self, span: Optional[Span] = None):
        self.span = span or core.get_span()

        self.request_enabled: bool = False
        self.iast_reporter: Optional[IastSpanReporter] = None


def _get_iast_context() -> Optional[IASTEnvironment]:
    return core.get_item(_IAST_CONTEXT)


def in_iast_context() -> bool:
    return core.get_item(_IAST_CONTEXT) is not None


def start_iast_context():
    if _is_iast_enabled():
        from ._taint_tracking import create_context as create_propagation_context

        create_propagation_context()
        core.set_item(_IAST_CONTEXT, IASTEnvironment())


def end_iast_context(span: Optional[Span] = None):
    from ._taint_tracking import reset_context as reset_propagation_context

    env = _get_iast_context()
    if env is not None and env.span is span:
        finalize_iast_env(env)
    reset_propagation_context()


def finalize_iast_env(env: IASTEnvironment) -> None:
    core.discard_item(_IAST_CONTEXT)


def set_iast_reporter(iast_reporter: IastSpanReporter) -> None:
    env = _get_iast_context()
    if env:
        env.iast_reporter = iast_reporter
    else:
        log.debug("[IAST] Trying to set IAST reporter but no context is present")


def get_iast_reporter() -> Optional[IastSpanReporter]:
    env = _get_iast_context()
    if env:
        return env.iast_reporter
    return None


def set_iast_request_enabled(request_enabled) -> None:
    env = _get_iast_context()
    if env:
        env.request_enabled = request_enabled
    else:
        log.debug("[IAST] Trying to set IAST reporter but no context is present")


def is_iast_request_enabled():
    env = _get_iast_context()
    if env:
        return env.request_enabled
    return False


def _iast_end_request(ctx=None, span=None, *args, **kwargs):
    try:
        if span:
            req_span = span
        else:
            req_span = ctx.get_item("req_span")

        if _is_iast_enabled():
            exist_data = req_span.get_tag(IAST.JSON)
            if exist_data is None and req_span.get_metric(IAST.ENABLED) is None:
                if not is_iast_request_enabled():
                    req_span.set_metric(IAST.ENABLED, 0.0)
                    end_iast_context(req_span)
                    oce.release_request()
                    return

                req_span.set_metric(IAST.ENABLED, 1.0)
                report_data: Optional[IastSpanReporter] = get_iast_reporter()

                if report_data:
                    report_data.build_and_scrub_value_parts()
                    req_span.set_tag_str(IAST.JSON, report_data._to_str())
                    _asm_manual_keep(req_span)
                _set_metric_iast_request_tainted()
                _set_span_tag_iast_request_tainted(req_span)
                _set_span_tag_iast_executed_sink(req_span)

                set_iast_request_enabled(False)
                end_iast_context(req_span)

                if req_span.get_tag(ORIGIN_KEY) is None:
                    req_span.set_tag_str(ORIGIN_KEY, APPSEC.ORIGIN_VALUE)

                oce.release_request()

    except Exception:
        log.debug("[IAST] Error finishing IAST context", exc_info=True)


def _iast_start_request(span=None, *args, **kwargs):
    try:
        if _is_iast_enabled():
            start_iast_context()
            request_iast_enabled = False
            if oce.acquire_request(span):
                request_iast_enabled = True
            set_iast_request_enabled(request_iast_enabled)
    except Exception:
        log.debug("[IAST] Error starting IAST context", exc_info=True)


def _on_grpc_server_response(message):
    _on_grpc_response(message)


def iast_listen():
    core.on("grpc.client.response.message", _on_grpc_response)
    core.on("grpc.server.response.message", _on_grpc_server_response)

    core.on("set_http_meta_for_asm", _on_set_http_meta_iast)
    core.on("django.patch", _on_django_patch)
    core.on("django.wsgi_environ", _on_wsgi_environ, "wrapped_result")
    core.on("django.func.wrapped", _on_django_func_wrapped)
    core.on("flask.patch", _on_flask_patch)
    core.on("flask.request_init", _on_request_init)

    core.on("context.ended.wsgi.__call__", _iast_end_request)
    core.on("context.ended.asgi.__call__", _iast_end_request)
