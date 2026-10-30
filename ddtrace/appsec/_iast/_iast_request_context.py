from typing import Optional
from typing import Union

from ddtrace.appsec._iast._constants import IAST
from ddtrace.appsec._iast._iast_env import IASTEnvironment
from ddtrace.appsec._iast._iast_env import _get_iast_env
import ddtrace.appsec._iast._iast_request_context_base as base
from ddtrace.appsec._iast._metrics import _set_metric_iast_request_tainted
from ddtrace.appsec._iast._span_metrics import _set_span_tag_iast_executed_sink
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import origin_to_str
from ddtrace.appsec._iast._taint_tracking._context import debug_num_tainted_objects
from ddtrace.appsec._iast.reporter import IastSpanReporter
from ddtrace.appsec._iast.sampling.vulnerability_detection import reset_request_vulnerabilities
from ddtrace.appsec._iast_context import _get_iast_context_id
from ddtrace.constants import _ORIGIN_KEY
from ddtrace.internal.core import ExecutionContext
from ddtrace.internal.core.events import Event
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import SpanData
from ddtrace.internal.settings.asm import config as asm_config


log = get_logger(__name__)


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


def _create_and_attach_iast_report_to_span(req_span: SpanData, environment: Optional[IASTEnvironment] = None) -> None:
    context_id = environment.context_id if environment is not None else _get_iast_context_id()
    environment = environment or _get_iast_env()
    report_data = environment.iast_reporter if environment is not None else None
    if report_data is not None:
        data = report_data.build_and_scrub_value_parts()
        if asm_config._use_metastruct_for_iast:
            for source in data.get("sources", []):
                if isinstance(source.get("origin"), OriginType):
                    source["origin"] = origin_to_str(source["origin"])
            req_span._set_struct_tag(IAST.STRUCT, data)
        else:
            req_span._set_attribute(IAST.JSON, report_data._to_str(data))
    tainted = debug_num_tainted_objects(context_id) if context_id is not None else 0
    _set_metric_iast_request_tainted(tainted)
    base._set_span_tag_iast_request_tainted(req_span, tainted)
    _set_span_tag_iast_executed_sink(req_span, environment)

    if req_span._get_str_attribute(_ORIGIN_KEY) is None:
        req_span._set_attribute(_ORIGIN_KEY, IAST.ORIGIN_VALUE)


def _iast_end_request(
    scope: Union[ExecutionContext[Event], SpanData], context: Optional[ExecutionContext[Event]] = None
) -> None:
    req_span: Optional[SpanData] = None
    try:
        environment = _get_iast_env(context)
        context_id = environment.context_id if context is not None and environment else _get_iast_context_id()
        if environment is not None and (context_id is None or environment.context_id != context_id):
            environment = None
        if isinstance(scope, SpanData):
            req_span = scope
        else:
            req_span = scope.get_item("req_span")
        if req_span is None:
            log.debug("iast::propagation::context::Error finishing IAST context. There isn't a SPAN")
            return

        # Nested WEB spans borrow their enclosing request's analysis. They must
        # leave it running until the span that owns it finishes.
        if environment is not None and environment.span is not None and environment.span is not req_span:
            return

        if asm_config._iast_enabled:
            reporting_succeeded = False
            try:
                if req_span._get_numeric_attribute(IAST.ENABLED) is None:
                    enabled = environment is not None if context is not None else base.is_iast_request_enabled()
                    req_span._set_attribute(IAST.ENABLED, float(enabled))
                    if enabled:
                        _create_and_attach_iast_report_to_span(req_span, environment)
                        reporting_succeeded = True
            finally:
                base._iast_finish_request(
                    req_span, shoud_update_global_vulnerability_limit=reporting_succeeded, context=context
                )
                if environment is not None:
                    reset_request_vulnerabilities(environment)
                    environment.iast_hash_object_tracking.clear()
    except Exception:
        log.debug("iast::propagation::context::Error finishing IAST context", exc_info=True)
