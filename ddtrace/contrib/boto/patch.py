import boto.connection
import wrapt

from ddtrace import Pin

from ...ext import http

# Original boto client class
_Boto_client = boto.connection.AWSQueryConnection

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

    with pin.tracer.trace('boto.command', service=pin.service, span_type=SPAN_TYPE) as span:
        operation_name, _, _, _ = args

        # Obtaining region name
        region = getattr(instance, "region")
        region_name = get_region_name(region)

        # Obtaining endpoint name and region name
        endpoint_name = None
        host = getattr(instance, "host")
        if host:
            host = host.split('.')
            if len(host) > 2:
                endpoint_name = host[0]

        meta = {
            'aws.agent': 'boto',
            'aws.operation': operation_name,
            'aws.endpoint': endpoint_name,
            'aws.region': region_name,
        }

        span.resource = '%s.%s.%s' % (operation_name, endpoint_name, region_name)
        span.set_tags(meta)

        span.set_meta("boto.args", args)
        span.set_meta("boto.kwargs", kwargs)

        # Original func returns a boto.connection.HPPResponse object
        result = original_func(*args, **kwargs)
        span.set_tag(http.STATUS_CODE, getattr(result, "status"))
        span.set_tag(http.METHOD, getattr(result, "_method"))

        return result


# s3, lambda
def patched_auth_request(original_func, instance, args, kwargs):

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return original_func(*args, **kwargs)

    with pin.tracer.trace('boto.command', service=pin.service, span_type=SPAN_TYPE) as span:
        method = args[0]
        path = args[1]
        operation_name = "{}.{}".format(method, path)

        # Obtaining region name
        region = getattr(instance, "region", None)
        region_name = get_region_name(region)

        # Obtaining endpoint name and if possible the region
        host = getattr(instance, "host")
        if host:
            host = host.split('.')
            endpoint_name = host[0]

        meta = {
            'aws.agent': 'boto',
            'aws.operation': operation_name,
            'aws.endpoint': endpoint_name,
            'aws.region': region_name,
        }

        span.resource = '%s.%s.%s' % (operation_name, endpoint_name, region_name)
        span.set_tags(meta)

        # Original func returns a boto.connection.HTTPResponse object
        result = original_func(*args, **kwargs)
        span.set_tag(http.STATUS_CODE, getattr(result, "status"))
        span.set_tag(http.METHOD, getattr(result, "_method"))

        span.set_meta("boto.args", args)
        span.set_meta("boto.kwargs", kwargs)

        return result


def get_region_name(region):
    if not region:
        return None
    if isinstance(region, str):
        return region.split(":")[1]
    else:
        return region.name
