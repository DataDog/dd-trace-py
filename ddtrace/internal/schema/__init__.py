import os

from .span_attribute_schema import _DEFAULT_SPAN_SERVICE_NAMES
from .span_attribute_schema import _SPAN_ATTRIBUTE_TO_FUNCTION


# Span attribute schema
def _validate_schema(version):
    error_message = (
        "You have specified an invalid span attribute schema version: '{}'.".format(__schema_version),
        "Valid options are: {}. You can change the specified value by updating".format(
            _SPAN_ATTRIBUTE_TO_FUNCTION.keys()
        ),
        "the value exported in the 'DD_TRACE_SPAN_ATTRIBUTE_SCHEMA' environment variable.",
    )

    assert version in _SPAN_ATTRIBUTE_TO_FUNCTION.keys(), error_message


__schema_version = os.getenv("DD_TRACE_SPAN_ATTRIBUTE_SCHEMA", default="v0")
_validate_schema(__schema_version)

DEFAULT_SPAN_SERVICE_NAME = _DEFAULT_SPAN_SERVICE_NAMES[__schema_version]
schematize_service_name = _SPAN_ATTRIBUTE_TO_FUNCTION[__schema_version]["service_name"]
schematize_database_operation = _SPAN_ATTRIBUTE_TO_FUNCTION[__schema_version]["database_operation"]
schematize_cache_operation = _SPAN_ATTRIBUTE_TO_FUNCTION[__schema_version]["cache_operation"]
schematize_cloud_api_operation = _SPAN_ATTRIBUTE_TO_FUNCTION[__schema_version]["cloud_api_operation"]

__all__ = [
    "DEFAULT_SPAN_SERVICE_NAME",
    "schematize_service_name",
    "schematize_database_operation",
    "schematize_cache_operation",
    "schematize_cloud_api_operation",
]
