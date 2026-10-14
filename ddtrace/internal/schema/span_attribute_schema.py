"""
Legacy import path for ``ddtrace.internal.schema.span_attribute_schema``, which moved to
``ddtrace_base.schema.span_attribute_schema``.

This module is a thin re-export shim so external consumers of the historical
``ddtrace.internal.schema.span_attribute_schema`` import path keep working..
Import from ````ddtrace_base.schema.span_attribute_schema```` instead.
"""

from ddtrace_base.schema.span_attribute_schema import SpanDirection as SpanDirection  # noqa: F401
from ddtrace_base.schema.span_attribute_schema import cache_operation_v0 as cache_operation_v0  # noqa: F401
from ddtrace_base.schema.span_attribute_schema import cache_operation_v1 as cache_operation_v1  # noqa: F401
from ddtrace_base.schema.span_attribute_schema import cloud_api_operation_v0 as cloud_api_operation_v0  # noqa: F401
from ddtrace_base.schema.span_attribute_schema import cloud_api_operation_v1 as cloud_api_operation_v1  # noqa: F401
from ddtrace_base.schema.span_attribute_schema import cloud_faas_operation_v0 as cloud_faas_operation_v0  # noqa: F401
from ddtrace_base.schema.span_attribute_schema import cloud_faas_operation_v1 as cloud_faas_operation_v1  # noqa: F401
from ddtrace_base.schema.span_attribute_schema import (
    cloud_messaging_operation_v0 as cloud_messaging_operation_v0,  # noqa: F401
)
from ddtrace_base.schema.span_attribute_schema import (
    cloud_messaging_operation_v1 as cloud_messaging_operation_v1,  # noqa: F401
)
from ddtrace_base.schema.span_attribute_schema import database_operation_v0 as database_operation_v0  # noqa: F401
from ddtrace_base.schema.span_attribute_schema import database_operation_v1 as database_operation_v1  # noqa: F401
from ddtrace_base.schema.span_attribute_schema import messaging_operation_v0 as messaging_operation_v0  # noqa: F401
from ddtrace_base.schema.span_attribute_schema import messaging_operation_v1 as messaging_operation_v1  # noqa: F401
from ddtrace_base.schema.span_attribute_schema import service_name_v0 as service_name_v0  # noqa: F401
from ddtrace_base.schema.span_attribute_schema import service_name_v1 as service_name_v1  # noqa: F401
from ddtrace_base.schema.span_attribute_schema import url_operation_v0 as url_operation_v0  # noqa: F401
from ddtrace_base.schema.span_attribute_schema import url_operation_v1 as url_operation_v1  # noqa: F401
