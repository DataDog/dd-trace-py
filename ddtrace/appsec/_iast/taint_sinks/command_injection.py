from collections.abc import Sequence
from typing import Union

from ddtrace.appsec._iast._constants import IAST
from ddtrace.appsec._iast._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast._iast_request_context_base import is_iast_request_enabled
from ddtrace.appsec._iast._metrics import _set_metric_iast_executed_sink
from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
from ddtrace.appsec._iast._span_metrics import increment_iast_span_metric
from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast.constants import VULN_CMDI

from .._logs import iast_error
from ._base import VulnerabilityBase


class CommandInjection(VulnerabilityBase):
    vulnerability_type = VULN_CMDI
    secure_mark = VulnerabilityType.COMMAND_INJECTION


IS_REPORTED_INTRUMENTED_SINK_METRIC = False


def _iast_report_cmdi(func_name: str, *args: object, **kwargs: object) -> None:
    global IS_REPORTED_INTRUMENTED_SINK_METRIC
    if not IS_REPORTED_INTRUMENTED_SINK_METRIC:
        _set_metric_iast_instrumented_sink(VULN_CMDI)
        IS_REPORTED_INTRUMENTED_SINK_METRIC = True

    report_cmdi: Union[str, bytes, bytearray] = ""
    if len(args) == 0:
        shell_args = kwargs.get("args", [])
    elif isinstance(args[0], (list, tuple)):
        shell_args = args[0]
    else:
        shell_args = args
    try:
        if is_iast_request_enabled():
            if CommandInjection.has_quota():
                from .._taint_tracking.aspects import join_aspect
                from .._taint_tracking.aspects import str_aspect

                if isinstance(shell_args, (list, tuple)):
                    command_args: Sequence[object] = shell_args
                    if "spawn" in func_name:
                        spawn_args = list(command_args[1:])
                        if isinstance(spawn_args[1], (list, tuple)):
                            nested_args: Sequence[object] = spawn_args[1]
                            spawn_args[1] = join_aspect(
                                " ".join, 1, " ", [str_aspect(str, 1, arg) for arg in nested_args]
                            )
                        command_args = spawn_args
                    for arg in command_args:
                        if isinstance(arg, IAST.TEXT_TYPES) and CommandInjection.is_tainted_pyobject(arg):
                            str_shell_args = [str_aspect(str, 1, arg) for arg in command_args]
                            report_cmdi = join_aspect(" ".join, 1, " ", str_shell_args)
                            break
                elif isinstance(shell_args, (str, bytes, bytearray)) and CommandInjection.is_tainted_pyobject(
                    shell_args
                ):
                    report_cmdi = shell_args

                if report_cmdi and isinstance(report_cmdi, IAST.TEXT_TYPES):
                    CommandInjection.report(evidence_value=report_cmdi)

            # Reports Span Metrics
            increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, CommandInjection.vulnerability_type)
        # Report Telemetry Metrics
        _set_metric_iast_executed_sink(CommandInjection.vulnerability_type)
    except Exception as e:
        iast_error("propagation::sink_point::Error in _iast_report_cmdi", e)
