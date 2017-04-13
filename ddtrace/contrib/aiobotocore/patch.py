"""
Trace queries to aws api done via aiobotocore client
"""

# project
import asyncio
from ddtrace import Pin
from ddtrace.util import deep_getattr, unwrap

# 3p
import wrapt
import aiobotocore.client

from ...ext import http
from ...ext import aws


# Original botocore client class
_Botocore_client = aiobotocore.client.AioBaseClient

SPAN_TYPE = "http"
ARGS_NAME = ("action", "params", "path", "verb")
TRACED_ARGS = ["params", "path", "verb"]


def patch():
    if getattr(aiobotocore.client, '_datadog_patch', False):
        return
    setattr(aiobotocore.client, '_datadog_patch', True)

    wrapt.wrap_function_wrapper('aiobotocore.client', 'AioBaseClient._make_api_call', patched_api_call)
    Pin(service="aws", app="aiobotocore", app_type="web").onto(aiobotocore.client.AioBaseClient)


def unpatch():
    if getattr(aiobotocore.client, '_datadog_patch', False):
        setattr(aiobotocore.client, '_datadog_patch', False)
        unwrap(aiobotocore.client.AioBaseClient, '_make_api_call')


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
            for arg in aws.unpacking_args(args, ARGS_NAME, TRACED_ARGS):
                span.set_tag(arg[0], arg[1])

        region_name = deep_getattr(instance, "meta.region_name")

        meta = {
            'aws.agent': 'botocore',
            'aws.operation': operation,
            'aws.region': region_name,
        }
        span.set_tags(meta)

        result = yield from original_func(*args, **kwargs)  # noqa: E999

        span.set_tag(http.STATUS_CODE, result['ResponseMetadata']['HTTPStatusCode'])
        span.set_tag("retry_attempts", result['ResponseMetadata']['RetryAttempts'])

        return result
