from typing import TYPE_CHECKING  # noqa:F401
from typing import Literal  # noqa:F401
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._iast_env import _get_iast_env
import ddtrace.appsec._iast._iast_request_context_base as base
from ddtrace.appsec._iast._metrics import _set_metric_iast_request_tainted
from ddtrace.appsec._iast._overhead_control_engine import oce
from ddtrace.appsec._iast._span_metrics import _set_span_tag_iast_executed_sink
from ddtrace.appsec._iast.reporter import IastSpanReporter
from ddtrace.appsec._iast.sampling.vulnerability_detection import reset_request_vulnerabilities
from ddtrace.constants import _ORIGIN_KEY
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)

# Stopgap module for providing ASM context for the blocking features wrapping some contextvars.


def set_iast_reporter(iast_reporter: IastSpanReporter) -> None:
    env = _get_iast_env()
    if env:
        env.iast_reporter = iast_reporter
    else:
        log.debug("iast::propagation::context::Trying to set IAST reporter but no context is present")


def get_iast_reporter() -> Optional[IastSpanReporter]:
    env = _get_iast_env()
    if env:
        return env.iast_reporter
    return None


def _create_and_attach_iast_report_to_span(req_span: "Span", existing_data: Optional[str], merge: bool = False):
    report_data: Optional[IastSpanReporter] = get_iast_reporter()
    if merge and existing_data is not None and report_data is not None:
        previous_data = IastSpanReporter()
        previous_data._from_json(existing_data)

        report_data._merge(previous_data)

    if report_data is not None:
        data = report_data.build_and_scrub_value_parts()
        req_span.set_tag_str(IAST.JSON, report_data._to_str(data))
    _set_metric_iast_request_tainted()
    base._set_span_tag_iast_request_tainted(req_span)
    _set_span_tag_iast_executed_sink(req_span)

    base.set_iast_request_enabled(False)
    base.end_iast_context(req_span)

    if req_span.get_tag(_ORIGIN_KEY) is None:
        req_span.set_tag_str(_ORIGIN_KEY, APPSEC.ORIGIN_VALUE)

    oce.release_request()


def _iast_end_request(ctx=None, span=None, *args, **kwargs):
    try:
        move_to_root = base._move_iast_data_to_root_span()
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
                        base.end_iast_context(req_span)
                        oce.release_request()
                        return

                    req_span.set_metric(IAST.ENABLED, 1.0)
                    _create_and_attach_iast_report_to_span(req_span, existing_data, merge=False)

            elif move_to_root:
                # Data exists from a previous request, we will merge both reports
                _create_and_attach_iast_report_to_span(req_span, existing_data, merge=True)

            reset_request_vulnerabilities()
    except Exception:
        log.debug("iast::propagation::context::Error finishing IAST context", exc_info=True)
