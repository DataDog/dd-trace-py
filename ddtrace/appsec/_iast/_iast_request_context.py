import os
import sys
from typing import Dict
from typing import Optional

from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._metrics import _set_metric_iast_request_tainted
from ddtrace.appsec._iast._metrics import _set_span_tag_iast_executed_sink
from ddtrace.appsec._iast._metrics import _set_span_tag_iast_request_tainted
from ddtrace.appsec._iast._taint_tracking._context import create_context as create_propagation_context
from ddtrace.appsec._iast._taint_tracking._context import reset_context as reset_propagation_context
from ddtrace.appsec._iast.reporter import IastSpanReporter
from ddtrace.constants import _ORIGIN_KEY
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import asbool
from ddtrace.settings.asm import config as asm_config
from ddtrace.trace import Span


log = get_logger(__name__)

# Stopgap module for providing ASM context for the blocking features wrapping some contextvars.

if sys.version_info >= (3, 8):
    from typing import Literal  # noqa:F401
else:
    from typing_extensions import Literal  # noqa:F401


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
        self.iast_span_metrics: Dict[str, int] = {}
        self.iast_stack_trace_id: int = 0
        self.iast_stack_trace_reported: bool = False


def _get_iast_context() -> Optional[IASTEnvironment]:
    return core.get_item(IAST.REQUEST_CONTEXT_KEY)


def in_iast_context() -> bool:
    return core.get_item(IAST.REQUEST_CONTEXT_KEY) is not None


def start_iast_context():
    if asm_config._iast_enabled:
        create_propagation_context()
        core.set_item(IAST.REQUEST_CONTEXT_KEY, IASTEnvironment())


def end_iast_context(span: Optional[Span] = None):
    env = _get_iast_context()
    if env is not None and env.span is span:
        finalize_iast_env(env)
    reset_propagation_context()


def finalize_iast_env(env: IASTEnvironment) -> None:
    core.discard_item(IAST.REQUEST_CONTEXT_KEY)


def set_iast_reporter(iast_reporter: IastSpanReporter) -> None:
    env = _get_iast_context()
    if env:
        env.iast_reporter = iast_reporter
    else:
        log.debug("iast::propagation::context::Trying to set IAST reporter but no context is present")


def get_iast_reporter() -> Optional[IastSpanReporter]:
    env = _get_iast_context()
    if env:
        return env.iast_reporter
    return None


def get_iast_stacktrace_reported() -> bool:
    env = _get_iast_context()
    if env:
        return env.iast_stack_trace_reported
    return False


def set_iast_stacktrace_reported(reported: bool) -> None:
    env = _get_iast_context()
    if env:
        env.iast_stack_trace_reported = reported


def get_iast_stacktrace_id() -> int:
    env = _get_iast_context()
    if env:
        env.iast_stack_trace_id += 1
        return env.iast_stack_trace_id
    return 0


def set_iast_request_enabled(request_enabled) -> None:
    env = _get_iast_context()
    if env:
        env.request_enabled = request_enabled
    else:
        log.debug("iast::propagation::context::Trying to set IAST reporter but no context is present")


def _move_iast_data_to_root_span():
    return asbool(os.getenv("_DD_IAST_USE_ROOT_SPAN"))


def _create_and_attach_iast_report_to_span(req_span: Span, existing_data: Optional[str], merge: bool = False):
    report_data: Optional[IastSpanReporter] = get_iast_reporter()
    if merge and existing_data is not None and report_data is not None:
        previous_data = IastSpanReporter()
        previous_data._from_json(existing_data)

        report_data._merge(previous_data)

    if report_data is not None:
        report_data.build_and_scrub_value_parts()
        req_span.set_tag_str(IAST.JSON, report_data._to_str())
    _set_metric_iast_request_tainted()
    _set_span_tag_iast_request_tainted(req_span)
    _set_span_tag_iast_executed_sink(req_span)

    set_iast_request_enabled(False)
    end_iast_context(req_span)

    if req_span.get_tag(_ORIGIN_KEY) is None:
        req_span.set_tag_str(_ORIGIN_KEY, APPSEC.ORIGIN_VALUE)

    oce.release_request()


def _iast_end_request(ctx=None, span=None, *args, **kwargs):
    try:
        move_to_root = _move_iast_data_to_root_span()
        if move_to_root:
            req_span = core.get_root_span()
        else:
            if span:
                req_span = span
            else:
                req_span = ctx.get_item("req_span")
        if req_span is None:
            log.debug("iast::propagation::context::Error finishing IAST context. There isn't a SPAN")
            return
        if asm_config._iast_enabled:
            existing_data = req_span.get_tag(IAST.JSON)
            if existing_data is None:
                if req_span.get_metric(IAST.ENABLED) is None:
                    if not asm_config.is_iast_request_enabled:
                        req_span.set_metric(IAST.ENABLED, 0.0)
                        end_iast_context(req_span)
                        oce.release_request()
                        return

                    req_span.set_metric(IAST.ENABLED, 1.0)
                    _create_and_attach_iast_report_to_span(req_span, existing_data, merge=False)

            elif move_to_root:
                # Data exists from a previous request, we will merge both reports
                _create_and_attach_iast_report_to_span(req_span, existing_data, merge=True)

    except Exception:
        log.debug("iast::propagation::context::Error finishing IAST context", exc_info=True)


def _iast_start_request(span=None, *args, **kwargs):
    try:
        if asm_config._iast_enabled:
            start_iast_context()
            request_iast_enabled = False
            if oce.acquire_request(span):
                request_iast_enabled = True
            set_iast_request_enabled(request_iast_enabled)
    except Exception:
        log.debug("iast::propagation::context::Error starting IAST context", exc_info=True)
