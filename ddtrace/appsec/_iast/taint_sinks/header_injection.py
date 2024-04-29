import re
from typing import Any
from typing import Dict

from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config

from ..._common_module_patches import try_unwrap
from ..._constants import IAST_SPAN_TAGS
from .. import oce
from .._metrics import _set_metric_iast_instrumented_sink
from .._metrics import increment_iast_span_metric
from .._patch import set_and_check_module_is_patched
from .._patch import set_module_unpatched
from .._patch import try_wrap_function_wrapper
from .._utils import _has_to_scrub
from .._utils import _scrub
from .._utils import _scrub_get_tokens_positions
from ..constants import EVIDENCE_HEADER_INJECTION
from ..constants import VULN_HEADER_INJECTION
from ..reporter import IastSpanReporter
from ..reporter import Vulnerability
from ._base import VulnerabilityBase


log = get_logger(__name__)

_HEADERS_NAME_REGEXP = re.compile(
    r"(?:p(?:ass)?w(?:or)?d|pass(?:_?phrase)?|secret|(?:api_?|private_?|public_?|access_?|secret_?)key(?:_?id)?|token|consumer_?(?:id|key|secret)|sign(?:ed|ature)?|auth(?:entication|orization)?)",
    re.IGNORECASE,
)
_HEADERS_VALUE_REGEXP = re.compile(
    r"(?:bearer\\s+[a-z0-9\\._\\-]+|glpat-[\\w\\-]{20}|gh[opsu]_[0-9a-zA-Z]{36}|ey[I-L][\\w=\\-]+\\.ey[I-L][\\w=\\-]+(?:\\.[\\w.+/=\\-]+)?|(?:[\\-]{5}BEGIN[a-z\\s]+PRIVATE\\sKEY[\\-]{5}[^\\-]+[\\-]{5}END[a-z\\s]+PRIVATE\\sKEY[\\-]{5}|ssh-rsa\\s*[a-z0-9/\\.+]{100,}))",
    re.IGNORECASE,
)


def get_version():
    # type: () -> str
    return ""


def patch():
    if not asm_config._iast_enabled:
        return

    if not set_and_check_module_is_patched("flask", default_attr="_datadog_header_injection_patch"):
        return
    if not set_and_check_module_is_patched("django", default_attr="_datadog_header_injection_patch"):
        return

    try_wrap_function_wrapper(
        "wsgiref.headers",
        "Headers.add_header",
        _iast_h,
    )
    try_wrap_function_wrapper(
        "wsgiref.headers",
        "Headers.__setitem__",
        _iast_h,
    )
    try_wrap_function_wrapper(
        "werkzeug.datastructures",
        "Headers.set",
        _iast_h,
    )
    try_wrap_function_wrapper(
        "werkzeug.datastructures",
        "Headers.add",
        _iast_h,
    )

    # Django
    try_wrap_function_wrapper(
        "django.http.response",
        "HttpResponseBase.__setitem__",
        _iast_h,
    )
    try_wrap_function_wrapper(
        "django.http.response",
        "ResponseHeaders.__setitem__",
        _iast_h,
    )

    _set_metric_iast_instrumented_sink(VULN_HEADER_INJECTION, 1)


def unpatch():
    # type: () -> None
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
    evidence_type = EVIDENCE_HEADER_INJECTION
    redact_report = True

    @classmethod
    def report(cls, evidence_value=None, sources=None):
        if isinstance(evidence_value, (str, bytes, bytearray)):
            from .._taint_tracking import taint_ranges_as_evidence_info

            evidence_value, sources = taint_ranges_as_evidence_info(evidence_value)
        super(HeaderInjection, cls).report(evidence_value=evidence_value, sources=sources)

    @classmethod
    def _extract_sensitive_tokens(cls, vulns_to_text: Dict[Vulnerability, str]) -> Dict[int, Dict[str, Any]]:
        ret = {}  # type: Dict[int, Dict[str, Any]]
        for vuln, text in vulns_to_text.items():
            vuln_hash = hash(vuln)
            ret[vuln_hash] = {
                "tokens": set(_HEADERS_NAME_REGEXP.findall(text) + _HEADERS_VALUE_REGEXP.findall(text)),
            }
            ret[vuln_hash]["token_positions"] = _scrub_get_tokens_positions(text, ret[vuln_hash]["tokens"])

        return ret

    @classmethod
    def _redact_report(cls, report: IastSpanReporter) -> IastSpanReporter:
        """TODO: this algorithm is not working as expected, it needs to be fixed."""
        if not asm_config._iast_redaction_enabled:
            return report

        try:
            for vuln in report.vulnerabilities:
                # Use the initial hash directly as iteration key since the vuln itself will change
                if vuln.type == VULN_HEADER_INJECTION:
                    scrub_the_following_elements = False
                    new_value_parts = []
                    for value_part in vuln.evidence.valueParts:
                        if _HEADERS_VALUE_REGEXP.match(value_part["value"]) or scrub_the_following_elements:
                            value_part["pattern"] = _scrub(value_part["value"], has_range=True)
                            value_part["redacted"] = True
                            del value_part["value"]
                        elif _has_to_scrub(value_part["value"]) or _HEADERS_NAME_REGEXP.match(value_part["value"]):
                            scrub_the_following_elements = True
                        new_value_parts.append(value_part)
                    vuln.evidence.valueParts = new_value_parts
        except (ValueError, KeyError):
            log.debug("an error occurred while redacting cmdi", exc_info=True)
        return report


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

    if is_pyobject_tainted(header_name) or is_pyobject_tainted(header_value):
        header_evidence = add_aspect(add_aspect(header_name, ": "), header_value)
        HeaderInjection.report(evidence_value=header_evidence)
