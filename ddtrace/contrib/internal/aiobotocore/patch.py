import os
from typing import Dict

import aiobotocore.client
import wrapt

from ddtrace import config
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import aws
from ddtrace.ext import http
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_cloud_api_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.formats import deep_getattr
from ddtrace.internal.utils.version import parse_version
from ddtrace.trace import Pin


aiobotocore_version_str = getattr(aiobotocore, "__version__", "")
AIOBOTOCORE_VERSION = parse_version(aiobotocore_version_str)

if AIOBOTOCORE_VERSION <= (0, 10, 0):
    # aiobotocore>=0.11.0
    from aiobotocore.endpoint import ClientResponseContentProxy
elif AIOBOTOCORE_VERSION >= (0, 11, 0) and AIOBOTOCORE_VERSION < (2, 3, 0):
    from aiobotocore._endpoint_helpers import ClientResponseContentProxy


ARGS_NAME = ("action", "params", "path", "verb")
TRACED_ARGS = {"params", "path", "verb"}


config._add(
    "aiobotocore",
    {
        "tag_no_params": asbool(os.getenv("DD_AWS_TAG_NO_PARAMS", default=False)),
    },
)


def get_version():
    # type: () -> str
    return aiobotocore_version_str


def _supported_versions() -> Dict[str, str]:
    return {"aiobotocore": ">=1.0.0"}


def patch():
    if getattr(aiobotocore.client, "_datadog_patch", False):
        return
    aiobotocore.client._datadog_patch = True

    wrapt.wrap_function_wrapper("aiobotocore.client", "AioBaseClient._make_api_call", _wrapped_api_call)
    Pin().onto(aiobotocore.client.AioBaseClient)


def unpatch():
    if getattr(aiobotocore.client, "_datadog_patch", False):
        aiobotocore.client._datadog_patch = False
        unwrap(aiobotocore.client.AioBaseClient, "_make_api_call")


class WrappedClientResponseContentProxy(wrapt.ObjectProxy):
    def __init__(self, body, pin, parent_span):
        super(WrappedClientResponseContentProxy, self).__init__(body)
        self._self_pin = pin
        self._self_parent_span = parent_span

    async def read(self, *args, **kwargs):
        # async read that must be child of the parent span operation
        operation_name = "{}.read".format(self._self_parent_span.name)

        with self._self_pin.tracer.start_span(operation_name, child_of=self._self_parent_span) as span:
            span.set_tag_str(COMPONENT, config.aiobotocore.integration_name)

            # set span.kind tag equal to type of request
            span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)

            # inherit parent attributes
            span.resource = self._self_parent_span.resource
            span.span_type = self._self_parent_span.span_type
            span._meta = dict(self._self_parent_span._meta)
            span._metrics = dict(self._self_parent_span.metrics)

            result = await self.__wrapped__.read(*args, **kwargs)
            span.set_tag("Length", len(result))

        return result

    # wrapt doesn't proxy `async with` context managers
    async def __aenter__(self):
        # call the wrapped method but return the object proxy
        await self.__wrapped__.__aenter__()
        return self

    async def __aexit__(self, *args, **kwargs):
        response = await self.__wrapped__.__aexit__(*args, **kwargs)
        return response


async def _wrapped_api_call(original_func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        result = await original_func(*args, **kwargs)
        return result

    endpoint_name = deep_getattr(instance, "_endpoint._endpoint_prefix")

    fallback_service = config._get_service(default="aws.{}".format(endpoint_name))
    with pin.tracer.trace(
        schematize_cloud_api_operation(
            "{}.command".format(endpoint_name), cloud_provider="aws", cloud_service=endpoint_name
        ),
        service=ext_service(pin, config.aiobotocore, default=schematize_service_name(fallback_service)),
        span_type=SpanTypes.HTTP,
    ) as span:
        span.set_tag_str(COMPONENT, config.aiobotocore.integration_name)

        # set span.kind tag equal to type of request
        span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)

        span.set_tag(_SPAN_MEASURED_KEY)

        try:
            operation = get_argument_value(args, kwargs, 0, "operation_name")
            params = get_argument_value(args, kwargs, 1, "api_params")

            span.resource = "{}.{}".format(endpoint_name, operation.lower())

            if params and not config.aiobotocore["tag_no_params"]:
                aws._add_api_param_span_tags(span, endpoint_name, params)
        except ArgumentError:
            operation = None
            span.resource = endpoint_name

        region_name = deep_getattr(instance, "meta.region_name")

        meta = {
            "aws.agent": "aiobotocore",
            "aws.operation": operation,
            "aws.region": region_name,
            "region": region_name,
        }
        span.set_tags(meta)

        result = await original_func(*args, **kwargs)

        body = result.get("Body")

        # ClientResponseContentProxy removed in aiobotocore 2.3.x: https://github.com/aio-libs/aiobotocore/pull/934/
        if hasattr(body, "ClientResponseContentProxy") and isinstance(body, ClientResponseContentProxy):
            result["Body"] = WrappedClientResponseContentProxy(body, pin, span)

        response_meta = result["ResponseMetadata"]
        response_headers = response_meta["HTTPHeaders"]

        span.set_tag(http.STATUS_CODE, response_meta["HTTPStatusCode"])
        if 500 <= response_meta["HTTPStatusCode"] < 600:
            span.error = 1

        span.set_tag("retry_attempts", response_meta["RetryAttempts"])

        request_id = response_meta.get("RequestId")
        if request_id:
            span.set_tag_str("aws.requestid", request_id)

        request_id2 = response_headers.get("x-amz-id-2")
        if request_id2:
            span.set_tag_str("aws.requestid2", request_id2)

        return result
