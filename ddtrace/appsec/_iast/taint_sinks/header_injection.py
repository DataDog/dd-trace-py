import typing
from typing import Text

from wrapt.importer import when_imported

from ddtrace.appsec._common_module_patches import try_unwrap
from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast._logs import iast_error
from ddtrace.appsec._iast._logs import iast_instrumentation_wrapt_debug_log
from ddtrace.appsec._iast._metrics import _set_metric_iast_executed_sink
from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
from ddtrace.appsec._iast._patch import set_and_check_module_is_patched
from ddtrace.appsec._iast._patch import set_module_unpatched
from ddtrace.appsec._iast._patch import try_wrap_function_wrapper
from ddtrace.appsec._iast._span_metrics import increment_iast_span_metric
from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast.constants import HEADER_NAME_VALUE_SEPARATOR
from ddtrace.appsec._iast.constants import VULN_HEADER_INJECTION
from ddtrace.appsec._iast.taint_sinks._base import VulnerabilityBase
from ddtrace.appsec._iast.taint_sinks.unvalidated_redirect import _iast_report_unvalidated_redirect
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)

HEADER_INJECTION_EXCLUSIONS = {
    "pragma",
    "content-type",
    "content-length",
    "content-encoding",
    "transfer-encoding",
    "set-cookie",
    "vary",
    "access-control-allow-",
    "sec-websocket-location",
    "sec-websocket-accept",
    "connection",
}


def get_version() -> Text:
    return ""


def patch():
    if not asm_config._iast_enabled:
        return

    if not set_and_check_module_is_patched("flask", default_attr="_datadog_header_injection_patch"):
        return
    if not set_and_check_module_is_patched("django", default_attr="_datadog_header_injection_patch"):
        return
    if not set_and_check_module_is_patched("fastapi", default_attr="_datadog_header_injection_patch"):
        return

    @when_imported("wsgiref.headers")
    def _(m):
        try_wrap_function_wrapper(m, "Headers.add_header", _iast_set_headers)
        try_wrap_function_wrapper(m, "Headers.__setitem__", _iast_set_headers)

    @when_imported("werkzeug.datastructures")
    def _(m):
        try_wrap_function_wrapper(m, "Headers.add", _iast_set_headers)
        try_wrap_function_wrapper(m, "Headers.set", _iast_set_headers)

    @when_imported("django.http.response")
    def _(m):
        try_wrap_function_wrapper(m, "ResponseHeaders.__init__", _iast_django_response_store)

    # For headers["foo"] = "bar"
    @when_imported("starlette.datastructures")
    def _(m):
        try_wrap_function_wrapper(m, "MutableHeaders.__setitem__", _iast_set_headers)

    # For Response("ok", header=...)
    @when_imported("starlette.responses")
    def _(m):
        try_wrap_function_wrapper(m, "Response.init_headers", _iast_set_headers)

    _set_metric_iast_instrumented_sink(VULN_HEADER_INJECTION)
    iast_instrumentation_wrapt_debug_log("Patching header injection correctly")


def unpatch():
    try_unwrap("wsgiref.headers", "Headers.add_header")
    try_unwrap("wsgiref.headers", "Headers.__setitem__")
    try_unwrap("werkzeug.datastructures", "Headers.set")
    try_unwrap("werkzeug.datastructures", "Headers.add")
    try_unwrap("django.http.response", "ResponseHeaders.__init__")
    try_unwrap("starlette.datastructures", "MutableHeaders.__setitem__")
    try_unwrap("starlette.responses", "Response.init_headers")

    set_module_unpatched("flask", default_attr="_datadog_header_injection_patch")
    set_module_unpatched("django", default_attr="_datadog_header_injection_patch")
    set_module_unpatched("fastapi", default_attr="_datadog_header_injection_patch")


class HeaderInjection(VulnerabilityBase):
    """
    Represents a Header Injection vulnerability in the IAST system.

    This class defines the vulnerability type and secure mark for header injection attacks.
    It inherits from VulnerabilityBase to provide common vulnerability handling functionality.
    """

    vulnerability_type = VULN_HEADER_INJECTION
    secure_mark = VulnerabilityType.HEADER_INJECTION


def _iast_django_response_store(wrapped, instance, args, kwargs):
    wrapped.__func__(instance, *args, **kwargs)
    instance._store = HeaderInjectionDict()


class HeaderInjectionDict(dict):
    def __setitem__(self, key, value):
        if asm_config.is_iast_request_enabled:
            _check_type_headers_and_report_header_injection(value)
        dict.__setitem__(self, key, value)


def _iast_set_headers(wrapped, instance, args, kwargs):
    """
    Wrapper for header setting functions to detect header injection vulnerabilities.

    This function wraps methods that set HTTP headers to check for potential header injection
    vulnerabilities before the headers are set. It runs after the wrapped function to account
    for any validators or serializers that might modify the header values.
    """
    if hasattr(wrapped, "__func__"):
        # We call `_check_type_headers_and_report_header_injection` after the wrapped function because it may
        # contain validators and serializers that modify the `args` ranges.
        result = wrapped.__func__(instance, *args, **kwargs)
        if asm_config.is_iast_request_enabled:
            _check_type_headers_and_report_header_injection(args)
        return result
    return wrapped(*args, **kwargs)


def _iast_report_header_injection(headers_args):
    """
    Process a header tuple to check for potential header injection vulnerabilities.

    This function analyzes a header name-value pair for:
    - Header injection vulnerabilities
    - Unvalidated redirects (for Location headers)
    - Excluded headers that should not be checked
    """
    from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

    if len(headers_args) != 2:
        return

    header_name, header_value = headers_args
    if header_name is None:
        return
    try:
        for header_to_exclude in HEADER_INJECTION_EXCLUSIONS:
            header_name_lower = header_name.lower()
            if header_name_lower == "location":
                _iast_report_unvalidated_redirect(header_value)
                return
            if header_name_lower == header_to_exclude or header_name_lower.startswith(header_to_exclude):
                return

        if HeaderInjection.has_quota() and (
            HeaderInjection.is_tainted_pyobject(header_name) or HeaderInjection.is_tainted_pyobject(header_value)
        ):
            header_evidence = add_aspect(add_aspect(header_name, HEADER_NAME_VALUE_SEPARATOR), header_value)
            HeaderInjection.report(evidence_value=header_evidence)

        # Reports Span Metrics
        increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, HeaderInjection.vulnerability_type)
        # Report Telemetry Metrics
        _set_metric_iast_executed_sink(HeaderInjection.vulnerability_type)
    except Exception as e:
        iast_error(f"propagation::sink_point::Error in _iast_report_header_injection. {e}")


def _check_type_headers_and_report_header_injection(headers_or_args) -> None:
    """
    Report potential header injection vulnerabilities found in headers.

    This function handles two types of header inputs:
    1. Dictionary of headers (used by FastAPI Response constructor)
    2. Tuple of (header_name, header_value)
    """
    if headers_or_args and isinstance(headers_or_args[0], typing.Mapping):
        # ({header_name: header_value}, {header_name: header_value}, ...), used by FastAPI Response constructor
        # when used with Response(..., headers={...})
        for headers_dict in headers_or_args:
            for header_name, header_value in headers_dict.items():
                _iast_report_header_injection((header_name, header_value))
    else:
        # (header_name, header_value), used in other cases
        _iast_report_header_injection(headers_or_args)
