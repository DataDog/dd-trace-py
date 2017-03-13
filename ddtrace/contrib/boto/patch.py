import boto.connection
import wrapt
import traceback

from ddtrace import Pin

from ...ext import http
from ...ext import aws

# Original boto client class
_Boto_client = boto.connection.AWSQueryConnection

SPAN_TYPE = "boto"
AWS_AUTH_ARGS_NAME = ('method', 'path', 'headers', 'data', 'host', 'auth_path', 'sender')
AWS_AUTH_TRACED_ARGS = ['path', 'headers', 'data', 'host']


def patch():

    """ AWSQueryConnection and AWSAuthConnection are two different classes called by
    different services for connection. For exemple EC2 uses AWSQueryConnection and
    S3 uses AWSAuthConnection
    """
    # Checking on the version for compatibility before patching
    wrapt.wrap_function_wrapper('boto.connection', 'AWSQueryConnection.make_request', patched_query_request)
    wrapt.wrap_function_wrapper('boto.connection', 'AWSAuthConnection.make_request', patched_auth_request)
    Pin(service="aws", app="boto", app_type="web").onto(boto.connection.AWSQueryConnection)
    Pin(service="aws", app="boto", app_type="web").onto(boto.connection.AWSAuthConnection)


# ec2, sqs, kinesis
def patched_query_request(original_func, instance, args, kwargs):

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return original_func(*args, **kwargs)

    # Obtaining endpoint name
    endpoint = getattr(instance, "host")
    if endpoint:
        endpoint = endpoint.split('.')
        endpoint_name = endpoint[0]

    with pin.tracer.trace('boto.command', service="{}.{}".format(pin.service, endpoint_name),
                          span_type=SPAN_TYPE) as span:
        # args contain action, params, path, verb
        operation_name, params, path, _ = args

        # Obtaining region name
        region = getattr(instance, "region")
        region_name = get_region_name(region)

        meta = {
            'aws.agent': 'boto',
            'aws.operation': operation_name,
            'aws.endpoint': endpoint_name,
            'aws.region': region_name,
        }

        span.resource = '%s.%s.%s' % (endpoint_name, operation_name.lower(), region_name)
        span.set_tags(meta)

        if not aws.is_blacklist(endpoint_name):
            if path:
                span.set_meta("args.path", path)
            if params:
                span.set_meta("args.params", params)

        # Original func returns a boto.connection.HTTPResponse object
        result = original_func(*args, **kwargs)
        span.set_tag(http.STATUS_CODE, getattr(result, "status"))
        span.set_tag(http.METHOD, getattr(result, "_method"))

        return result


# s3, lambda
def patched_auth_request(original_func, instance, args, kwargs):

    # Catching the name of the operation that called make_request()
    operation_name = traceback.extract_stack()[-3][2]

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return original_func(*args, **kwargs)

    # Obtaining endpoint name
    endpoint = getattr(instance, "host")
    if endpoint:
        endpoint = endpoint.split('.')
        endpoint_name = endpoint[0]

    with pin.tracer.trace('boto.command', service="{}.{}".format(pin.service, endpoint_name),
                          span_type=SPAN_TYPE) as span:

        # Obtaining region name
        region = getattr(instance, "region", None)
        region_name = get_region_name(region)

        # Obtaining endpoint name
        endpoint = getattr(instance, "host")
        if endpoint:
            endpoint = endpoint.split('.')
            endpoint_name = endpoint[0]

        meta = {
            'aws.agent': 'boto',
            'aws.operation': operation_name,
            'aws.endpoint': endpoint_name,
            'aws.region': region_name,
        }

        # Original func returns a boto.connection.HTTPResponse object
        result = original_func(*args, **kwargs)
        http_method = getattr(result, "_method")
        span.set_tag(http.STATUS_CODE, getattr(result, "status"))
        span.set_tag(http.METHOD, getattr(result, "_method"))

        if region:
            span.resource = '%s.%s.%s' % (endpoint_name, http_method.lower(), region_name)
        else:
            span.resource = '%s.%s' % (endpoint_name, http_method.lower())
        span.set_tags(meta)

        if not aws.is_blacklist(endpoint_name):

            # args contain: method, path, headers, data, host, auth_path, sender
            for arg in unpacking_args(args, AWS_AUTH_ARGS_NAME, AWS_AUTH_TRACED_ARGS):
                span.set_tag(arg[0], arg[1])

        return result


def get_region_name(region):
    if not region:
        return None
    if isinstance(region, str):
        return region.split(":")[1]
    else:
        return region.name


def unpacking_args(args, args_name, traced_args_list):
    index = 0
    response = []
    for arg in args:
        if arg and args_name[index] in traced_args_list:
            response += [(args_name[index], arg)]
        index += 1
    return response
