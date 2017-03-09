"""
Trace queries to aws api done via botocore client
"""

# project
from ddtrace import Pin
from ddtrace.util import deep_getattr

# 3p
import wrapt
import botocore.client

from ...ext import http

# Original botocore client class
_Botocore_client = botocore.client.BaseClient

SPAN_TYPE = "http"


def patch():
    # Checking for the version compatibility before patching
    wrapt.wrap_function_wrapper('botocore.client', 'BaseClient._make_api_call', patched_api_call)
    Pin(service="botocore", app="botocore", app_type="web").onto(botocore.client.BaseClient)


def patched_api_call(original_func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return original_func(*args, **kwargs)

    with pin.tracer.trace('botocore.command', service=pin.service, span_type=SPAN_TYPE) as span:
        endpoint_name = deep_getattr(instance, "_endpoint._endpoint_prefix")
        region_name = deep_getattr(instance, "meta.region_name")

        operation, _ = args
        meta = {
            'aws.agent': 'botocore',
            'aws.operation': operation,
            'aws.endpoint': endpoint_name,
            'aws.region': region_name,
        }

        span.resource = '%s.%s.%s' % (operation, endpoint_name, region_name)
        span.set_tags(meta)

        if not endpoint_name == "kms" and not endpoint_name == "sts":
            span.set_meta("botocore.args", args)
            span.set_meta("botocore.kwargs", kwargs)

        result = original_func(*args, **kwargs)
        span.set_tag(http.STATUS_CODE, result['ResponseMetadata']['HTTPStatusCode'])
        span.set_tag("retry_attempts", result['ResponseMetadata']['RetryAttempts'])
        return result
