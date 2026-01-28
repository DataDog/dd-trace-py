import azure.cosmos as azure_cosmos
import azure.cosmos.aio as azure_cosmos_aio
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.internal.schema import schematize_service_name
from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.ext import db
from ddtrace.ext import net
from ddtrace.ext import http
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.constants import COMPONENT

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
    
    with pin.tracer.trace(
        "cosmosdb.query",
        service=trace_utils.ext_service(pin, config.azure_cosmos),
        span_type=SpanTypes.SQL,
    ) as span:
        #span kind
        span._set_tag_str(SPAN_KIND, SpanKind.CLIENT)
        #replacement for db.type, is db.system
        span._set_tag_str(db.SYSTEM, "cosmosdb")
        #instrumentation name/component
        span._set_tag_str(COMPONENT, config.azure_cosmos.integration_name)

        client = get_argument_value(args, kwargs, 0, "client")
        request_params = get_argument_value(args, kwargs, 1, "request_params")

        #out.host
        span._set_tag_str(net.TARGET_HOST, client.url_connection)
        #http.useragent
        span._set_tag_str(http.USER_AGENT, client._user_agent)
        #connection mode
        connection_mode = client.connection_policy.ConnectionMode
        if connection_mode == 0:
            span._set_tag_str("cosmosdb.connection.mode", "gateway")
        else:
            span._set_tag_str("cosmosdb.connection.mode", "other")
    
        #resource name
        span.resource = request_params.operation_type + 

        result = wrapped(*args, **kwargs)
        (res, headers) = result

        #db.name (database id)
        span._set_tag_str(db.NAME)
        #container id
        span._set_tag_str("cosmosdb.container")
        #equivalent of db.response.status_code
        span._set_tag_str(http.STATUS_CODE, result["statusCode"])
        #self-explanatory
        span._set_tag_str("cosmosdb.response.sub_status_code", headers[azure_cosmos.http_constants.HttpHeaders.SubStatus])

        return result

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

