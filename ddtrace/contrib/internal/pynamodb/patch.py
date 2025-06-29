"""
Trace queries to botocore api done via a pynamodb client
"""

from typing import Dict

import pynamodb.connection.base
import wrapt

from ddtrace import config
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_cloud_api_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import deep_getattr
from ddtrace.trace import Pin


# Pynamodb connection class
_PynamoDB_client = pynamodb.connection.base.Connection

config._add(
    "pynamodb",
    {
        "_default_service": schematize_service_name("pynamodb"),
    },
)


def get_version():
    # type: () -> str
    return getattr(pynamodb, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"pynamodb": ">=5.0"}


def patch():
    if getattr(pynamodb.connection.base, "_datadog_patch", False):
        return
    pynamodb.connection.base._datadog_patch = True

    wrapt.wrap_function_wrapper("pynamodb.connection.base", "Connection._make_api_call", patched_api_call)
    Pin(service=None).onto(pynamodb.connection.base.Connection)


def unpatch():
    if getattr(pynamodb.connection.base, "_datadog_patch", False):
        pynamodb.connection.base._datadog_patch = False
        unwrap(pynamodb.connection.base.Connection, "_make_api_call")


def patched_api_call(original_func, instance, args, kwargs):
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

        span.set_tag(_SPAN_MEASURED_KEY)

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

        result = original_func(*args, **kwargs)

        return result
