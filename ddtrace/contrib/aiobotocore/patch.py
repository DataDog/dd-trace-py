import asyncio
import wrapt
import aiobotocore.client

from aiobotocore.endpoint import ClientResponseContentProxy

from ...pin import Pin
from ...ext import http, aws
from ...compat import PYTHON_VERSION_INFO
from ...utils.formats import deep_getattr
from ...utils.wrappers import unwrap


ARGS_NAME = ('action', 'params', 'path', 'verb')
TRACED_ARGS = ['params', 'path', 'verb']


def patch():
    if getattr(aiobotocore.client, '_datadog_patch', False):
        return
    setattr(aiobotocore.client, '_datadog_patch', True)

    wrapt.wrap_function_wrapper('aiobotocore.client', 'AioBaseClient._make_api_call', _wrapped_api_call)
    Pin(service='aws', app='aws', app_type='web').onto(aiobotocore.client.AioBaseClient)


def unpatch():
    if getattr(aiobotocore.client, '_datadog_patch', False):
        setattr(aiobotocore.client, '_datadog_patch', False)
        unwrap(aiobotocore.client.AioBaseClient, '_make_api_call')


class WrappedClientResponseContentProxy(wrapt.ObjectProxy):
    def __init__(self, body, pin, parent_span):
        super(WrappedClientResponseContentProxy, self).__init__(body)
        self._self_pin = pin
        self._self_parent_span = parent_span

    @asyncio.coroutine
    def read(self, *args, **kwargs):
        # async read that must be child of the parent span operation
        operation_name = '{}.read'.format(self._self_parent_span.name)

        with self._self_pin.tracer.start_span(operation_name, child_of=self._self_parent_span) as span:
            # inherit parent attributes
            span.resource = self._self_parent_span.resource
            span.span_type = self._self_parent_span.span_type
            span.meta = dict(self._self_parent_span.meta)

            result = yield from self.__wrapped__.read(*args, **kwargs)  # noqa: E999
            span.set_tag('Length', len(result))

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


def truncate_arg_value(value, max_len=1024):
    """Truncate values which are bytes and greater than `max_len`.
    Useful for parameters like 'Body' in `put_object` operations.
    """
    if isinstance(value, bytes) and len(value) > max_len:
        return b'...'

    return value


@asyncio.coroutine
def _wrapped_api_call(original_func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        result = yield from original_func(*args, **kwargs)  # noqa: E999
        return result

    endpoint_name = deep_getattr(instance, '_endpoint._endpoint_prefix')

    with pin.tracer.trace('{}.command'.format(endpoint_name),
                          service='{}.{}'.format(pin.service, endpoint_name),
                          span_type=http.TYPE) as span:

        if len(args) > 0:
            operation = args[0]
            span.resource = '{}.{}'.format(endpoint_name, operation.lower())
        else:
            operation = None
            span.resource = endpoint_name

        # add args in TRACED_ARGS if exist to the span
        if not aws.is_blacklist(endpoint_name):
            for name, value in aws.unpacking_args(args, ARGS_NAME, TRACED_ARGS):
                if name == 'params':
                    value = {k: truncate_arg_value(v) for k, v in value.items()}
                span.set_tag(name, (value))

        region_name = deep_getattr(instance, 'meta.region_name')

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
        span.set_tag('retry_attempts', response_meta['RetryAttempts'])

        request_id = response_meta.get('RequestId')
        if request_id:
            span.set_tag('aws.requestid', request_id)

        request_id2 = response_headers.get('x-amz-id-2')
        if request_id2:
            span.set_tag('aws.requestid2', request_id2)

        return result
