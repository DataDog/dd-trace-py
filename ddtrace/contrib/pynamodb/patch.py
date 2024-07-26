"""
Trace queries to botocore api done via a pynamodb client
"""

import pynamodb.connection.base

from ddtrace import config
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_cloud_api_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor import wrapt
from ddtrace.vendor.debtcollector import deprecate

from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_KIND
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanKind
from ...ext import SpanTypes
from ...ext import db
from ...internal.utils import ArgumentError
from ...internal.utils import get_argument_value
from ...internal.utils.formats import deep_getattr
from ...pin import Pin
from .. import trace_utils
from ..trace_utils import unwrap


# Pynamodb connection class
_PynamoDB_client = pynamodb.connection.base.Connection

config._add(
    "pynamodb",
    {
        "_default_service": schematize_service_name("pynamodb"),
    },
)


def _get_version():
    # type: () -> str
    return getattr(pynamodb, "__version__", "")


def get_version():
    deprecate(
        "get_version is deprecated",
        message="get_version is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _get_version()


def patch():
    if getattr(pynamodb.connection.base, "_datadog_patch", False):
        return
    pynamodb.connection.base._datadog_patch = True

    wrapt.wrap_function_wrapper("pynamodb.connection.base", "Connection._make_api_call", _patched_api_call)
    Pin(service=None).onto(pynamodb.connection.base.Connection)


def unpatch():
    if getattr(pynamodb.connection.base, "_datadog_patch", False):
        pynamodb.connection.base._datadog_patch = False
        unwrap(pynamodb.connection.base.Connection, "_make_api_call")


def _patched_api_call(original_func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return original_func(*args, **kwargs)

    with pin.tracer.trace(
        schematize_cloud_api_operation("pynamodb.command", cloud_provider="aws", cloud_service="dynamodb"),
        service=trace_utils.ext_service(pin, config.pynamodb, "pynamodb"),
        span_type=SpanTypes.HTTP,
    ) as span:
        span.set_tag_str(COMPONENT, config.pynamodb.integration_name)
        span.set_tag_str(db.SYSTEM, "dynamodb")

        # set span.kind to the type of operation being performed
        span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)

        span.set_tag(SPAN_MEASURED_KEY)

        try:
            operation = get_argument_value(args, kwargs, 0, "operation_name")
            span.resource = operation

            if args[1] and "TableName" in args[1]:
                table_name = args[1]["TableName"]
                span.set_tag_str("table_name", table_name)
                span.set_tag_str("tablename", table_name)
                span.resource = span.resource + " " + table_name

        except ArgumentError:
            span.resource = "Unknown"
            operation = None

        region_name = deep_getattr(instance, "client.meta.region_name")

        meta = {
            "aws.agent": "pynamodb",
            "aws.operation": operation,
            "aws.region": region_name,
            "region": region_name,
        }
        span.set_tags(meta)

        # set analytics sample rate
        sample_rate = config.pynamodb.get_analytics_sample_rate(use_global_config=True)

        if sample_rate is not None:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, sample_rate)

        result = original_func(*args, **kwargs)

        return result


def patched_api_call(original_func, instance, args, kwargs):
    deprecate(
        "patched_api_call is deprecated",
        message="patched_api_call is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _patched_api_call(original_func, instance, args, kwargs)
