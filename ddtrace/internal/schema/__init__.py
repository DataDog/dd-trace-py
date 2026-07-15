import os

from ddtrace.internal.utils.formats import asbool

from .default import _get_schema_version
from .span_attribute_schema import _SPAN_ATTRIBUTE_TO_FUNCTION
from .span_attribute_schema import SpanDirection


SCHEMA_VERSION = _get_schema_version()
_remove_client_service_names = asbool(os.getenv("DD_TRACE_REMOVE_INTEGRATION_SERVICE_NAMES_ENABLED", default=False))
_service_name_schema_version = "v0" if SCHEMA_VERSION == "v0" and not _remove_client_service_names else "v1"

schematize_cache_operation = _SPAN_ATTRIBUTE_TO_FUNCTION[SCHEMA_VERSION]["cache_operation"]
schematize_cloud_api_operation = _SPAN_ATTRIBUTE_TO_FUNCTION[SCHEMA_VERSION]["cloud_api_operation"]
schematize_cloud_faas_operation = _SPAN_ATTRIBUTE_TO_FUNCTION[SCHEMA_VERSION]["cloud_faas_operation"]
schematize_cloud_messaging_operation = _SPAN_ATTRIBUTE_TO_FUNCTION[SCHEMA_VERSION]["cloud_messaging_operation"]
schematize_database_operation = _SPAN_ATTRIBUTE_TO_FUNCTION[SCHEMA_VERSION]["database_operation"]
schematize_messaging_operation = _SPAN_ATTRIBUTE_TO_FUNCTION[SCHEMA_VERSION]["messaging_operation"]
schematize_service_name = _SPAN_ATTRIBUTE_TO_FUNCTION[_service_name_schema_version]["service_name"]
schematize_url_operation = _SPAN_ATTRIBUTE_TO_FUNCTION[SCHEMA_VERSION]["url_operation"]

__all__ = [
    "SCHEMA_VERSION",
    "SpanDirection",
    "schematize_cache_operation",
    "schematize_cloud_api_operation",
    "schematize_cloud_faas_operation",
    "schematize_cloud_messaging_operation",
    "schematize_database_operation",
    "schematize_messaging_operation",
    "schematize_service_name",
    "schematize_url_operation",
]
