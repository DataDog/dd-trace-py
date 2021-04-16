import asyncio
import base64
import json

import aiobotocore.client

from ddtrace import config
from ddtrace.vendor import wrapt


try:
    from aiobotocore.endpoint import ClientResponseContentProxy
except ImportError:
    # aiobotocore>=0.11.0
    from aiobotocore._endpoint_helpers import ClientResponseContentProxy

from ...compat import PYTHON_VERSION_INFO
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanTypes
from ...ext import aws
from ...ext import http
from ...internal.logger import get_logger
from ...pin import Pin
from ...propagation.http import HTTPPropagator
from ...utils.formats import deep_getattr
from ...utils.formats import get_env
from ...utils.wrappers import unwrap


ARGS_NAME = ("action", "params", "path", "verb")
TRACED_ARGS = {"params", "path", "verb"}


log = get_logger(__name__)


# aiobotocore default settings
config._add(
    "aiobotocore",
    {
        "distributed_tracing": get_env("aiobotocore", "distributed_tracing", default=True),
    },
)


def patch():
    if getattr(aiobotocore.client, "_datadog_patch", False):
        return
    setattr(aiobotocore.client, "_datadog_patch", True)

    wrapt.wrap_function_wrapper("aiobotocore.client", "AioBaseClient._make_api_call", _wrapped_api_call)
    Pin(service=config.service or "aws", app="aws").onto(aiobotocore.client.AioBaseClient)


def unpatch():
    if getattr(aiobotocore.client, "_datadog_patch", False):
        setattr(aiobotocore.client, "_datadog_patch", False)
        unwrap(aiobotocore.client.AioBaseClient, "_make_api_call")


class WrappedClientResponseContentProxy(wrapt.ObjectProxy):
    def __init__(self, body, pin, parent_span):
        super(WrappedClientResponseContentProxy, self).__init__(body)
        self._self_pin = pin
        self._self_parent_span = parent_span

    @asyncio.coroutine
    def read(self, *args, **kwargs):
        # async read that must be child of the parent span operation
        operation_name = "{}.read".format(self._self_parent_span.name)

        with self._self_pin.tracer.start_span(operation_name, child_of=self._self_parent_span) as span:
            # inherit parent attributes
            span.resource = self._self_parent_span.resource
            span.span_type = self._self_parent_span.span_type
            span.meta = dict(self._self_parent_span.meta)
            span.metrics = dict(self._self_parent_span.metrics)

            result = yield from self.__wrapped__.read(*args, **kwargs)
            span.set_tag("Length", len(result))

        return result

    # wrapt doesn't proxy `async with` context managers
    if PYTHON_VERSION_INFO >= (3, 5, 0):

        @asyncio.coroutine
        def __aenter__(self):
            # call the wrapped method but return the object proxy
            yield from self.__wrapped__.__aenter__()
            return self

        @asyncio.coroutine
        def __aexit__(self, *args, **kwargs):
            response = yield from self.__wrapped__.__aexit__(*args, **kwargs)
            return response


@asyncio.coroutine
def _wrapped_api_call(original_func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        result = yield from original_func(*args, **kwargs)
        return result

    endpoint_name = deep_getattr(instance, "_endpoint._endpoint_prefix")

    service = pin.service if pin.service != "aws" else "{}.{}".format(pin.service, endpoint_name)
    with pin.tracer.trace("{}.command".format(endpoint_name), service=service, span_type=SpanTypes.HTTP) as span:
        span.set_tag(SPAN_MEASURED_KEY)

        if len(args) > 0:
            operation = args[0]
            span.resource = "{}.{}".format(endpoint_name, operation.lower())

            if config.aiobotocore["distributed_tracing"]:
                if endpoint_name == "lambda" and operation == "Invoke":
                    inject_trace_to_client_context(args, span)
        else:
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

        result = yield from original_func(*args, **kwargs)

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


def modify_client_context(client_context_base64, trace_headers):
    try:
        client_context_json = base64.b64decode(client_context_base64).decode("utf-8")
        client_context_object = json.loads(client_context_json)

        if "custom" in client_context_object:
            client_context_object["custom"]["_datadog"] = trace_headers
        else:
            client_context_object["custom"] = {"_datadog": trace_headers}

        new_context = base64.b64encode(json.dumps(client_context_object).encode("utf-8")).decode("utf-8")
        return new_context
    except Exception:
        log.warning("malformed client_context=%s", client_context_base64, exc_info=True)
        return client_context_base64


def inject_trace_to_client_context(args, span):
    trace_headers = {}
    HTTPPropagator.inject(span.context, trace_headers)

    params = args[1]
    if "ClientContext" in params:
        params["ClientContext"] = modify_client_context(params["ClientContext"], trace_headers)
    else:
        client_context_object = {"custom": {"_datadog": trace_headers}}
        json_context = json.dumps(client_context_object).encode("utf-8")
        params["ClientContext"] = base64.b64encode(json_context).decode("utf-8")
