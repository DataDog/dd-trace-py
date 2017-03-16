import boto.connection
import wrapt
import inspect

from ddtrace import Pin

from ...ext import http
from ...ext import aws

# Original boto client class
_Boto_client = boto.connection.AWSQueryConnection

SPAN_TYPE = "boto"
AWS_QUERY_ARGS_NAME = ('operation_name', 'params', 'path', 'verb')
AWS_AUTH_ARGS_NAME = ('method', 'path', 'headers', 'data', 'host', 'auth_path', 'sender')
AWS_QUERY_TRACED_ARGS = ['operation_name', 'params', 'path']
AWS_AUTH_TRACED_ARGS = ['path', 'data', 'host']


def patch():

    """ AWSQueryConnection and AWSAuthConnection are two different classes called by
    different services for connection. For exemple EC2 uses AWSQueryConnection and
    S3 uses AWSAuthConnection
    """
    wrapt.wrap_function_wrapper('boto.connection', 'AWSQueryConnection.make_request', patched_query_request)
    wrapt.wrap_function_wrapper('boto.connection', 'AWSAuthConnection.make_request', patched_auth_request)
    Pin(service="aws", app="boto", app_type="web").onto(boto.connection.AWSQueryConnection)
    Pin(service="aws", app="boto", app_type="web").onto(boto.connection.AWSAuthConnection)


# ec2, sqs, kinesis
def patched_query_request(original_func, instance, args, kwargs):

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return original_func(*args, **kwargs)

    endpoint_name = getattr(instance, "host").split('.')[0]

    with pin.tracer.trace('boto.{}.command'.format(endpoint_name), service="{}.{}".format(pin.service, endpoint_name),
                          span_type=SPAN_TYPE) as span:

        operation_name, _, _, _ = args

        # Adding the args in AWS_QUERY_TRACED_ARGS if exist to the span
        if not aws.is_blacklist(endpoint_name):
            for arg in aws.unpacking_args(args, AWS_QUERY_ARGS_NAME, AWS_QUERY_TRACED_ARGS):
                span.set_tag(arg[0], arg[1])

        # Original func returns a boto.connection.HTTPResponse object
        result = original_func(*args, **kwargs)
        span.set_tag(http.STATUS_CODE, getattr(result, "status"))
        span.set_tag(http.METHOD, getattr(result, "_method"))

        # Obtaining region name
        region = getattr(instance, "region")
        region_name = get_region_name(region)

        if region_name:
            span.resource = '%s.%s.%s' % (endpoint_name, operation_name.lower(), region_name)
        else:
            span.resource = '%s.%s' % (endpoint_name, operation_name.lower())

        meta = {
            'aws.agent': 'boto',
            'aws.operation': operation_name,
            'aws.region': region_name,
        }
        span.set_tags(meta)

        return result


# s3, lambda
def patched_auth_request(original_func, instance, args, kwargs):

    # Catching the name of the operation that called make_request()
    operation_name = None
    for frame in inspect.getouterframes(inspect.currentframe()):
        # Going backwards in the traceback till first call outside off ddtrace before make_request
        if len(frame) > 3:
            if "ddtrace" not in frame[1].split('/') and frame[3] != 'make_request':
                operation_name = frame[3]
                break

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return original_func(*args, **kwargs)

    endpoint_name = getattr(instance, "host").split('.')[0]

    with pin.tracer.trace('boto.{}.command'.format(endpoint_name), service="{}.{}".format(pin.service, endpoint_name),
                          span_type=SPAN_TYPE) as span:

        # Adding the args in AWS_AUTH_TRACED_ARGS if exist to the span
        if not aws.is_blacklist(endpoint_name):
            for arg in aws.unpacking_args(args, AWS_AUTH_ARGS_NAME, AWS_AUTH_TRACED_ARGS):
                span.set_tag(arg[0], arg[1])

        # Original func returns a boto.connection.HTTPResponse object
        result = original_func(*args, **kwargs)
        http_method = getattr(result, "_method")
        span.set_tag(http.STATUS_CODE, getattr(result, "status"))
        span.set_tag(http.METHOD, http_method)

        # Obtaining region name
        region = getattr(instance, "region", None)
        region_name = get_region_name(region)

        if region_name:
            span.resource = '%s.%s.%s' % (endpoint_name, http_method.lower(), region_name)
        else:
            span.resource = '%s.%s' % (endpoint_name, http_method.lower())

        meta = {
            'aws.agent': 'boto',
            'aws.operation': operation_name,
            'aws.region': region_name,
        }
        span.set_tags(meta)

        return result


def get_region_name(region):
    if not region:
        return None
    if isinstance(region, str):
        return region.split(":")[1]
    else:
        return region.name
