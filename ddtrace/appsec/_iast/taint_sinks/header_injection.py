"""
Header Injection sink analysis for common Python web frameworks.

This module documents and tracks the behavior of popular frameworks regarding HTTP header
injection risks, particularly when tainted data is used to set response headers.

Framework-specific findings:

Django:
--------
- Safe usage: Setting headers via `response.headers["Name"] = value` is safe.
  Django uses `ResponseHeaders._convert_to_charset()` to validate header values and
  raises `BadHeaderError` if control characters (`\n`, `\r`) are present.
- Unsafe usage: Direct access to `response.headers._store[...]` bypasses validation,
  making header injection possible.
- Tests using a real Django server (not the test client) confirm that WSGI will
  serialize injected headers if validation is bypassed.

Flask (Werkzeug):
------------------
- Safe usage: The `werkzeug.datastructures.Headers` class validates header values and
  prevents control characters by default (`ValueError` is raised).
- Unsafe usage (theoretical): Even if internal structures like `response.headers._list` are
  modified directly, Werkzeug revalidates header values via `_str_header_value()` when
  `to_wsgi_list()` is called during response finalization. This means that control characters
  (`\r`, `\n`) are caught and raise a `ValueError`, preventing header injection.
  Only by disabling or monkeypatching internal validation methods (e.g., `_validate_value`
  or `_str_header_value`) could protections be bypassed — making this a non-exploitable
  vector under normal circumstances.
- Werkzeug has enforced these validations since version 0.8.
- No known CVEs for header injection via public APIs, but misuse or internal manipulation
  can make injection possible.

FastAPI (Starlette + Uvicorn):
-------------------------------
- Safe usage: Starlette’s `MutableHeaders` class validates against `\r` and `\n`
  when setting headers via the public API.
- Unsafe usage (blocked at runtime): Although it's theoretically possible to manipulate
  `response.raw_headers` to insert invalid values (e.g., with `\r\n`), Uvicorn and its
  HTTP backend (`h11`) validate headers at the point of serialization. If a header
  contains illegal characters, `h11` raises a `LocalProtocolError`, preventing the
  header injection from reaching the client. Therefore, header injection is not feasible
  even with low-level tampering, unless the ASGI server or HTTP stack itself is modified.
- No known CVEs or bypasses via public APIs; injection only possible via direct internal modification.

General notes:
---------------
- WSGI and ASGI servers (like Gunicorn, Uvicorn) usually trust the framework to validate headers.
  If validation is bypassed, these servers may serialize unsafe header values directly to the client.
- Header injection typically requires either a vulnerability in the framework
  (historical in older versions) or developer misuse (e.g., constructing header names/values
  from unsanitized input or bypassing validation mechanisms).

This module implements taint sink detection to track and block cases where tainted data
is passed to header-setting APIs without proper sanitization.
"""  # noqa: D301
import typing
from typing import Text

from wrapt.importer import when_imported

from ddtrace.appsec._common_module_patches import try_unwrap
from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast._logs import iast_error
from ddtrace.appsec._iast._metrics import _set_metric_iast_executed_sink
from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
from ddtrace.appsec._iast._patch import set_and_check_module_is_patched
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
    """
    Patch header injection detection for supported web frameworks.

    The patching strategy varies by framework:

    Django:
    - Patches ResponseHeaders.__init__ to wrap the header store with injection detection
    - Works in conjunction with Django's built-in header validation
    - Allows detection of attempts before Django's BadHeaderError is raised

    Flask/Werkzeug (currently disabled):
    - Would patch Headers.add and Headers.set methods
    - Framework's built-in protection makes this less critical
    - Detection would occur before Werkzeug's ValueError

    FastAPI/Starlette (currently disabled):
    - Would patch MutableHeaders.__setitem__ and Response.init_headers
    - Framework's validation provides primary protection
    - Detection would complement existing checks

    Note: Flask and FastAPI patching is currently disabled as these frameworks
    have robust built-in protections. Django patching is maintained to ensure
    comprehensive vulnerability detection and reporting.
    """
    if not asm_config._iast_enabled:
        return

    if not set_and_check_module_is_patched("django", default_attr="_datadog_header_injection_patch"):
        return

    # Header injection for > Django 3.2
    @when_imported("django.http.response")
    def _(m):
        try_wrap_function_wrapper(m, "ResponseHeaders.__init__", _iast_django_response_store)

    # Header injection for <= Django 2.2
    @when_imported("django.http.response")
    def _(m):
        try_wrap_function_wrapper(m, "HttpResponseBase.__init__", _iast_django_response_store)

    _set_metric_iast_instrumented_sink(VULN_HEADER_INJECTION)


def unpatch():
    try_unwrap("django.http.response", "ResponseHeaders.__init__")


class HeaderInjection(VulnerabilityBase):
    """
    Represents a Header Injection vulnerability in the IAST system.

    Header Injection is a security vulnerability that occurs when an application allows
    unvalidated user input to be included in HTTP response headers. The most common
    attack vector is injecting newline characters (backslash r, backslash n or backslash n) to add unexpected headers
    or modify existing ones.

    Example of vulnerable code:
        response = HttpResponse()
        response.headers["X-Custom-Header"] = user_input  # user_input could contain backslash r, backslash n

    Framework protections:
    - Django: Raises BadHeaderError for newlines in header values
    - Flask/Werkzeug: Raises ValueError for control chars in headers
    - FastAPI/Starlette: Validates and rejects illegal characters

    This class provides:
    - Detection of header injection attempts
    - Reporting of potential vulnerabilities
    - Integration with framework-specific protections
    """

    vulnerability_type = VULN_HEADER_INJECTION
    secure_mark = VulnerabilityType.HEADER_INJECTION


def _iast_django_response_store(wrapped, instance, args, kwargs):
    try:
        from django import VERSION as DJANGO_VERSION

        wrapped.__func__(instance, *args, **kwargs)
        if DJANGO_VERSION < (3, 2, 0):
            print("TAINT HEADERS!!")
            instance._headers = HeaderInjectionDict()
        else:
            instance._store = HeaderInjectionDict()
    except Exception as e:
        iast_error(f"propagation::sink_point::Error in _iast_django_response_store. {e}")


class HeaderInjectionDict(dict):
    def __setitem__(self, key, value):
        if asm_config.is_iast_request_enabled:
            print(f"HeaderInjectionDict!! {key} {value}")
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
        header_name_lower = header_name.lower()
        if header_name_lower == "location":
            _iast_report_unvalidated_redirect(header_value)
            return
        for header_to_exclude in HEADER_INJECTION_EXCLUSIONS:
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
