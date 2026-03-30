from urllib import parse

import azure.cosmos as azure_cosmos
import azure.cosmos.aio as azure_cosmos_aio
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import http
from ddtrace.ext import net
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.trace import tracer


config._add(
    "azure_cosmos",
    {
        "distributed_tracing": True,
    },
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
    if not tracer or not tracer.enabled:
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

    with tracer.trace(
        "cosmosdb.query",
        service=None,
        span_type=SpanTypes.COSMOS,
    ) as span:
        _build_span_tags(span, client, request_params, request, request_data)

        try:
            result = wrapped(*args, **kwargs)
            _, headers = result

            sub_status = headers.get(azure_cosmos.http_constants.HttpHeaders.SubStatus)
            if sub_status:
                span._set_attribute("cosmosdb.response.sub_status_code", sub_status)

            return result
        except Exception as e:
            _tag_cosmos_exceptions(e, span)
            raise e


async def _patch_asynchronous_request(wrapped, instance, args, kwargs):
    if not tracer or not tracer.enabled:
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

    with tracer.trace(
        "cosmosdb.query",
        service=None,
        span_type=SpanTypes.COSMOS,
    ) as span:
        _build_span_tags(span, client, request_params, request, request_data)

        try:
            result = await wrapped(*args, **kwargs)
            _, headers = result

            sub_status = headers.get(azure_cosmos.http_constants.HttpHeaders.SubStatus)
            if sub_status:
                span._set_attribute("cosmosdb.response.sub_status_code", sub_status)

            return result
        except Exception as e:
            _tag_cosmos_exceptions(e, span)
            raise e


def _build_span_tags(span, client, request_params, request, request_data):
    span._set_attribute(SPAN_KIND, SpanKind.CLIENT)
    span._set_attribute(db.SYSTEM, "cosmosdb")
    span._set_attribute(COMPONENT, config.azure_cosmos.integration_name)
    span._set_attribute(net.TARGET_HOST, client.url_connection)
    span._set_attribute(http.USER_AGENT, client._user_agent)
    connection_mode = client.connection_policy.ConnectionMode
    if connection_mode == 0:
        span._set_attribute("cosmosdb.connection.mode", "gateway")
    elif connection_mode == 1:
        span._set_attribute("cosmosdb.connection.mode", "direct")
    else:
        span._set_attribute("cosmosdb.connection.mode", "other")

    resource_link = request.url
    parsed = parse.urlsplit(resource_link)
    if parsed.path:
        resource_link = parsed.path

    span.resource = request_params.operation_type + " " + resource_link
    if (
        request_params.operation_type == "Create"
        and request_params.resource_type == "dbs"
        and (request_data.get("id") is not None)
    ):
        span._set_attribute(db.NAME, request_data["id"])

    if resource_link:
        if resource_link.startswith("/") and len(resource_link) > 1:
            resource_link = resource_link[1:]

        parts = resource_link.split("/")

        if parts and parts[0].lower() == "dbs" and len(parts) >= 2:
            span._set_attribute(db.NAME, parts[1])
            if len(parts) >= 4:
                if parts[2].lower() == "colls" and parts[3].lower() != "":
                    span._set_attribute("cosmosdb.container", parts[3])


def _tag_cosmos_exceptions(e, span):
    sub_status = getattr(e, "sub_status", None)
    if sub_status:
        span._set_attribute("cosmosdb.response.sub_status_code", sub_status)
    if isinstance(e, azure_cosmos.exceptions.CosmosResourceExistsError):
        span._set_attribute(http.STATUS_CODE, 409)
    elif isinstance(e, azure_cosmos.exceptions.CosmosResourceNotFoundError):
        span._set_attribute(http.STATUS_CODE, 404)
    elif isinstance(e, azure_cosmos.exceptions.CosmosAccessConditionFailedError):
        span._set_attribute(http.STATUS_CODE, 412)
    elif isinstance(e, azure_cosmos.exceptions.CosmosHttpResponseError):
        if e.status_code:
            span._set_attribute(http.STATUS_CODE, e.status_code)


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
