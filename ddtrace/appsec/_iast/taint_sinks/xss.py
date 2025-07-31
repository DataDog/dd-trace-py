from typing import Text

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast._logs import iast_error
from ddtrace.appsec._iast._metrics import _set_metric_iast_executed_sink
from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
from ddtrace.appsec._iast._patch_modules import WrapFunctonsForIAST
from ddtrace.appsec._iast._span_metrics import increment_iast_span_metric
from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast.constants import VULN_XSS
from ddtrace.appsec._iast.taint_sinks._base import VulnerabilityBase
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)


class XSS(VulnerabilityBase):
    vulnerability_type = VULN_XSS
    secure_mark = VulnerabilityType.XSS


def get_version() -> Text:
    return ""


_IS_PATCHED = False


def patch():
    global _IS_PATCHED
    if _IS_PATCHED and not asm_config._iast_is_testing:
        return

    if not asm_config._iast_enabled:
        return

    _IS_PATCHED = True

    iast_funcs = WrapFunctonsForIAST()

    iast_funcs.wrap_function(
        "django.utils.safestring",
        "mark_safe",
        _iast_django_xss,
    )

    iast_funcs.wrap_function(
        "django.template.defaultfilters",
        "mark_safe",
        _iast_django_xss,
    )

    iast_funcs.wrap_function(
        "jinja2.filters",
        "do_mark_safe",
        _iast_jinja2_xss,
    )
    iast_funcs.wrap_function(
        "flask",
        "render_template_string",
        _iast_jinja2_xss,
    )

    iast_funcs.patch()

    _set_metric_iast_instrumented_sink(VULN_XSS)

    # Even when starting the application with `ddtrace-run ddtrace-run`, `jinja2.FILTERS` is created before this patch
    # function executes. Therefore, we update the in-memory object with the newly patched version.
    @ModuleWatchdog.after_module_imported("jinja2.filters")
    def _(module):
        try:
            from jinja2.filters import FILTERS
            from jinja2.filters import do_mark_safe

            FILTERS["safe"] = do_mark_safe
        except (ImportError, KeyError):
            pass


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
            if isinstance(code_string, IAST.TEXT_TYPES) and XSS.has_quota() and XSS.is_tainted_pyobject(code_string):
                XSS.report(evidence_value=code_string)

            # Reports Span Metrics
            increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, XSS.vulnerability_type)
            # Report Telemetry Metrics
            _set_metric_iast_executed_sink(XSS.vulnerability_type)
    except Exception as e:
        iast_error(f"propagation::sink_point::Error in _iast_report_xss. {e}")
