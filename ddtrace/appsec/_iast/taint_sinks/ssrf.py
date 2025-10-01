from ddtrace.appsec._constants import IAST
from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast._iast_request_context_base import is_iast_request_enabled
from ddtrace.appsec._iast._logs import iast_error
from ddtrace.appsec._iast._logs import iast_propagation_sink_point_debug_log
from ddtrace.appsec._iast._metrics import _set_metric_iast_executed_sink
from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
from ddtrace.appsec._iast._span_metrics import increment_iast_span_metric
from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast._taint_tracking import get_ranges
from ddtrace.appsec._iast.constants import VULN_SSRF
from ddtrace.appsec._iast.taint_sinks._base import VulnerabilityBase
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value


class SSRF(VulnerabilityBase):
    vulnerability_type = VULN_SSRF
    secure_mark = VulnerabilityType.SSRF


_FUNC_TO_URL_ARGUMENT = {
    "requests.api": (0, "url"),
    "urllib.request": (0, "url"),
    "urllib3": (0, "url"),
    "http.client": (1, "url"),
    "urllib3._request_methods": (1, "url"),
    "webbrowser": (0, "url"),
}


IS_REPORTED_INTRUMENTED_SINK_METRIC = False


def _iast_report_ssrf(func_name: str, module_name, *args, **kwargs):
    """
    Check and report potential SSRF (Server-Side Request Forgery) vulnerabilities in function calls.

    This function analyzes calls to URL-handling functions to detect potential SSRF vulnerabilities.
    It checks if the URL argument is tainted (user-controlled) and reports it if conditions are met.
    URL fragments (parts after #) are handled specially - if all tainted parts are in the fragment,
    no vulnerability is reported.
    """
    global IS_REPORTED_INTRUMENTED_SINK_METRIC
    if not IS_REPORTED_INTRUMENTED_SINK_METRIC:
        _set_metric_iast_instrumented_sink(VULN_SSRF)
        IS_REPORTED_INTRUMENTED_SINK_METRIC = True

    arg_pos, kwarg_name = _FUNC_TO_URL_ARGUMENT.get(module_name, (None, None))
    if arg_pos is None:
        iast_propagation_sink_point_debug_log(
            f"{module_name}.{func_name} not found in list of functions supported for SSRF"
        )
        return

    try:
        kw = kwarg_name if kwarg_name else ""
        report_ssrf = get_argument_value(list(args), kwargs, arg_pos, kw)
    except ArgumentError:
        iast_propagation_sink_point_debug_log(
            f"Failed to get URL argument from _FUNC_TO_URL_ARGUMENT dict for function {module_name}.{func_name}"
        )
        return
    if report_ssrf and isinstance(report_ssrf, IAST.TEXT_TYPES):
        if is_iast_request_enabled():
            try:
                if SSRF.has_quota() and SSRF.is_tainted_pyobject(report_ssrf):
                    valid_to_report = True
                    fragment_start = report_ssrf.find("#")
                    taint_ranges = get_ranges(report_ssrf)
                    if fragment_start != -1:
                        # If all taint ranges are in the fragment, do not report
                        if all(r.start >= fragment_start for r in taint_ranges):
                            valid_to_report = False
                    if valid_to_report:
                        SSRF.report(evidence_value=report_ssrf)

                # Reports Span Metrics
                _set_metric_iast_executed_sink(SSRF.vulnerability_type)
                # Report Telemetry Metrics
                increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, SSRF.vulnerability_type)
            except Exception as e:
                iast_error("propagation::sink_point::Error in _iast_report_ssrf", e)
