"""
Trace queries to botocore api done via a pynamodb client
"""

import os
from ddtrace.vendor import wrapt
from ddtrace import config
import pynamodb.connection.base


from ...constants import ANALYTICS_SAMPLE_RATE_KEY, SPAN_MEASURED_KEY
from ...pin import Pin
from ...ext import SpanTypes
from ...utils.formats import deep_getattr, get_env
from ...utils.wrappers import unwrap

# Pynamodb connection class
_PynamoDB_client = pynamodb.connection.base.Connection

if "DD_PYNAMODB_SERVICE" in os.environ:
    service = os.getenv("DD_PYNAMODB_SERVICE")
else:
    service = get_env("pynamodb", "service_name")

config._add("pynamodb", {"service_name": service or "pynamodb",})


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
        "pynamodb.command", service=config.pynamodb["service_name"], span_type=SpanTypes.HTTP
    ) as span:

        span.set_tag(SPAN_MEASURED_KEY)

        operation = None

        if args and args[0]:
            operation = args[0]
            span.resource = operation

            if args[1] and "TableName" in args[1]:
                table_name = args[1]["TableName"]
                span.set_tag("table_name", table_name)
                span.resource = span.resource + " " + table_name

        else:
            span.resource = "Unknown"

        region_name = deep_getattr(instance, "client.meta.region_name")

        meta = {
            "aws.agent": "pynamodb",
            "aws.operation": operation,
            "aws.region": region_name,
        }
        span.set_tags(meta)

        # set analytics sample rate
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.pynamodb.get_analytics_sample_rate())

        result = original_func(*args, **kwargs)

        return result
