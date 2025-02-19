from typing import Text

from wrapt.importer import when_imported

from ddtrace.appsec._common_module_patches import try_unwrap
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._metrics import _set_metric_iast_executed_sink
from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
from ddtrace.appsec._iast._patch import set_and_check_module_is_patched
from ddtrace.appsec._iast._patch import set_module_unpatched
from ddtrace.appsec._iast._patch import try_wrap_function_wrapper
from ddtrace.appsec._iast._taint_tracking._errors import iast_taint_log_error
from ddtrace.appsec._iast.constants import VULN_INSECURE_COOKIE
from ddtrace.appsec._iast.constants import VULN_NO_HTTPONLY_COOKIE
from ddtrace.appsec._iast.constants import VULN_NO_SAMESITE_COOKIE
from ddtrace.settings.asm import config as asm_config

from ._base import VulnerabilityBase


@oce.register
class InsecureCookie(VulnerabilityBase):
    vulnerability_type = VULN_INSECURE_COOKIE
    scrub_evidence = False


@oce.register
class NoHttpOnlyCookie(VulnerabilityBase):
    vulnerability_type = VULN_NO_HTTPONLY_COOKIE


@oce.register
class NoSameSite(VulnerabilityBase):
    vulnerability_type = VULN_NO_SAMESITE_COOKIE


def get_version() -> Text:
    return ""


def patch():
    if not asm_config._iast_enabled:
        return

    if not set_and_check_module_is_patched("flask", default_attr="_datadog_insecure_cookies_patch"):
        return
    if not set_and_check_module_is_patched("django", default_attr="_datadog_insecure_cookies_patch"):
        return
    if not set_and_check_module_is_patched("fastapi", default_attr="_datadog_insecure_cookies_patch"):
        return

    @when_imported("django.http.response")
    def _(m):
        try_wrap_function_wrapper(m, "HttpResponseBase.set_cookie", _iast_response_cookies)

    @when_imported("flask")
    def _(m):
        try_wrap_function_wrapper(m, "Response.set_cookie", _iast_response_cookies)

    @when_imported("starlette.responses")
    def _(m):
        try_wrap_function_wrapper(m, "Response.set_cookie", _iast_response_cookies)

    _set_metric_iast_instrumented_sink(VULN_INSECURE_COOKIE)
    _set_metric_iast_instrumented_sink(VULN_NO_HTTPONLY_COOKIE)
    _set_metric_iast_instrumented_sink(VULN_NO_SAMESITE_COOKIE)


def unpatch():
    try_unwrap("flask", "Response.set_cookie")
    try_unwrap("starlette.responses", "Response.set_cookie")
    try_unwrap("django.http.response", "HttpResponseBase.set_cookie")

    set_module_unpatched("flask", default_attr="_datadog_insecure_cookies_patch")
    set_module_unpatched("django", default_attr="_datadog_insecure_cookies_patch")
    set_module_unpatched("fastapi", default_attr="_datadog_insecure_cookies_patch")


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
            if asm_config._iast_enabled and asm_config.is_iast_request_enabled:
                if kwargs.get("secure") is not True:
                    _set_metric_iast_executed_sink(InsecureCookie.vulnerability_type)
                    InsecureCookie.report(evidence_value=cookie_key)
                if kwargs.get("httponly") is not True:
                    _set_metric_iast_executed_sink(NoHttpOnlyCookie.vulnerability_type)
                    NoHttpOnlyCookie.report(evidence_value=cookie_key)

                samesite = kwargs.get("samesite", "")
                if samesite:
                    samesite = samesite.lower()
                    if not samesite.startswith("strict") and not samesite.startswith("lax"):
                        _set_metric_iast_executed_sink(NoSameSite.vulnerability_type)
                        NoSameSite.report(evidence_value=cookie_key)
    except Exception as e:
        iast_taint_log_error("[IAST] error in asm_check_cookies. {}".format(e))
    return wrapped(*args, **kwargs)
