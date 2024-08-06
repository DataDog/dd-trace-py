import os
import subprocess  # nosec
from typing import List
from typing import Union

from ddtrace.contrib import trace_utils
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config

from ..._constants import IAST_SPAN_TAGS
from .. import oce
from .._metrics import _set_metric_iast_instrumented_sink
from .._metrics import increment_iast_span_metric
from ..constants import VULN_CMDI
from ..processor import AppSecIastSpanProcessor
from ._base import VulnerabilityBase


log = get_logger(__name__)


def get_version() -> str:
    return ""


def patch():
    if not asm_config._iast_enabled:
        return

    if not getattr(os, "_datadog_cmdi_patch", False):
        # all os.spawn* variants eventually use this one:
        trace_utils.wrap(os, "_spawnvef", _iast_cmdi_osspawn)

    if not getattr(subprocess, "_datadog_cmdi_patch", False):
        trace_utils.wrap(subprocess, "Popen.__init__", _iast_cmdi_subprocess_init)

        os._datadog_cmdi_patch = True
        subprocess._datadog_cmdi_patch = True

    _set_metric_iast_instrumented_sink(VULN_CMDI)


def unpatch() -> None:
    trace_utils.unwrap(os, "system")
    trace_utils.unwrap(os, "_spawnvef")
    trace_utils.unwrap(subprocess.Popen, "__init__")

    os._datadog_cmdi_patch = False  # type: ignore[attr-defined]
    subprocess._datadog_cmdi_patch = False  # type: ignore[attr-defined]


def _iast_cmdi_osspawn(wrapped, instance, args, kwargs):
    mode, file, func_args, _, _ = args
    _iast_report_cmdi(func_args)

    return wrapped(*args, **kwargs)


def _iast_cmdi_subprocess_init(wrapped, instance, args, kwargs):
    cmd_args = args[0] if len(args) else kwargs["args"]
    _iast_report_cmdi(cmd_args)

    return wrapped(*args, **kwargs)


@oce.register
class CommandInjection(VulnerabilityBase):
    vulnerability_type = VULN_CMDI


def _iast_report_cmdi(shell_args: Union[str, List[str]]) -> None:
    report_cmdi = ""
    from .._metrics import _set_metric_iast_executed_sink

    increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, CommandInjection.vulnerability_type)
    _set_metric_iast_executed_sink(CommandInjection.vulnerability_type)

    if AppSecIastSpanProcessor.is_span_analyzed() and CommandInjection.has_quota():
        from .._taint_tracking import is_pyobject_tainted
        from .._taint_tracking.aspects import join_aspect

        if isinstance(shell_args, (list, tuple)):
            for arg in shell_args:
                if is_pyobject_tainted(arg):
                    report_cmdi = join_aspect(" ".join, 1, " ", shell_args)
                    break
        elif is_pyobject_tainted(shell_args):
            report_cmdi = shell_args

        if report_cmdi:
            CommandInjection.report(evidence_value=report_cmdi)
