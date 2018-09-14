import boto.connection
import wrapt
import inspect

from ...pin import Pin
from ...ext import http, aws
from ...utils.wrappers import unwrap


# Original boto client class
_Boto_client = boto.connection.AWSQueryConnection

SPAN_TYPE = "boto"
AWS_QUERY_ARGS_NAME = ("operation_name", "params", "path", "verb")
AWS_AUTH_ARGS_NAME = (
    "method",
    "path",
    "headers",
    "data",
    "host",
    "auth_path",
    "sender",
)
AWS_QUERY_TRACED_ARGS = ["operation_name", "params", "path"]
AWS_AUTH_TRACED_ARGS = ["path", "data", "host"]


def patch():

    """ AWSQueryConnection and AWSAuthConnection are two different classes called by
    different services for connection. For exemple EC2 uses AWSQueryConnection and
    S3 uses AWSAuthConnection
    """
    if getattr(boto.connection, "_datadog_patch", False):
        return
    setattr(boto.connection, "_datadog_patch", True)

    wrapt.wrap_function_wrapper(
        "boto.connection", "AWSQueryConnection.make_request", patched_query_request
    )
    wrapt.wrap_function_wrapper(
        "boto.connection", "AWSAuthConnection.make_request", patched_auth_request
    )
    Pin(service="aws", app="aws", app_type="web").onto(
        boto.connection.AWSQueryConnection
    )
    Pin(service="aws", app="aws", app_type="web").onto(
        boto.connection.AWSAuthConnection
    )


def unpatch():
    if getattr(boto.connection, "_datadog_patch", False):
        setattr(boto.connection, "_datadog_patch", False)
        unwrap(boto.connection.AWSQueryConnection, "make_request")
        unwrap(boto.connection.AWSAuthConnection, "make_request")


# ec2, sqs, kinesis
def patched_query_request(original_func, instance, args, kwargs):

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return original_func(*args, **kwargs)

    endpoint_name = getattr(instance, "host").split(".")[0]

    with pin.tracer.trace(
        "{}.command".format(endpoint_name),
        service="{}.{}".format(pin.service, endpoint_name),
        span_type=SPAN_TYPE,
    ) as span:

        operation_name = None
        if args:
            operation_name = args[0]
            span.resource = "%s.%s" % (endpoint_name, operation_name.lower())
        else:
            span.resource = endpoint_name

        # Adding the args in AWS_QUERY_TRACED_ARGS if exist to the span
        if not aws.is_blacklist(endpoint_name):
            for arg in aws.unpacking_args(
                args, AWS_QUERY_ARGS_NAME, AWS_QUERY_TRACED_ARGS
            ):
                span.set_tag(arg[0], arg[1])

        # Obtaining region name
        region_name = _get_instance_region_name(instance)

        meta = {
            aws.AGENT: "boto",
            aws.OPERATION: operation_name,
        }
        if region_name:
            meta[aws.REGION] = region_name

        span.set_tags(meta)

        # Original func returns a boto.connection.HTTPResponse object
        result = original_func(*args, **kwargs)
        span.set_tag(http.STATUS_CODE, getattr(result, "status"))
        span.set_tag(http.METHOD, getattr(result, "_method"))

        return result


# s3, lambda
def patched_auth_request(original_func, instance, args, kwargs):

    # Catching the name of the operation that called make_request()
    operation_name = None
    frame = inspect.currentframe()
    # go up the call stack twice to get into the boto frame
    boto_frame = frame.f_back.f_back
    operation_name = boto_frame.f_code.co_name

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return original_func(*args, **kwargs)

    endpoint_name = getattr(instance, "host").split(".")[0]

    with pin.tracer.trace(
        "{}.command".format(endpoint_name),
        service="{}.{}".format(pin.service, endpoint_name),
        span_type=SPAN_TYPE,
    ) as span:

        # Adding the args in AWS_AUTH_TRACED_ARGS if exist to the span
        if not aws.is_blacklist(endpoint_name):
            for arg in aws.unpacking_args(
                args, AWS_AUTH_ARGS_NAME, AWS_AUTH_TRACED_ARGS
            ):
                span.set_tag(arg[0], arg[1])

        if args:
            http_method = args[0]
            span.resource = "%s.%s" % (endpoint_name, http_method.lower())
        else:
            span.resource = endpoint_name

        # Obtaining region name
        region_name = _get_instance_region_name(instance)

        meta = {
            aws.AGENT: "boto",
            aws.OPERATION: operation_name,
        }
        if region_name:
            meta[aws.REGION] = region_name

        span.set_tags(meta)

        # Original func returns a boto.connection.HTTPResponse object
        result = original_func(*args, **kwargs)
        span.set_tag(http.STATUS_CODE, getattr(result, "status"))
        span.set_tag(http.METHOD, getattr(result, "_method"))

        return result


def _get_instance_region_name(instance):
    region = getattr(instance, "region", None)

    if not region:
        return None
    if isinstance(region, str):
        return region.split(":")[1]
    else:
        return region.name
