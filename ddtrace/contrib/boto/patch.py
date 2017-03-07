import boto.connection
import wrapt
import json

from ddtrace import Pin

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
    # Checking on the version for compatibility before patching
    wrapt.wrap_function_wrapper('boto.connection', 'AWSQueryConnection.make_request', patched_query_request)
    wrapt.wrap_function_wrapper('boto.connection', 'AWSAuthConnection.make_request', patched_auth_request)
    Pin(service="boto", app="boto", app_type="web").onto(boto.connection.AWSQueryConnection)
    Pin(service="boto", app="boto", app_type="web").onto(boto.connection.AWSAuthConnection)


# ec2, sqs, kinesis
def patched_query_request(original_func, instance, args, kwargs):

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return original_func(*args, **kwargs)

    with pin.tracer.trace('boto.command', service=DEFAULT_SERVICE, span_type=SPAN_TYPE) as span:
        operation_name, _, _, _ = args
        span.set_tag('aws.operation', operation_name)
        # Obtaining region name
        region = getattr(instance, "region", ":")
        span.set_tag('aws.region', get_region_name(region))

        span.set_tag("args", json.dumps(args, ensure_ascii=False))
        span.set_tag("kwargs", json.dumps(kwargs, ensure_ascii=False))

        # Obtaining endpoint name and region name
        host = getattr(instance, "host", "unknown.unknown..").split('.')
        endpoint_name = "unknown"
        if len(host) == 4:
            endpoint_name = host[0]
        span.set_tag('aws.endpoint', endpoint_name)

        # Original func returns a boto.connection.HPPResponse object
        result = original_func(*args, **kwargs)
        span.set_tag(http.STATUS_CODE, getattr(result, "status"))
        span.set_tag(http.METHOD, getattr(result, "_method", "unknown"))

        return result


# s3, lambda
def patched_auth_request(original_func, instance, args, kwargs):

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return original_func(*args, **kwargs)

    with pin.tracer.trace('boto.command', service=DEFAULT_SERVICE, span_type=SPAN_TYPE) as span:

        # Original func returns a boto.connection.HPPResponse object
        result = original_func(*args, **kwargs)
        span.set_tag(http.STATUS_CODE, getattr(result, "status"))
        span.set_tag(http.METHOD, getattr(result, "_method", "unknown"))

        span.set_tag("args", json.dumps(args, ensure_ascii=False))
        span.set_tag("kwargs", json.dumps(kwargs, ensure_ascii=False))

        # Obtaining region name
        region = getattr(instance, "region", ":")
        span.set_tag('aws.region', get_region_name(region))

        # Obtaining endpoint name and if possible the region
        host = getattr(instance, "host", "unknown.unknown.").split('.')
        endpoint_name = "unknown"
        if len(host) == 3:
            endpoint_name = host[0]
        # In this case we also have the the region name
        elif len(host) >= 4:
            endpoint_name = host[0]
        span.set_tag('aws.endpoint', endpoint_name)

        return result


def get_region_name(region):
    if isinstance(region, str):
        return region.split(":")[1]
    else:
        return region.name
