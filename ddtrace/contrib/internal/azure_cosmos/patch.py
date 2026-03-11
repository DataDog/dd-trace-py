import azure.cosmos as azure_cosmos
import azure.cosmos.aio as azure_cosmos_aio
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import http
from ddtrace.ext import net
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils import get_argument_value
from ddtrace.trace import tracer


log = get_logger(__name__)

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
        Pin().onto(azure_cosmos_module)
        _w("azure.cosmos.aio", "_asynchronous_request.AsynchronousRequest", _patch_asynchronous_request)
    else:
        Pin().onto(azure_cosmos_module)
        _w("azure.cosmos", "_synchronized_request.SynchronizedRequest", _patched_synchronized_request)


def _patched_synchronized_request(wrapped, instance, args, kwargs):
    pin = Pin.get_from(azure_cosmos)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    client = get_argument_value(args, kwargs, 0, "client")
    request_params = get_argument_value(args, kwargs, 1, "request_params")
    request = get_argument_value(args, kwargs, 5, "request")
    request_data = get_argument_value(args, kwargs, 6, "request_data")

    if request_params.resource_type == "databaseaccount" and (request.url).find("/dbs") == -1:
        return wrapped(*args, **kwargs)

    with tracer.trace(
        "cosmosdb.query",
        service=trace_utils.ext_service(pin, config.azure_cosmos),
        span_type=SpanTypes.COSMOS,
    ) as span:
        _build_span_tags(span, client, request_params, request, request_data)

        try:
            result = wrapped(*args, **kwargs)
            (res, headers) = result

            sub_status = headers.get(azure_cosmos.http_constants.HttpHeaders.SubStatus)
            if sub_status:
                span._set_tag("cosmosdb.response.sub_status_code", sub_status)

            return result
        except azure_cosmos.exceptions.CosmosResourceExistsError as e:
            if e.sub_status:
                span._set_tag("cosmosdb.response.sub_status_code", e.sub_status)
            span.set_tag(http.STATUS_CODE, 409)
            raise e
        except azure_cosmos.exceptions.CosmosResourceNotFoundError as e:
            if e.sub_status:
                span._set_tag("cosmosdb.response.sub_status_code", e.sub_status)
            span.set_tag(http.STATUS_CODE, 404)
            raise e
        except azure_cosmos.exceptions.CosmosAccessConditionFailedError as e:
            if e.sub_status:
                span._set_tag("cosmosdb.response.sub_status_code", e.sub_status)
            span.set_tag(http.STATUS_CODE, 412)
            raise e


async def _patch_asynchronous_request(wrapped, instance, args, kwargs):
    pin = Pin.get_from(azure_cosmos_aio)
    if not pin or not pin.enabled():
        return await wrapped(*args, **kwargs)

    client = get_argument_value(args, kwargs, 0, "client")
    request_params = get_argument_value(args, kwargs, 1, "request_params")
    request = get_argument_value(args, kwargs, 5, "request")
    request_data = get_argument_value(args, kwargs, 6, "request_data")

    if request_params.resource_type == "databaseaccount" and (request.url).find("/dbs") == -1:
        return await wrapped(*args, **kwargs)

    with tracer.trace(
        "cosmosdb.query",
        service=trace_utils.ext_service(pin, config.azure_cosmos),
        span_type=SpanTypes.COSMOS,
    ) as span:
        _build_span_tags(span, client, request_params, request, request_data)

        try:
            result = await wrapped(*args, **kwargs)
            (res, headers) = result

            sub_status = headers.get(azure_cosmos.http_constants.HttpHeaders.SubStatus)
            if sub_status:
                span._set_tag("cosmosdb.response.sub_status_code", sub_status)

            return result
        except azure_cosmos.exceptions.CosmosResourceExistsError as e:
            if e.sub_status:
                span._set_tag("cosmosdb.response.sub_status_code", e.sub_status)
            span.set_tag(http.STATUS_CODE, 409)
            raise e
        except azure_cosmos.exceptions.CosmosResourceNotFoundError as e:
            if e.sub_status:
                span._set_tag("cosmosdb.response.sub_status_code", e.sub_status)
            span.set_tag(http.STATUS_CODE, 404)
            raise e
        except azure_cosmos.exceptions.CosmosAccessConditionFailedError as e:
            if e.sub_status:
                span._set_tag("cosmosdb.response.sub_status_code", e.sub_status)
            span.set_tag(http.STATUS_CODE, 412)
            raise e


def _build_span_tags(span, client, request_params, request, request_data):
    span._set_tag_str(SPAN_KIND, SpanKind.CLIENT)
    span._set_tag_str(db.SYSTEM, "cosmosdb")
    span._set_tag_str(COMPONENT, config.azure_cosmos.integration_name)
    span._set_tag_str(net.TARGET_HOST, client.url_connection)
    span._set_tag_str(http.USER_AGENT, client._user_agent)
    connection_mode = client.connection_policy.ConnectionMode
    if connection_mode == 0:
        span._set_tag_str("cosmosdb.connection.mode", "gateway")
    else:
        span._set_tag_str("cosmosdb.connection.mode", "other")

    idx = (request.url).find("/dbs")
    if idx != -1:
        resource_link = (request.url)[idx:]
    else:
        resource_link = request.url

    span.resource = request_params.operation_type + " " + resource_link
    if (
        request_params.operation_type == "Create"
        and request_params.resource_type == "database"
        and (request_data.get("id") is not None)
    ):
        span._set_tag_str(db.NAME, request_data["id"])

    if resource_link:
        if resource_link.startswith("/") and len(resource_link) > 1:
            resource_link = resource_link[1:]

        parts = resource_link.split("/")

        if parts and parts[0].lower() == "dbs" and len(parts) >= 2:
            span._set_tag_str(db.NAME, parts[1])
            if len(parts) >= 4:
                if parts[2].lower() == "colls":
                    span._set_tag_str("cosmosdb.container", parts[3])


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
