from typing import Text

from ddtrace.appsec._common_module_patches import try_unwrap
from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast._logs import iast_error
from ddtrace.appsec._iast._metrics import _set_metric_iast_executed_sink
from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
from ddtrace.appsec._iast._overhead_control_engine import oce
from ddtrace.appsec._iast._patch import set_and_check_module_is_patched
from ddtrace.appsec._iast._patch import set_module_unpatched
from ddtrace.appsec._iast._patch import try_wrap_function_wrapper
from ddtrace.appsec._iast._span_metrics import increment_iast_span_metric
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from ddtrace.appsec._iast.constants import VULN_XSS
from ddtrace.appsec._iast.taint_sinks._base import VulnerabilityBase
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)


@oce.register
class XSS(VulnerabilityBase):
    vulnerability_type = VULN_XSS


def get_version() -> Text:
    return ""


def patch():
    if not asm_config._iast_enabled:
        return

    if not set_and_check_module_is_patched("flask", default_attr="_datadog_xss_patch"):
        return
    if not set_and_check_module_is_patched("django", default_attr="_datadog_xss_patch"):
        return
    if not set_and_check_module_is_patched("fastapi", default_attr="_datadog_xss_patch"):
        return

    try_wrap_function_wrapper(
        "django.utils.safestring",
        "mark_safe",
        _iast_django_xss,
    )

    try_wrap_function_wrapper(
        "django.template.defaultfilters",
        "mark_safe",
        _iast_django_xss,
    )

    try_wrap_function_wrapper(
        "jinja2.filters",
        "do_mark_safe",
        _iast_jinja2_xss,
    )
    try_wrap_function_wrapper(
        "flask",
        "render_template_string",
        _iast_jinja2_xss,
    )

    _set_metric_iast_instrumented_sink(VULN_XSS)
    # Even when starting the application with `ddtrace-run ddtrace-run`, `jinja2.FILTERS` is created before this patch
    # function executes. Therefore, we update the in-memory object with the newly patched version.
    try:
        from jinja2.filters import FILTERS
        from jinja2.filters import do_mark_safe

        FILTERS["safe"] = do_mark_safe
    except (ImportError, KeyError):
        pass


def unpatch():
    try_unwrap("django.utils.safestring", "mark_safe")
    try_unwrap("django.template.defaultfilters", "mark_safe")

    set_module_unpatched("flask", default_attr="_datadog_xss_patch")
    set_module_unpatched("django", default_attr="_datadog_xss_patch")
    set_module_unpatched("fastapi", default_attr="_datadog_xss_patch")


def _iast_django_xss(wrapped, instance, args, kwargs):
    if args and len(args) >= 1:
        _iast_report_xss(args[0])
    return wrapped(*args, **kwargs)


def _iast_jinja2_xss(wrapped, instance, args, kwargs):
    if args and len(args) >= 1:
        _iast_report_xss(args[0])
    return wrapped(*args, **kwargs)


def _iast_report_xss(code_string: Text):
    try:
        if asm_config.is_iast_request_enabled:
            if XSS.has_quota() and is_pyobject_tainted(code_string):
                XSS.report(evidence_value=code_string)

            # Reports Span Metrics
            increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, XSS.vulnerability_type)
            # Report Telemetry Metrics
            _set_metric_iast_executed_sink(XSS.vulnerability_type)
    except Exception as e:
        iast_error(f"propagation::sink_point::Error in _iast_report_xss. {e}")
