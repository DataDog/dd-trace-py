import sys
from typing import Any
from typing import Callable
from typing import Optional
from typing import cast

import aiohttp
import azure.durable_functions as durable_functions
from azure.durable_functions.models.DurableOrchestrationClient import DurableOrchestrationClient
from wrapt import wrap_function_wrapper as _w

from ddtrace import tracer
from ddtrace.contrib.internal.azure_functions._worker import patch_worker_context
from ddtrace.contrib.internal.azure_functions._worker import unpatch_worker_context
from ddtrace.contrib.internal.azure_functions.shared import patched_get_functions
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.propagation.http import _TraceContext


def _active_w3c_carrier() -> dict[str, str]:
    active_span = tracer.current_span()
    if active_span is None:
        return {}

    carrier: dict[str, str] = {}
    # AIDEV-NOTE: Durable Functions only accepts W3C propagation, even when
    # the application's general ddtrace injection styles exclude tracecontext.
    _TraceContext._inject(active_span.context, carrier)
    return carrier


def patched_get_current_activity_context(
    wrapped: Callable[..., tuple[Optional[str], Optional[str]]],
    instance: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[Optional[str], Optional[str]]:
    carrier = _active_w3c_carrier()
    if not carrier:
        return wrapped(*args, **kwargs)

    traceparent = carrier.get("traceparent")
    if traceparent is None:
        return wrapped(*args, **kwargs)
    return traceparent, carrier.get("tracestate")


async def patched_legacy_post_async_request(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> list[Any]:
    carrier = _active_w3c_carrier()
    if not carrier:
        return cast(list[Any], await wrapped(*args, **kwargs))

    url = kwargs.get("url", args[0] if args else None)
    data = kwargs.get("data", args[1] if len(args) > 1 else None)
    if not isinstance(url, str):
        return cast(list[Any], await wrapped(*args, **kwargs))

    # Durable SDK 1.2 predates its trace-context arguments. This mirrors that
    # version's small HTTP helper while supplying the W3C headers required by
    # the Durable host. Newer SDKs use patched_get_current_activity_context.
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=data, headers=carrier) as response:
            response_data = await response.json(content_type=None)
            return [response.status, response_data]


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

    try:
        from azure.durable_functions.decorators import durable_app  # noqa: F401
    except Exception:
        return

    durable_functions._datadog_patch = True
    _w("azure.durable_functions", "DFApp.get_functions", patched_get_functions)
    patch_worker_context()
    if hasattr(DurableOrchestrationClient, "_get_current_activity_context"):
        _w(
            "azure.durable_functions.models.DurableOrchestrationClient",
            "DurableOrchestrationClient._get_current_activity_context",
            patched_get_current_activity_context,
        )
    else:
        _w(
            "azure.durable_functions.models.DurableOrchestrationClient",
            "post_async_request",
            patched_legacy_post_async_request,
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
    else:
        durable_client_module = sys.modules.get("azure.durable_functions.models.DurableOrchestrationClient")
        if durable_client_module is not None:
            _u(durable_client_module, "post_async_request")
    unpatch_worker_context()
