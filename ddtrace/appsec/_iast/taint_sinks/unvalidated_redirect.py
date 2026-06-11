from typing import Text

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast._iast_request_context_base import is_iast_request_enabled
from ddtrace.appsec._iast._logs import iast_error
from ddtrace.appsec._iast._metrics import _set_metric_iast_executed_sink
from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
from ddtrace.appsec._iast._patch_modules import WrapFunctonsForIAST
from ddtrace.appsec._iast._span_metrics import increment_iast_span_metric
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
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


def _iast_diag_unvalidated_redirect(headers):
    """THROWAWAY DIAGNOSTIC. Dumps native taint-context slot state at the moment an
    UNVALIDATED_REDIRECT is about to be reported, so we can tell whether the matched
    taint entry lives in the current request's slot (same-slot reuse) or leaked from
    another slot, and whether the key (PyObject address) is shared across slots.
    Grep CI logs for the 'IAST-URDIAG' marker.
    """
    try:
        from ddtrace.appsec._iast._iast_request_context_base import _get_iast_context_id
        from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_free_slots_number
        from ddtrace.appsec._iast._taint_tracking._context import debug_context_array_size
        from ddtrace.appsec._iast._taint_tracking._context import debug_num_tainted_objects
        from ddtrace.appsec._iast._taint_tracking._context import debug_taint_map
        from ddtrace.appsec._iast._taint_tracking._context import is_in_taint_map

        ctx_id = _get_iast_context_id()
        obj_id = id(headers)
        size = debug_context_array_size()
        free = debug_context_array_free_slots_number()
        log.error(
            "IAST-URDIAG report value=%r obj_id=%#x ctx_id=%s slots_size=%s free_slots=%s "
            "in_current_slot=%s in_any_slot=%s",
            headers,
            obj_id,
            ctx_id,
            size,
            free,
            is_in_taint_map(headers, ctx_id) if ctx_id is not None else None,
            is_in_taint_map(headers, None),
        )
        for slot in range(size):
            try:
                n = debug_num_tainted_objects(slot)
            except Exception:
                n = "ERR"
            dump = ""
            try:
                dump = debug_taint_map(slot)
            except Exception as e:  # pragma: no cover
                dump = f"ERR({e})"
            key_present = (f"{obj_id}" in dump) or (f"{obj_id:#x}" in dump)
            log.error(
                "IAST-URDIAG   slot=%s is_current=%s num_tainted=%s key_present=%s map=%s",
                slot,
                slot == ctx_id,
                n,
                key_present,
                dump,
            )
    except Exception as e:  # pragma: no cover
        log.error("IAST-URDIAG failed: %r", e)


def _iast_report_unvalidated_redirect(headers):
    if headers and isinstance(headers, IAST.TEXT_TYPES):
        try:
            is_tainted = UnvalidatedRedirect.is_tainted_pyobject(
                headers, origins_to_exclude=UNVALIDATED_REDIRECT_ORIGIN_EXCLUSIONS
            )

            if is_tainted:
                _iast_diag_unvalidated_redirect(headers)
            if is_tainted:
                if UnvalidatedRedirect.has_quota():
                    UnvalidatedRedirect.report(evidence_value=headers)
                add_secure_mark(headers, [VulnerabilityType.UNVALIDATED_REDIRECT])

            # Reports Span Metrics
            increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, UnvalidatedRedirect.vulnerability_type)
            # Report Telemetry Metrics
            _set_metric_iast_executed_sink(UnvalidatedRedirect.vulnerability_type)
        except Exception as e:
            iast_error("propagation::sink_point::Error in _iast_report_unvalidated_redirect", e)
