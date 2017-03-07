"""
Trace queries to aws api done via botocore client
"""

# project
from ddtrace import Pin

# 3p
import wrapt
import botocore.client

from ...ext import http

# Original botocore client class
_Botocore_client = botocore.client.BaseClient

DEFAULT_SERVICE = "aws"
SPAN_TYPE = "http"


def patch():
    # Checking for the version compatibility before patching
    wrapt.wrap_function_wrapper('botocore.client', 'BaseClient._make_api_call', patched_api_call)
    Pin(service="botocore", app="botocore", app_type="web").onto(botocore.client.BaseClient)


def patched_api_call(original_func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return original_func(*args, **kwargs)

    with pin.tracer.trace('botocore.command', service=DEFAULT_SERVICE, span_type=SPAN_TYPE) as span:
        _endpoint = getattr(instance, "_endpoint", None)
        endpoint_name = getattr(_endpoint, "_endpoint_prefix", "unknown")
        _boto_meta = getattr(instance, "meta", None)
        region_name = getattr(_boto_meta, "region_name", "unknown")
        operation, _ = args
        meta = {
            'aws.agent': 'botocore',
            'aws.operation': operation,
            'aws.endpoint': endpoint_name,
            'aws.region': region_name,
        }

        meta = add_cleaned_api_params(meta, operation, kwargs)
        span.resource = '%s.%s.%s' % (operation, endpoint_name, region_name)
        span.set_tags(meta)


        span.set_meta("botocore.args", args)
        span.set_meta("botocore.kwargs", kwargs)
        # span.set_tag("args", json.dumps(args, ensure_ascii=False))
        # span.set_tag("kwargs", json.dumps(kwargs, ensure_ascii=False))

        result = original_func(*args, **kwargs)
        span.set_tag(http.STATUS_CODE, result['ResponseMetadata']['HTTPStatusCode'])
        span.set_tag("retry_attempts", result['ResponseMetadata']['RetryAttempts'])
        return result


def add_cleaned_api_params(meta, operation_name, api_params):
    """ Avoid sending sensitive information
    """
    if operation_name != 'AssumeRole' and api_params:
        meta['aws.api_params'] = str(api_params)
    return meta
