import aiobotocore.client

from ddtrace import config
from ddtrace.vendor import wrapt


try:
    from aiobotocore.endpoint import ClientResponseContentProxy
except ImportError:
    # aiobotocore>=0.11.0
    from aiobotocore._endpoint_helpers import ClientResponseContentProxy

from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanTypes
from ...ext import aws
from ...ext import http
from ...internal.compat import PYTHON_VERSION_INFO
from ...internal.utils import ArgumentError
from ...internal.utils import get_argument_value
from ...internal.utils.formats import deep_getattr
from ...pin import Pin
from ..trace_utils import unwrap


ARGS_NAME = ("action", "params", "path", "verb")
TRACED_ARGS = {"params", "path", "verb"}


def patch():
    if getattr(aiobotocore.client, "_datadog_patch", False):
        return
    setattr(aiobotocore.client, "_datadog_patch", True)

    wrapt.wrap_function_wrapper("aiobotocore.client", "AioBaseClient._make_api_call", _wrapped_api_call)
    Pin(service=config.service or "aws").onto(aiobotocore.client.AioBaseClient)


def unpatch():
    if getattr(aiobotocore.client, "_datadog_patch", False):
        setattr(aiobotocore.client, "_datadog_patch", False)
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
            # inherit parent attributes
            span.resource = self._self_parent_span.resource
            span.span_type = self._self_parent_span.span_type
            span._meta = dict(self._self_parent_span._meta)
            span._metrics = dict(self._self_parent_span.metrics)

            result = await self.__wrapped__.read(*args, **kwargs)
            span.set_tag("Length", len(result))

        return result

    # wrapt doesn't proxy `async with` context managers
    if PYTHON_VERSION_INFO >= (3, 5, 0):

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

    service = pin.service if pin.service != "aws" else "{}.{}".format(pin.service, endpoint_name)
    with pin.tracer.trace("{}.command".format(endpoint_name), service=service, span_type=SpanTypes.HTTP) as span:
        span.set_tag(SPAN_MEASURED_KEY)

        try:
            operation = get_argument_value(args, kwargs, 0, "operation_name")
            span.resource = "{}.{}".format(endpoint_name, operation.lower())
        except ArgumentError:
            operation = None
            span.resource = endpoint_name

        aws.add_span_arg_tags(span, endpoint_name, args, ARGS_NAME, TRACED_ARGS)

        region_name = deep_getattr(instance, "meta.region_name")

        meta = {
            "aws.agent": "aiobotocore",
            "aws.operation": operation,
            "aws.region": region_name,
        }
        span.set_tags(meta)

        result = await original_func(*args, **kwargs)

        body = result.get("Body")
        if isinstance(body, ClientResponseContentProxy):
            result["Body"] = WrappedClientResponseContentProxy(body, pin, span)

        response_meta = result["ResponseMetadata"]
        response_headers = response_meta["HTTPHeaders"]

        span.set_tag(http.STATUS_CODE, response_meta["HTTPStatusCode"])
        if 500 <= response_meta["HTTPStatusCode"] < 600:
            span.error = 1

        span.set_tag("retry_attempts", response_meta["RetryAttempts"])

        request_id = response_meta.get("RequestId")
        if request_id:
            span.set_tag("aws.requestid", request_id)

        request_id2 = response_headers.get("x-amz-id-2")
        if request_id2:
            span.set_tag("aws.requestid2", request_id2)

        # set analytics sample rate
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.aiobotocore.get_analytics_sample_rate())

        return result
