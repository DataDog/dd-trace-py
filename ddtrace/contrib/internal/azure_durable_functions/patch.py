from typing import Any
from typing import Callable
from typing import Optional

import azure.durable_functions as durable_functions
from azure.durable_functions.models.DurableOrchestrationClient import DurableOrchestrationClient
from wrapt import wrap_function_wrapper as _w

from ddtrace import tracer
from ddtrace.contrib.internal.azure_functions.shared import patched_get_functions
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.propagation.http import _TraceContext


def patched_get_current_activity_context(
    wrapped: Callable[..., tuple[Optional[str], Optional[str]]],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[Optional[str], Optional[str]]:
    active_span = tracer.current_span()
    if active_span is None:
        return wrapped(*args, **kwargs)

    carrier: dict[str, str] = {}
    HTTPPropagator.inject(active_span, carrier)
    traceparent = carrier.get("traceparent")
    if traceparent is None:
        # AIDEV-NOTE: Durable Functions only accepts W3C propagation, even when
        # the application's general ddtrace injection styles exclude tracecontext.
        _TraceContext._inject(active_span.context, carrier)
        traceparent = carrier.get("traceparent")
        if traceparent is None:
            return wrapped(*args, **kwargs)
    return traceparent, carrier.get("tracestate")


def get_version() -> str:
    from importlib.metadata import version

    return version("azure-functions-durable")


def _supported_versions() -> dict[str, str]:
    return {"azure.durable_functions": ">=1.2.1"}


def patch():
    """
    Patch `azure.durable_functions` module for tracing.
    """
    if getattr(durable_functions, "_datadog_patch", False):
        return
    durable_functions._datadog_patch = True

    try:
        from azure.durable_functions.decorators import durable_app  # noqa: F401
    except Exception:
        return

    _w("azure.durable_functions", "DFApp.get_functions", patched_get_functions)
    if hasattr(DurableOrchestrationClient, "_get_current_activity_context"):
        _w(
            "azure.durable_functions.models.DurableOrchestrationClient",
            "DurableOrchestrationClient._get_current_activity_context",
            patched_get_current_activity_context,
        )


def unpatch():
    if not getattr(durable_functions, "_datadog_patch", False):
        return
    durable_functions._datadog_patch = False

    try:
        from azure.durable_functions.decorators import durable_app
    except Exception:
        durable_app = None
    if durable_app is not None:
        _u(durable_app.DFApp, "get_functions")

    if hasattr(DurableOrchestrationClient, "_get_current_activity_context"):
        _u(DurableOrchestrationClient, "_get_current_activity_context")
