import azure.cosmos as azure_cosmos
import azure.cosmos.aio as azure_cosmos_aio
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.internal.schema import schematize_service_name
from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.trace_utils import unwrap as _u


config._add(
    "azure_cosmos",
    {
        "distributed_tracing": True,
        "_default_service": schematize_service_name("azure_cosmos"),
    },
)


def _supported_versions() -> dict[str, str]:
    return {"azure.cosmos": ">=4.14.5"}


def get_version():
    return getattr(azure_cosmos, "__version__", "")


def patch():
    for azure_cosmos_module in (azure_cosmos, azure_cosmos_aio):
        _patch(azure_cosmos_module)

def _patch(azure_cosmos_module):
    if getattr(azure_cosmos_module, "_datadog_patch", False):
        return
    azure_cosmos_module._datadog_patch = True

    if azure_cosmos_module.__name__ == "azure.cosmos.aio":
        Pin().onto(azure_cosmos_module)
        _w("azure.cosmos.aio", "_asynchronous_request.AsynchronousRequest", _patch_asynchrous_request)
    else:
        Pin().onto(azure_cosmos_module)
        _w("azure.cosmos", "_synchronized_request.SynchronizedRequest", _patched_synchronized_request)

def _patched_synchronized_request(wrapped, instance, args, kwargs):
    pin = Pin.get_from(azure_cosmos)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)
    
    
    return wrapped(*args, **kwargs)

async def _patch_asynchrous_request(wrapped, instance, args, kwargs):
    pin = Pin.get_from(azure_cosmos_aio)
    if not pin or not pin.enabled():
        return await wrapped(*args, **kwargs)
    
    return await wrapped(*args, **kwargs)


def unpatch():
    for azure_cosmos_module in (azure_cosmos, azure_cosmos_aio):
        _unpatch(azure_cosmos_module)

def _unpatch(azure_cosmos_module):
    if not getattr(azure_cosmos_module, "_datadog_patch", False):
        return
    azure_cosmos_module._datadog_patch = False

    if azure_cosmos_module.__name__ == "azure.cosmos.aio":
        _u("azure.cosmos.aio", "_asynchronous_request.AsynchronousRequest")
    else:
        _u("azure.cosmos", "_synchronized_request.SynchronizedRequest")

