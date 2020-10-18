"""
Trace queries to botocore api done via a pynamodb client
"""

from ddtrace.vendor import wrapt
from ddtrace import config
import pynamodb.connection.base


from ...constants import ANALYTICS_SAMPLE_RATE_KEY, SPAN_MEASURED_KEY
from ...pin import Pin
from ...ext import SpanTypes
from ...utils.formats import deep_getattr
from ...utils.wrappers import unwrap
from .. import trace_utils

# Pynamodb connection class
_PynamoDB_client = pynamodb.connection.base.Connection

config._add(
    "pynamodb",
    {
        "_default_service": "pynamodb",
    },
)


def patch():
    if getattr(pynamodb.connection.base, "_datadog_patch", False):
        return
    setattr(pynamodb.connection.base, "_datadog_patch", True)

    wrapt.wrap_function_wrapper("pynamodb.connection.base", "Connection._make_api_call", patched_api_call)
    Pin(service=None).onto(pynamodb.connection.base.Connection)


def unpatch():
    if getattr(pynamodb.connection.base, "_datadog_patch", False):
        setattr(pynamodb.connection.base, "_datadog_patch", False)
        unwrap(pynamodb.connection.base.Connection, "_make_api_call")


def patched_api_call(original_func, instance, args, kwargs):

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return original_func(*args, **kwargs)

    with pin.tracer.trace(
        "pynamodb.command", service=trace_utils.ext_service(pin, config.pynamodb, "pynamodb"), span_type=SpanTypes.HTTP
    ) as span:

        span.set_tag(SPAN_MEASURED_KEY)

        if args and args[0]:
            operation = args[0]
            span.resource = operation

            if args[1] and "TableName" in args[1]:
                table_name = args[1]["TableName"]
                span.set_tag("table_name", table_name)
                span.resource = span.resource + " " + table_name

        else:
            span.resource = "Unknown"
            operation = None

        region_name = deep_getattr(instance, "client.meta.region_name")

        meta = {
            "aws.agent": "pynamodb",
            "aws.operation": operation,
            "aws.region": region_name,
        }
        span.set_tags(meta)

        # set analytics sample rate
        sample_rate = config.pynamodb.get_analytics_sample_rate(use_global_config=True)

        if sample_rate is not None:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, sample_rate)

        result = original_func(*args, **kwargs)

        return result
