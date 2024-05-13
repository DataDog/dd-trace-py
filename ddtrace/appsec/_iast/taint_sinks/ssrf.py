from typing import Callable

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.importlib import func_name

from ..._constants import IAST_SPAN_TAGS
from .. import oce
from .._metrics import increment_iast_span_metric
from ..constants import VULN_SSRF
from ..processor import AppSecIastSpanProcessor
from ._base import VulnerabilityBase


log = get_logger(__name__)


@oce.register
class SSRF(VulnerabilityBase):
    vulnerability_type = VULN_SSRF


_FUNC_TO_URL_ARGUMENT = {
    "http.client.request": (1, "url"),
    "requests.sessions.request": (1, "url"),
    "urllib.request.urlopen": (0, "url"),
    "urllib3._request_methods.request": (1, "url"),
    "urllib3.request.request": (1, "url"),
    "webbrowser.open": (0, "url"),
}


def _iast_report_ssrf(func: Callable, *args, **kwargs):
    func_key = func_name(func)
    arg_pos, kwarg_name = _FUNC_TO_URL_ARGUMENT.get(func_key, (None, None))
    if arg_pos is None:
        log.debug("%s not found in list of functions supported for SSRF", func_key)
        return

    try:
        kw = kwarg_name if kwarg_name else ""
        report_ssrf = get_argument_value(list(args), kwargs, arg_pos, kw)
    except ArgumentError:
        log.debug("Failed to get URL argument from _FUNC_TO_URL_ARGUMENT dict for function %s", func_key)
        return

    if report_ssrf:
        from .._metrics import _set_metric_iast_executed_sink

        _set_metric_iast_executed_sink(SSRF.vulnerability_type)
        increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, SSRF.vulnerability_type)
        if AppSecIastSpanProcessor.is_span_analyzed() and SSRF.has_quota():
            try:
                from .._taint_tracking import is_pyobject_tainted

                if is_pyobject_tainted(report_ssrf):
                    SSRF.report(evidence_value=report_ssrf)
            except Exception:
                log.debug("Unexpected exception while reporting vulnerability", exc_info=True)
