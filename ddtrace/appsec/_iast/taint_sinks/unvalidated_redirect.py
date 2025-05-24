from typing import Text

from wrapt.importer import when_imported

from ddtrace.appsec._common_module_patches import try_unwrap
from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._logs import iast_error
from ddtrace.appsec._iast._metrics import _set_metric_iast_executed_sink
from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
from ddtrace.appsec._iast._patch import set_and_check_module_is_patched
from ddtrace.appsec._iast._patch import set_module_unpatched
from ddtrace.appsec._iast._patch import try_wrap_function_wrapper
from ddtrace.appsec._iast._span_metrics import increment_iast_span_metric
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast.constants import VULN_UNVALIDATED_REDIRECT
from ddtrace.appsec._iast.taint_sinks._base import VulnerabilityBase
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)

UNVALIDATED_REDIRECT_ORIGIN_EXCLUSIONS = {
    OriginType.HEADER,
    OriginType.HEADER_NAME,
    OriginType.COOKIE,
    OriginType.COOKIE_NAME,
    OriginType.GRPC_BODY,
}


def get_version() -> Text:
    return ""


def patch():
    if not asm_config._iast_enabled:
        return

    if not set_and_check_module_is_patched("flask", default_attr="_datadog_unvalidated_redirect_patch"):
        return
    if not set_and_check_module_is_patched("django", default_attr="_datadog_unvalidated_redirect_patch"):
        return
    if not set_and_check_module_is_patched("fastapi", default_attr="_datadog_unvalidated_redirect_patch"):
        return

    @when_imported("django.shortcuts")
    def _(m):
        try_wrap_function_wrapper(m, "redirect", _unvalidated_redirect_for_django)

    @when_imported("flask")
    def _(m):
        try_wrap_function_wrapper(m, "redirect", _unvalidated_redirect_for_flask)

    @when_imported("fastapi.responses")
    def _(m):
        try_wrap_function_wrapper(m, "RedirectResponse", _unvalidated_redirect_forfastapi)

    _set_metric_iast_instrumented_sink(VULN_UNVALIDATED_REDIRECT)


def unpatch():
    try_unwrap("django.shortcuts", "redirect")
    try_unwrap("flask", "redirect")
    try_unwrap("fastapi.responses", "RedirectResponse")

    set_module_unpatched("flask", default_attr="_datadog_unvalidated_redirect_patch")
    set_module_unpatched("django", default_attr="_datadog_unvalidated_redirect_patch")
    set_module_unpatched("fastapi", default_attr="_datadog_unvalidated_redirect_patch")


def _unvalidated_redirect_for_django(wrapped, instance, args, kwargs):
    if asm_config.is_iast_request_enabled:
        _iast_report_unvalidated_redirect(get_argument_value(args, kwargs, 0, "to", optional=True))
    if hasattr(wrapped, "__func__"):
        return wrapped.__func__(instance, *args, **kwargs)
    return wrapped(*args, **kwargs)


def _unvalidated_redirect_for_flask(wrapped, instance, args, kwargs):
    if asm_config.is_iast_request_enabled:
        _iast_report_unvalidated_redirect(get_argument_value(args, kwargs, 0, "location", optional=True))
    if hasattr(wrapped, "__func__"):
        return wrapped.__func__(instance, *args, **kwargs)
    return wrapped(*args, **kwargs)


def _unvalidated_redirect_forfastapi(wrapped, instance, args, kwargs):
    if asm_config.is_iast_request_enabled:
        _iast_report_unvalidated_redirect(get_argument_value(args, kwargs, 0, "url", optional=True))
    if hasattr(wrapped, "__func__"):
        return wrapped.__func__(instance, *args, **kwargs)
    return wrapped(*args, **kwargs)


@oce.register
class UnvalidatedRedirect(VulnerabilityBase):
    vulnerability_type = VULN_UNVALIDATED_REDIRECT
    secure_mark = VulnerabilityType.UNVALIDATED_REDIRECT


def _iast_report_unvalidated_redirect(headers):
    if headers:
        try:
            is_tainted = UnvalidatedRedirect.is_tainted_pyobject(
                headers, origins_to_exclude=UNVALIDATED_REDIRECT_ORIGIN_EXCLUSIONS
            )

            if UnvalidatedRedirect.has_quota() and is_tainted:
                UnvalidatedRedirect.report(evidence_value=headers)

            # Reports Span Metrics
            increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, UnvalidatedRedirect.vulnerability_type)
            # Report Telemetry Metrics
            _set_metric_iast_executed_sink(UnvalidatedRedirect.vulnerability_type)
        except Exception as e:
            iast_error(f"propagation::sink_point::Error in _iast_report_unvalidated_redirect. {e}")
