import ddtrace

import boto.connection
import wrapt

from ...ext import AppTypes
from ...ext import http

# Original boto client class
_Boto_client = boto.connection.AWSQueryConnection

DEFAULT_SERVICE = "aws"
SPAN_TYPE = "boto"


def patch():

    """ AWSQueryConnection and AWSAuthConnection are two different classes called by
    different services for connection. For exemple EC2 uses AWSQueryConnection and
    S3 uses AWSAuthConnection
    """
    wrapt.wrap_function_wrapper('boto.connection', 'AWSQueryConnection.make_request', patched_query_request)
    wrapt.wrap_function_wrapper('boto.connection', 'AWSAuthConnection.make_request', patched_auth_request)


def patched_query_request(original_func, instance, args, kwargs):
    tracer = getattr(instance, 'datadog_tracer', ddtrace.tracer)
    tracer.set_service_info(service=DEFAULT_SERVICE, app=SPAN_TYPE, app_type=AppTypes.web)

    if not tracer.enabled:
        return original_func(*args, **kwargs)

    with tracer.trace("aws.api.request") as span:
        operation_name, _, _, _ = args
        span.set_tag('aws.operation', operation_name)

        # Original func returns a boto.connection.HPPResponse object
        result = original_func(*args, **kwargs)
        span.set_tag(http.STATUS_CODE, getattr(result, "status", 500))
        span.set_tag(http.METHOD, getattr(result, "_method", "unknown"))

        host = getattr(instance, "host", "unknown.unknown..").split('.')
        if len(host) == 4:
            endpoint_name = host[0]
            region_name = host[1]
        span.set_tag('aws.endpoint', endpoint_name)
        span.set_tag('aws.region', region_name)

        return result


def patched_auth_request(original_func, instance, args, kwargs):
    tracer = getattr(instance, 'datadog_tracer', ddtrace.tracer)
    tracer.set_service_info(service=DEFAULT_SERVICE, app=SPAN_TYPE, app_type=AppTypes.web)

    if not tracer.enabled:
        return original_func(*args, **kwargs)

    with tracer.trace("aws.api.request") as span:

        # Original func returns a boto.connection.HPPResponse object
        result = original_func(*args, **kwargs)
        span.set_tag(http.STATUS_CODE, getattr(result, "status", 500))
        span.set_tag(http.METHOD, getattr(result, "_method", "unknown"))

        host = getattr(instance, "host", "unknown.unknown.").split('.')
        if len(host) == 3:
            endpoint_name = host[0]
        span.set_tag('aws.endpoint', endpoint_name)
        span.set_tag('aws.operation', "unknown")

        return result
