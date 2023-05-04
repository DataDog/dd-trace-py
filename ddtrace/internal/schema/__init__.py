import os

from .span_attribute_schema import SPAN_ATTRIBUTE_SCHEMA_VERSIONS
from .span_attribute_schema import _DEFAULT_SPAN_SERVICE_NAMES


# Span attribute schema
def _validate_schema(version):
    error_message = (
        "You have specified an invalid span attribute schema version: '{}'.".format(__schema_version),
        "Valid options are: {}. You can change the specified value by updating".format(SPAN_ATTRIBUTE_SCHEMA_VERSIONS),
        "the value exported in the 'DD_TRACE_SPAN_ATTRIBUTE_SCHEMA' environment variable.",
    )

    assert version in SPAN_ATTRIBUTE_SCHEMA_VERSIONS, error_message


__schema_version = os.getenv("DD_TRACE_SPAN_ATTRIBUTE_SCHEMA", default="v0")
_validate_schema(__schema_version)

DEFAULT_SPAN_SERVICE_NAME = _DEFAULT_SPAN_SERVICE_NAMES[__schema_version]
schematize_service_name = SPAN_ATTRIBUTE_SCHEMA_VERSIONS.get(__schema_version)

__all__ = ["schematize_service_name", "DEFAULT_SPAN_SERVICE_NAME"]
