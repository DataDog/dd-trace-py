"""
Trace queries to aws api done via aiobotocore client
"""

# project
import asyncio
import sys
from ddtrace import Pin
from ddtrace.util import deep_getattr, unwrap

# 3p
import wrapt
import aiobotocore.client
from aiobotocore.endpoint import ClientResponseContentProxy

from ...ext import http
from ...ext import aws

PY_VER = sys.version_info


# Original botocore client class
_Botocore_client = aiobotocore.client.AioBaseClient

SPAN_TYPE = "http"
ARGS_NAME = ("action", "params", "path", "verb")
TRACED_ARGS = ["params", "path", "verb"]


def patch(tracer=None):
    if getattr(aiobotocore.client, '_datadog_patch', False):
        return
    setattr(aiobotocore.client, '_datadog_patch', True)

    wrapt.wrap_function_wrapper('aiobotocore.client', 'AioBaseClient._make_api_call', patched_api_call)
    Pin(service="aws", app="aiobotocore", app_type="web", tracer=tracer).onto(aiobotocore.client.AioBaseClient)


def unpatch():
    if getattr(aiobotocore.client, '_datadog_patch', False):
        setattr(aiobotocore.client, '_datadog_patch', False)
        unwrap(aiobotocore.client.AioBaseClient, '_make_api_call')


class WrappedClientResponseContentProxy(wrapt.ObjectProxy):
    def __init__(self, wrapped, pin, parent_span):
        super(WrappedClientResponseContentProxy, self).__init__(wrapped)
        self.__pin = pin
        self.__parent_span = parent_span

    @asyncio.coroutine
    def read(self, *args, **kwargs):
        with self.__pin.tracer.trace('{}.read'.format(
                self.__parent_span.name),
                resource=self.__parent_span.resource,
                service=self.__parent_span.service,
                span_type=self.__parent_span.span_type) as span:
            span.trace_id = self.__parent_span.trace_id
            span.parent_id = self.__parent_span.span_id
            span.meta = dict(self.__parent_span.meta)
            result = yield from self.__wrapped__.read(*args, **kwargs)  # noqa: E999
            span.set_tag('Length', len(result))

        return result

    if PY_VER >= (3, 5, 0):
        @asyncio.coroutine
        def __aenter__(self):
            result = yield from self.__wrapped__.__aenter__()  # noqa: E999
            assert result == self.__wrapped__
            return self

        @asyncio.coroutine
        def __aexit__(self, *args, **kwargs):
            result = yield from self.__wrapped__.__aexit__(*args, **kwargs)  # noqa: E999
            return result


def truncate_arg_value(value, max_len=1024):
    """ Method will truncate values which are bytes and greater than `max_len`.
    Useful for parameters like 'Body' in `put_object` operations. """
    if isinstance(value, bytes) and len(value) > max_len:
        return b'...'

    return value


@asyncio.coroutine
def patched_api_call(original_func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        result = yield from original_func(*args, **kwargs)  # noqa: E999
        return result

    endpoint_name = deep_getattr(instance, "_endpoint._endpoint_prefix")

    with pin.tracer.trace('{}.command'.format(endpoint_name),
                          service="{}.{}".format(pin.service, endpoint_name),
                          span_type=SPAN_TYPE) as span:

        operation = None
        if args:
            operation = args[0]
            span.resource = '%s.%s' % (endpoint_name, operation.lower())

        else:
            span.resource = endpoint_name

        # Adding the args in TRACED_ARGS if exist to the span
        if not aws.is_blacklist(endpoint_name):
            for name, value in aws.unpacking_args(args, ARGS_NAME, TRACED_ARGS):
                if name == 'params':
                    value = {k: truncate_arg_value(v) for k, v in value.items()}
                span.set_tag(name, (value))

        region_name = deep_getattr(instance, "meta.region_name")

        meta = {
            'aws.agent': 'aiobotocore',
            'aws.operation': operation,
            'aws.region': region_name,
        }
        span.set_tags(meta)

        result = yield from original_func(*args, **kwargs)  # noqa: E999

        body = result.get('Body')
        if isinstance(body, ClientResponseContentProxy):
            result['Body'] = WrappedClientResponseContentProxy(body, pin, span)

        response_meta = result['ResponseMetadata']
        response_headers = response_meta['HTTPHeaders']

        span.set_tag(http.STATUS_CODE, response_meta['HTTPStatusCode'])
        span.set_tag("retry_attempts", response_meta['RetryAttempts'])

        request_id = response_meta.get('RequestId')
        if request_id:
            span.set_tag("aws.requestid", request_id)

        request_id2 = response_headers.get('x-amz-id-2')
        if request_id2:
            span.set_tag("aws.requestid2", request_id2)

        return result
