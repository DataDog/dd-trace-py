from typing import Text

from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast._logs import iast_error
from ddtrace.appsec._iast._metrics import _set_metric_iast_executed_sink
from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
from ddtrace.appsec._iast._patch_modules import WrapFunctonsForIAST
from ddtrace.appsec._iast._span_metrics import increment_iast_span_metric
from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast.constants import VULN_INSECURE_COOKIE
from ddtrace.appsec._iast.constants import VULN_NO_HTTPONLY_COOKIE
from ddtrace.appsec._iast.constants import VULN_NO_SAMESITE_COOKIE
from ddtrace.appsec._iast.sampling.vulnerability_detection import should_process_vulnerability
from ddtrace.appsec._iast.taint_sinks._base import VulnerabilityBase
from ddtrace.settings.asm import config as asm_config


class InsecureCookie(VulnerabilityBase):
    vulnerability_type = VULN_INSECURE_COOKIE


class NoHttpOnlyCookie(VulnerabilityBase):
    vulnerability_type = VULN_NO_HTTPONLY_COOKIE
    secure_mark = VulnerabilityType.NO_HTTPONLY_COOKIE


class NoSameSite(VulnerabilityBase):
    vulnerability_type = VULN_NO_SAMESITE_COOKIE
    secure_mark = VulnerabilityType.NO_SAMESITE_COOKIE


class CookiesVulnerability(VulnerabilityBase):
    vulnerability_type = "COOKIES_VULNERABILITY"

    @classmethod
    def report_cookies(cls, evidence_value, insecure_cookie, no_http_only, no_samesite) -> None:
        """Build a IastSpanReporter instance to report it in the `AppSecIastSpanProcessor` as a string JSON"""
        if insecure_cookie or no_http_only or no_samesite:
            if should_process_vulnerability(InsecureCookie.vulnerability_type):
                file_name, line_number, function_name, class_name = cls._compute_file_line()
                if file_name is None:
                    return
                if insecure_cookie:
                    increment_iast_span_metric(
                        IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, InsecureCookie.vulnerability_type
                    )
                    _set_metric_iast_executed_sink(InsecureCookie.vulnerability_type)
                    InsecureCookie._create_evidence_and_report(
                        InsecureCookie.vulnerability_type,
                        evidence_value,
                        None,
                        file_name,
                        line_number,
                        function_name,
                        class_name,
                        InsecureCookie.vulnerability_type,  # Extra field in args to skip deduplication
                    )

                if no_http_only:
                    increment_iast_span_metric(
                        IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, NoHttpOnlyCookie.vulnerability_type
                    )
                    _set_metric_iast_executed_sink(NoHttpOnlyCookie.vulnerability_type)
                    NoHttpOnlyCookie._create_evidence_and_report(
                        NoHttpOnlyCookie.vulnerability_type,
                        evidence_value,
                        None,
                        file_name,
                        line_number,
                        function_name,
                        class_name,
                        NoHttpOnlyCookie.vulnerability_type,  # Extra field in args to skip deduplication
                    )

                if no_samesite:
                    increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, NoSameSite.vulnerability_type)
                    _set_metric_iast_executed_sink(NoSameSite.vulnerability_type)
                    NoSameSite._create_evidence_and_report(
                        NoSameSite.vulnerability_type,
                        evidence_value,
                        None,
                        file_name,
                        line_number,
                        function_name,
                        class_name,
                        NoSameSite.vulnerability_type,  # Extra field in args to skip deduplication
                    )


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

    iast_funcs.wrap_function("django.http.response", "HttpResponseBase.set_cookie", _iast_response_cookies)
    iast_funcs.wrap_function("flask", "Response.set_cookie", _iast_response_cookies)
    iast_funcs.wrap_function("starlette.responses", "Response.set_cookie", _iast_response_cookies)

    iast_funcs.patch()

    _set_metric_iast_instrumented_sink(VULN_INSECURE_COOKIE)
    _set_metric_iast_instrumented_sink(VULN_NO_HTTPONLY_COOKIE)
    _set_metric_iast_instrumented_sink(VULN_NO_SAMESITE_COOKIE)


def _iast_response_cookies(wrapped, instance, args, kwargs):
    try:
        cookie_key = ""
        cookie_value = ""
        if len(args) > 1:
            cookie_key = args[0]
            cookie_value = args[1]
        elif len(kwargs.keys()) > 0:
            cookie_key = kwargs.get("key")
            cookie_value = kwargs.get("value")

        if cookie_value and cookie_key:
            if asm_config.is_iast_request_enabled and CookiesVulnerability.has_quota():
                report_samesite = False
                samesite = kwargs.get("samesite", "")
                if samesite:
                    samesite = samesite.lower()
                    report_samesite = not samesite.startswith("strict") and not samesite.startswith("lax")

                CookiesVulnerability.report_cookies(
                    cookie_key, kwargs.get("secure") is not True, kwargs.get("httponly") is not True, report_samesite
                )
    except Exception as e:
        iast_error(f"propagation::sink_point::Error in _iast_response_cookies. {e}")
    return wrapped(*args, **kwargs)
