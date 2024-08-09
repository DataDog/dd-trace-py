from typing import Text

from wrapt.importer import when_imported

from ddtrace.contrib import trace_utils
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config

from ..._common_module_patches import try_unwrap
from ..._constants import IAST_SPAN_TAGS
from .. import oce
from .._metrics import _set_metric_iast_instrumented_sink
from .._metrics import increment_iast_span_metric
from .._patch import set_and_check_module_is_patched
from .._patch import set_module_unpatched
from ..constants import HEADER_NAME_VALUE_SEPARATOR
from ..constants import VULN_HEADER_INJECTION
from ..processor import AppSecIastSpanProcessor
from ._base import VulnerabilityBase


log = get_logger(__name__)


def get_version() -> Text:
    return ""


def patch():
    if not asm_config._iast_enabled:
        return

    if not set_and_check_module_is_patched("flask", default_attr="_datadog_header_injection_patch"):
        return
    if not set_and_check_module_is_patched("django", default_attr="_datadog_header_injection_patch"):
        return

    @when_imported("wsgiref.headers")
    def _(m):
        trace_utils.wrap(m, "Headers.add_header", _iast_h)
        trace_utils.wrap(m, "Headers.__setitem__", _iast_h)

    @when_imported("werkzeug.datastructures")
    def _(m):
        trace_utils.wrap(m, "Headers.add", _iast_h)
        trace_utils.wrap(m, "Headers.set", _iast_h)

    @when_imported("django.http.response")
    def _(m):
        trace_utils.wrap(m, "HttpResponse.__setitem__", _iast_h)
        trace_utils.wrap(m, "HttpResponseBase.__setitem__", _iast_h)
        try:
            trace_utils.wrap(m, "ResponseHeaders.__setitem__", _iast_h)
        except AttributeError:
            # no ResponseHeaders in django<3
            pass

    _set_metric_iast_instrumented_sink(VULN_HEADER_INJECTION)


def unpatch():
    try_unwrap("wsgiref.headers", "Headers.add_header")
    try_unwrap("wsgiref.headers", "Headers.__setitem__")
    try_unwrap("werkzeug.datastructures", "Headers.set")
    try_unwrap("werkzeug.datastructures", "Headers.add")
    try_unwrap("django.http.response", "HttpResponseBase.__setitem__")
    try_unwrap("django.http.response", "ResponseHeaders.__setitem__")

    set_module_unpatched("flask", default_attr="_datadog_header_injection_patch")
    set_module_unpatched("django", default_attr="_datadog_header_injection_patch")

    pass


def _iast_h(wrapped, instance, args, kwargs):
    if asm_config._iast_enabled:
        _iast_report_header_injection(args)
    return wrapped(*args, **kwargs)


@oce.register
class HeaderInjection(VulnerabilityBase):
    vulnerability_type = VULN_HEADER_INJECTION


def _iast_report_header_injection(headers_args) -> None:
    headers_exclusion = {
        "content-type",
        "content-length",
        "content-encoding",
        "transfer-encoding",
        "set-cookie",
        "vary",
    }
    from .._metrics import _set_metric_iast_executed_sink
    from .._taint_tracking import is_pyobject_tainted
    from .._taint_tracking.aspects import add_aspect

    header_name, header_value = headers_args
    for header_to_exclude in headers_exclusion:
        header_name_lower = header_name.lower()
        if header_name_lower == header_to_exclude or header_name_lower.startswith(header_to_exclude):
            return

    increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, HeaderInjection.vulnerability_type)
    _set_metric_iast_executed_sink(HeaderInjection.vulnerability_type)

    if AppSecIastSpanProcessor.is_span_analyzed() and HeaderInjection.has_quota():
        if is_pyobject_tainted(header_name) or is_pyobject_tainted(header_value):
            header_evidence = add_aspect(add_aspect(header_name, HEADER_NAME_VALUE_SEPARATOR), header_value)
            HeaderInjection.report(evidence_value=header_evidence)
