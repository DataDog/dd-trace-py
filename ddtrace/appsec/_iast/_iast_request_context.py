from typing import Optional
from typing import Union

from ddtrace.appsec._iast._constants import IAST
from ddtrace.appsec._iast._iast_env import IASTEnvironment
from ddtrace.appsec._iast._iast_env import _get_iast_env
from ddtrace.appsec._iast._iast_env import _report_span_for_request
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
from ddtrace.internal import core
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


def _create_and_attach_iast_report_to_span(req_span: SpanData, environment: IASTEnvironment) -> None:
    report_span = environment.report_span or req_span
    report_data = environment.iast_reporter
    if report_data is not None:
        if report_span is not req_span:
            report_span._set_attributes(
                {
                    key: value
                    for key, value in req_span._get_attributes().items()
                    if (key.startswith("http.") or key == "api.endpoint.route_id")
                    and report_span._get_str_attribute(key) is None
                }
            )
        if environment.endpoint_route and report_span._get_str_attribute("http.route") is None:
            report_span._set_attribute("http.route", environment.endpoint_route)
        data = report_data.build_and_scrub_value_parts()
        if asm_config._use_metastruct_for_iast:
            for source in data.get("sources", []):
                if isinstance(source.get("origin"), OriginType):
                    source["origin"] = origin_to_str(source["origin"])
            report_span._set_struct_tag(IAST.STRUCT, data)
        else:
            report_span._set_attribute(IAST.JSON, report_data._to_str(data))
    if environment.owns_taint_context:
        tainted_objects = debug_num_tainted_objects(environment.context_id) if environment.context_id is not None else 0
        _set_metric_iast_request_tainted(tainted_objects)
        base._set_span_tag_iast_request_tainted(report_span, tainted_objects)
    _set_span_tag_iast_executed_sink(report_span, environment)

    if report_span._get_str_attribute(_ORIGIN_KEY) is None:
        report_span._set_attribute(_ORIGIN_KEY, IAST.ORIGIN_VALUE)


def _iast_end_request(
    scope: Union[ExecutionContext[Event], SpanData], context: Optional[ExecutionContext[Event]] = None
) -> None:
    req_span: Optional[SpanData] = None
    try:
        explicit_context = context is not None or isinstance(scope, ExecutionContext)
        if isinstance(scope, SpanData):
            req_span = scope
            context = context or core.current
        else:
            req_span = scope.get_item("req_span")
            context = scope
        if req_span is None:
            log.debug("iast::propagation::context::Error finishing IAST context. There isn't a SPAN")
            return

        inherited = _get_iast_env(context)
        context_id = _get_iast_context_id()
        environment = None
        # A parent completion callback can run while a nested request is current.
        while context is not None:
            candidate = context.get_item(IAST.REQUEST_CONTEXT_KEY)
            if candidate is not None and candidate.span is req_span:
                environment = candidate
                break
            context = context.parent
        if environment is not None:
            if environment.completed:
                return
            if not explicit_context and environment.owns_taint_context and environment.context_id != context_id:
                return
        elif inherited is not None and (
            (context_id is not None and inherited.context_id == context_id)
            or (
                inherited.completed
                and not req_span._service_entry_span.finished
                and req_span._service_entry_span is inherited.report_span
            )
        ):
            # Same-service wrappers borrow analysis without owning its completion.
            return

        if not asm_config._iast_enabled:
            return
        report_span = environment.report_span if environment is not None else _report_span_for_request(req_span)
        report_span = report_span or req_span
        enabled = float(environment is not None or (not explicit_context and context_id is not None))
        report_span._set_attribute(IAST.ENABLED, max(report_span._get_numeric_attribute(IAST.ENABLED) or 0, enabled))
        if environment is None:
            if not explicit_context:
                base._iast_finish_request(req_span)
            return

        environment.completed = True
        try:
            _create_and_attach_iast_report_to_span(req_span, environment)
        finally:
            base._iast_finish_request(req_span, context=context)
            reset_request_vulnerabilities(environment)
            environment.iast_hash_object_tracking.clear()
            environment.iast_reporter = None
    except Exception:
        log.debug("iast::propagation::context::Error finishing IAST context", exc_info=True)
