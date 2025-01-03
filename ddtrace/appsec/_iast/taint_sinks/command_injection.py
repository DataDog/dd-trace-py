import os
import subprocess  # nosec
from typing import List
from typing import Union

from ddtrace.appsec._common_module_patches import try_unwrap
from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._iast_request_context import is_iast_request_enabled
from ddtrace.appsec._iast._metrics import _set_metric_iast_executed_sink
from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
from ddtrace.appsec._iast._metrics import increment_iast_span_metric
from ddtrace.appsec._iast._patch import try_wrap_function_wrapper
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from ddtrace.appsec._iast.constants import VULN_CMDI
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config

from ._base import VulnerabilityBase


log = get_logger(__name__)


def get_version() -> str:
    return ""


def patch():
    if not asm_config._iast_enabled:
        return

    if not getattr(os, "_datadog_cmdi_patch", False):
        # all os.spawn* variants eventually use this one:
        try_wrap_function_wrapper("os", "_spawnvef", _iast_cmdi_osspawn)

    if not getattr(subprocess, "_datadog_cmdi_patch", False):
        try_wrap_function_wrapper("subprocess", "Popen.__init__", _iast_cmdi_subprocess_init)

        os._datadog_cmdi_patch = True
        subprocess._datadog_cmdi_patch = True

    _set_metric_iast_instrumented_sink(VULN_CMDI)


def unpatch() -> None:
    try_unwrap("os", "system")
    try_unwrap("os", "_spawnvef")
    try_unwrap("subprocess", "Popen.__init__")

    os._datadog_cmdi_patch = False  # type: ignore[attr-defined]
    subprocess._datadog_cmdi_patch = False  # type: ignore[attr-defined]


def _iast_cmdi_osspawn(wrapped, instance, args, kwargs):
    mode, file, func_args, _, _ = args
    _iast_report_cmdi(func_args)

    if hasattr(wrapped, "__func__"):
        return wrapped.__func__(instance, *args, **kwargs)
    return wrapped(*args, **kwargs)


def _iast_cmdi_subprocess_init(wrapped, instance, args, kwargs):
    cmd_args = args[0] if len(args) else kwargs["args"]
    _iast_report_cmdi(cmd_args)

    if hasattr(wrapped, "__func__"):
        return wrapped.__func__(instance, *args, **kwargs)
    return wrapped(*args, **kwargs)


@oce.register
class CommandInjection(VulnerabilityBase):
    vulnerability_type = VULN_CMDI


def _iast_report_cmdi(shell_args: Union[str, List[str]]) -> None:
    report_cmdi = ""

    increment_iast_span_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK, CommandInjection.vulnerability_type)
    _set_metric_iast_executed_sink(CommandInjection.vulnerability_type)

    if is_iast_request_enabled() and CommandInjection.has_quota():
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
