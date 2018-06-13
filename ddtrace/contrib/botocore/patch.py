"""
Trace queries to aws api done via botocore client
"""
# 3p
import wrapt
import botocore.client

# project
from ...pin import Pin
from ...ext import http, aws
from ...utils.formats import deep_getattr
from ...utils.wrappers import unwrap


# Original botocore client class
_Botocore_client = botocore.client.BaseClient

SPAN_TYPE = "http"
ARGS_NAME = ("action", "params", "path", "verb")
TRACED_ARGS = ["params", "path", "verb"]


def patch():
    if getattr(botocore.client, '_datadog_patch', False):
        return
    setattr(botocore.client, '_datadog_patch', True)

    wrapt.wrap_function_wrapper('botocore.client', 'BaseClient._make_api_call', patched_api_call)
    Pin(service="aws", app="aws", app_type="web").onto(botocore.client.BaseClient)


def unpatch():
    if getattr(botocore.client, '_datadog_patch', False):
        setattr(botocore.client, '_datadog_patch', False)
        unwrap(botocore.client.BaseClient, '_make_api_call')


def patched_api_call(original_func, instance, args, kwargs):

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return original_func(*args, **kwargs)

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

        result = original_func(*args, **kwargs)

        span.set_tag(http.STATUS_CODE, result['ResponseMetadata']['HTTPStatusCode'])
        span.set_tag("retry_attempts", result['ResponseMetadata']['RetryAttempts'])

        return result
