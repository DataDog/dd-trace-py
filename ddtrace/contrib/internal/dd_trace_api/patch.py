import os

import dd_trace_api
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.appsec._common_module_patches import wrapped_request_D8CB81E472AF98A2 as _wrap_request
from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
from ddtrace.appsec._iast.constants import VULN_SSRF
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.formats import asbool
from ddtrace.settings.asm import config as asm_config
from ddtrace.trace import Pin


def get_version() -> str:
    return getattr(dd_trace_api, "__version__", "")


def patch():
    if getattr(dd_trace_api, "__datadog_patch", False):
        return
    dd_trace_api.__datadog_patch = True


def unpatch():
    if not getattr(dd_trace_api, "__datadog_patch", False):
        return
    dd_trace_api.__datadog_patch = False
