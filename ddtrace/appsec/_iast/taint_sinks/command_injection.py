from typing import List
from typing import Union

from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._metrics import _set_metric_iast_executed_sink
from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
from ddtrace.appsec._iast._metrics import increment_iast_span_metric
from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast.constants import VULN_CMDI
import ddtrace.contrib.internal.subprocess.patch as subprocess_patch
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config

from .._logs import iast_error
from ._base import VulnerabilityBase


log = get_logger(__name__)


def get_version() -> str:
    return ""


_IAST_CMDI = "iast_cmdi"


def patch():
    if asm_config._iast_enabled:
        subprocess_patch.patch()
        subprocess_patch.add_str_callback(_IAST_CMDI, _iast_report_cmdi)
        subprocess_patch.add_lst_callback(_IAST_CMDI, _iast_report_cmdi)
        _set_metric_iast_instrumented_sink(VULN_CMDI)


def unpatch() -> None:
    subprocess_patch.del_str_callback(_IAST_CMDI)
    subprocess_patch.del_lst_callback(_IAST_CMDI)


@oce.register
class CommandInjection(VulnerabilityBase):
    vulnerability_type = VULN_CMDI
    secure_mark = VulnerabilityType.COMMAND_INJECTION


def _iast_report_cmdi(shell_args: Union[str, List[str]]) -> None:
    report_cmdi = ""

    increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, CommandInjection.vulnerability_type)
    _set_metric_iast_executed_sink(CommandInjection.vulnerability_type)
    try:
        if asm_config.is_iast_request_enabled and CommandInjection.has_quota():
            from .._taint_tracking.aspects import join_aspect

            if isinstance(shell_args, (list, tuple)):
                for arg in shell_args:
                    if CommandInjection.is_valid_tainted(arg):
                        report_cmdi = join_aspect(" ".join, 1, " ", shell_args)
                        break
            elif CommandInjection.is_valid_tainted(shell_args):
                report_cmdi = shell_args

            if report_cmdi:
                CommandInjection.report(evidence_value=report_cmdi)
    except Exception as e:
        iast_error(f"propagation::sink_point::Error in _iast_report_ssrf. {e}")
