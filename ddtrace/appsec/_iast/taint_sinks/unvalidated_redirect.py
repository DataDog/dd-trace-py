from typing import Text

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast._iast_request_context import get_iast_reporter
from ddtrace.appsec._iast._iast_request_context_base import is_iast_request_enabled
from ddtrace.appsec._iast._logs import iast_error
from ddtrace.appsec._iast._metrics import _set_metric_iast_executed_sink
from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
from ddtrace.appsec._iast._patch_modules import WrapFunctonsForIAST
from ddtrace.appsec._iast._span_metrics import increment_iast_span_metric
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast._taint_tracking import origin_to_str
from ddtrace.appsec._iast.constants import VULN_UNVALIDATED_REDIRECT
from ddtrace.appsec._iast.secure_marks.base import add_secure_mark
from ddtrace.appsec._iast.taint_sinks._base import VulnerabilityBase
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.utils import get_argument_value


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


_IS_PATCHED = False


def patch():
    global _IS_PATCHED
    if _IS_PATCHED and not asm_config._iast_is_testing:
        return

    if not asm_config._iast_enabled:
        return

    _IS_PATCHED = True

    iast_funcs = WrapFunctonsForIAST()

    iast_funcs.wrap_function("django.shortcuts", "redirect", _unvalidated_redirect_for_django)
    iast_funcs.wrap_function("flask", "redirect", _unvalidated_redirect_for_flask)
    iast_funcs.wrap_function("fastapi.responses", "RedirectResponse", _unvalidated_redirect_forfastapi)

    _set_metric_iast_instrumented_sink(VULN_UNVALIDATED_REDIRECT)

    iast_funcs.patch()


def _unvalidated_redirect_for_django(wrapped, instance, args, kwargs):
    if is_iast_request_enabled():
        _iast_report_unvalidated_redirect(get_argument_value(args, kwargs, 0, "to", optional=True))
    if hasattr(wrapped, "__func__"):
        return wrapped.__func__(instance, *args, **kwargs)
    return wrapped(*args, **kwargs)


def _unvalidated_redirect_for_flask(wrapped, instance, args, kwargs):
    if is_iast_request_enabled():
        _iast_report_unvalidated_redirect(get_argument_value(args, kwargs, 0, "location", optional=True))
    if hasattr(wrapped, "__func__"):
        return wrapped.__func__(instance, *args, **kwargs)
    return wrapped(*args, **kwargs)


def _unvalidated_redirect_forfastapi(wrapped, instance, args, kwargs):
    if is_iast_request_enabled():
        _iast_report_unvalidated_redirect(get_argument_value(args, kwargs, 0, "url", optional=True))
    if hasattr(wrapped, "__func__"):
        return wrapped.__func__(instance, *args, **kwargs)
    return wrapped(*args, **kwargs)


class UnvalidatedRedirect(VulnerabilityBase):
    vulnerability_type = VULN_UNVALIDATED_REDIRECT
    secure_mark = VulnerabilityType.UNVALIDATED_REDIRECT


def _already_reported_unvalidated_redirect(value: Text) -> bool:
    """Return True if an UNVALIDATED_REDIRECT with the same evidence value was already
    reported in the current request.

    A single ``redirect(location)`` reaches IAST through two independent code paths that
    report at different locations (so hash-based deduplication cannot merge them): the
    ``redirect()`` wrapper (reports at the app call site) and the ``Location`` header sink,
    which fires again when Werkzeug re-writes the header during WSGI response finalization
    (``Response.get_wsgi_headers`` runs ``iri_to_uri(location)``, producing a *new* string
    that does not carry the ``secure_mark`` set on the original). Relying on the secure mark
    to suppress the duplicate is therefore flaky; deduplicating on the already-reported
    evidence value is not.

    Only vulnerabilities that were actually emitted are stored in the reporter, so skipping
    on a match can only ever collapse a duplicate (2 -> 1) and never suppress the sole
    report (1 -> 0)."""
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "ignore")
    report = get_iast_reporter()
    if report is None:
        return False
    return any(
        vuln.type == VULN_UNVALIDATED_REDIRECT and vuln.evidence.value == value
        for vuln in report.vulnerabilities
    )


def _iast_report_unvalidated_redirect(headers):
    if headers and isinstance(headers, IAST.TEXT_TYPES):
        try:
            is_tainted = UnvalidatedRedirect.is_tainted_pyobject(
                headers, origins_to_exclude=UNVALIDATED_REDIRECT_ORIGIN_EXCLUSIONS
            )

            if is_tainted:
                try:
                    from ddtrace.appsec._iast._iast_request_context_base import _get_iast_context_id
                    from ddtrace.appsec._iast._iast_request_context_base import _get_iast_env
                    from ddtrace.appsec._iast._taint_tracking import get_ranges

                    _env = _get_iast_env()
                    _endpoint = getattr(_env, "endpoint_route", None) if _env else None
                    _ranges = get_ranges(headers)
                    _rsrc = [
                        (origin_to_str(r.source.origin) if r.source else None, r.source.name if r.source else None)
                        for r in _ranges
                    ]
                    log.error(
                        "URDIAG-SINK ctx=%s endpoint=%s id=%s val=%r range_sources=%s",
                        _get_iast_context_id(),
                        _endpoint,
                        id(headers),
                        headers,
                        _rsrc,
                    )
                except Exception:
                    pass
                if UnvalidatedRedirect.has_quota() and not _already_reported_unvalidated_redirect(headers):
                    UnvalidatedRedirect.report(evidence_value=headers)
                add_secure_mark(headers, [VulnerabilityType.UNVALIDATED_REDIRECT])

            # Reports Span Metrics
            increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, UnvalidatedRedirect.vulnerability_type)
            # Report Telemetry Metrics
            _set_metric_iast_executed_sink(UnvalidatedRedirect.vulnerability_type)
        except Exception as e:
            iast_error("propagation::sink_point::Error in _iast_report_unvalidated_redirect", e)
