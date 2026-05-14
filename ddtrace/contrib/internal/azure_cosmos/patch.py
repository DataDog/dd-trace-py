import azure.cosmos as azure_cosmos
import azure.cosmos.aio as azure_cosmos_aio
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import is_tracing_enabled
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value


config._add(
    "azure_cosmos",
    dict(),
)


def _supported_versions() -> dict[str, str]:
    return {"azure.cosmos": ">=4.9.0"}


def get_version():
    # get the package distribution version here
    return getattr(azure_cosmos, "__version__", "")


def patch():
    for azure_cosmos_module in (azure_cosmos, azure_cosmos_aio):
        _patch(azure_cosmos_module)


def _patch(azure_cosmos_module):
    if getattr(azure_cosmos_module, "_datadog_patch", False):
        return
    azure_cosmos_module._datadog_patch = True

    if azure_cosmos_module.__name__ == "azure.cosmos.aio":
        _w("azure.cosmos.aio", "_asynchronous_request.AsynchronousRequest", _patch_asynchronous_request)
    else:
        _w("azure.cosmos", "_synchronized_request.SynchronizedRequest", _patched_synchronized_request)


def _patched_synchronized_request(wrapped, instance, args, kwargs):
    if not is_tracing_enabled():
        return wrapped(*args, **kwargs)

    try:
        client = get_argument_value(args, kwargs, 0, "client")
        request_params = get_argument_value(args, kwargs, 1, "request_params")
        request = get_argument_value(args, kwargs, 5, "request")
        request_data = get_argument_value(args, kwargs, 6, "request_data")
    except ArgumentError:
        return wrapped(*args, **kwargs)

    if request_params.resource_type == "databaseaccount":
        return wrapped(*args, **kwargs)

    with core.context_with_data(
        "azure_cosmos.request",
        span_name="cosmosdb.query",
        span_type=SpanTypes.COSMOS,
        client=client,
        request_params=request_params,
        request=request,
        request_data=request_data,
    ) as ctx:
        result = wrapped(*args, **kwargs)
        _, headers = result
        ctx.set_item("sub_status_code", headers.get(azure_cosmos.http_constants.HttpHeaders.SubStatus))

        return result


async def _patch_asynchronous_request(wrapped, instance, args, kwargs):
    if not is_tracing_enabled():
        return await wrapped(*args, **kwargs)

    try:
        client = get_argument_value(args, kwargs, 0, "client")
        request_params = get_argument_value(args, kwargs, 1, "request_params")
        request = get_argument_value(args, kwargs, 5, "request")
        request_data = get_argument_value(args, kwargs, 6, "request_data")
    except ArgumentError:
        return await wrapped(*args, **kwargs)

    if request_params.resource_type == "databaseaccount":
        return await wrapped(*args, **kwargs)

    with core.context_with_data(
        "azure_cosmos.request",
        span_name="cosmosdb.query",
        span_type=SpanTypes.COSMOS,
        client=client,
        request_params=request_params,
        request=request,
        request_data=request_data,
    ) as ctx:
        result = await wrapped(*args, **kwargs)
        _, headers = result
        ctx.set_item("sub_status_code", headers.get(azure_cosmos.http_constants.HttpHeaders.SubStatus))

        return result


def unpatch():
    for azure_cosmos_module in (azure_cosmos, azure_cosmos_aio):
        _unpatch(azure_cosmos_module)


def _unpatch(azure_cosmos_module):
    if not getattr(azure_cosmos_module, "_datadog_patch", False):
        return
    azure_cosmos_module._datadog_patch = False

    if azure_cosmos_module.__name__ == "azure.cosmos.aio":
        _u(azure_cosmos_module._asynchronous_request, "AsynchronousRequest")
    else:
        _u(azure_cosmos_module._synchronized_request, "SynchronizedRequest")
