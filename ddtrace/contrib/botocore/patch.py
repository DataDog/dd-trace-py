"""
Trace queries to aws api done via botocore client
"""

# project
import ddtrace

# 3p
import wrapt
import botocore.client

from ...ext import AppTypes
from ...ext import http

# Original botocore client class
_Botocore_client = botocore.client.BaseClient

DEFAULT_SERVICE = "aws"
SPAN_TYPE = "botocore"


def patch():
    wrapt.wrap_function_wrapper('botocore.client', 'BaseClient._make_api_call', patched_api_call)


def patched_api_call(original_func, instance, args, kwargs):

    tracer = getattr(instance, 'datadog_tracer', ddtrace.tracer)
    tracer.set_service_info(service=DEFAULT_SERVICE, app=SPAN_TYPE, app_type=AppTypes.web)

    if not tracer.enabled:
        return original_func(*args, **kwargs)

    with tracer.trace("aws.api.request") as span:
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

        result = original_func(*args, **kwargs)
        span.set_tag(http.STATUS_CODE, result['ResponseMetadata']['HTTPStatusCode'])
        span.set_tag("retry_attempts", result['ResponseMetadata']['RetryAttempts'])
        return result


def add_cleaned_api_params(meta, operation_name, api_params):
    """ Avoid sending sensitive information
    """
    if operation_name != 'AssumeRole':
        meta['aws.api_params'] = str(api_params)
    return meta
