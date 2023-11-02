import pytest


@pytest.mark.subprocess(env=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
def test_service_names_import_default():
    from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
    from ddtrace.internal.schema import schematize_cache_operation
    from ddtrace.internal.schema import schematize_cloud_api_operation
    from ddtrace.internal.schema import schematize_database_operation
    from ddtrace.internal.schema import schematize_service_name
    from ddtrace.internal.schema import schematize_url_operation
    from ddtrace.internal.schema.span_attribute_schema import cache_operation_v0
    from ddtrace.internal.schema.span_attribute_schema import cloud_api_operation_v0
    from ddtrace.internal.schema.span_attribute_schema import database_operation_v0
    from ddtrace.internal.schema.span_attribute_schema import service_name_v0
    from ddtrace.internal.schema.span_attribute_schema import url_operation_v0

    assert DEFAULT_SPAN_SERVICE_NAME is None
    assert schematize_service_name == service_name_v0
    assert schematize_database_operation == database_operation_v0
    assert schematize_cache_operation == cache_operation_v0
    assert schematize_cloud_api_operation == cloud_api_operation_v0
    assert schematize_url_operation == url_operation_v0


@pytest.mark.subprocess(env=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
def test_service_names_import_and_v0():
    from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
    from ddtrace.internal.schema import schematize_cache_operation
    from ddtrace.internal.schema import schematize_cloud_api_operation
    from ddtrace.internal.schema import schematize_database_operation
    from ddtrace.internal.schema import schematize_service_name
    from ddtrace.internal.schema import schematize_url_operation
    from ddtrace.internal.schema.span_attribute_schema import cache_operation_v0
    from ddtrace.internal.schema.span_attribute_schema import cloud_api_operation_v0
    from ddtrace.internal.schema.span_attribute_schema import database_operation_v0
    from ddtrace.internal.schema.span_attribute_schema import service_name_v0
    from ddtrace.internal.schema.span_attribute_schema import url_operation_v0

    assert DEFAULT_SPAN_SERVICE_NAME is None
    assert schematize_service_name == service_name_v0
    assert schematize_database_operation == database_operation_v0
    assert schematize_cache_operation == cache_operation_v0
    assert schematize_cloud_api_operation == cloud_api_operation_v0
    assert schematize_url_operation == url_operation_v0


@pytest.mark.subprocess(
    env=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"),
    parametrize={"DD_TRACE_REMOVE_INTEGRATION_SERVICE_NAMES_ENABLED": ["False", "True"]},
)
def test_service_name_imports_v1():
    from ddtrace.internal.constants import DEFAULT_SERVICE_NAME
    from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
    from ddtrace.internal.schema import schematize_cache_operation
    from ddtrace.internal.schema import schematize_cloud_api_operation
    from ddtrace.internal.schema import schematize_database_operation
    from ddtrace.internal.schema import schematize_service_name
    from ddtrace.internal.schema import schematize_url_operation
    from ddtrace.internal.schema.span_attribute_schema import cache_operation_v1
    from ddtrace.internal.schema.span_attribute_schema import cloud_api_operation_v1
    from ddtrace.internal.schema.span_attribute_schema import database_operation_v1
    from ddtrace.internal.schema.span_attribute_schema import service_name_v1
    from ddtrace.internal.schema.span_attribute_schema import url_operation_v1

    assert DEFAULT_SPAN_SERVICE_NAME == DEFAULT_SERVICE_NAME
    assert schematize_service_name == service_name_v1
    assert schematize_database_operation == database_operation_v1
    assert schematize_cache_operation == cache_operation_v1
    assert schematize_cloud_api_operation == cloud_api_operation_v1
    assert schematize_url_operation == url_operation_v1


@pytest.mark.subprocess(
    env=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0", DD_TRACE_REMOVE_INTEGRATION_SERVICE_NAMES_ENABLED="True")
)
def test_service_name_import_with_client_service_names_enabled_v0():
    """
    Service name parameters are flipped when DD_TRACE_REMOVE_INTEGRATION_SERVICE_NAMES_ENABLED is True for v0
    """
    from ddtrace.internal.constants import DEFAULT_SERVICE_NAME
    from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
    from ddtrace.internal.schema import schematize_cache_operation
    from ddtrace.internal.schema import schematize_cloud_api_operation
    from ddtrace.internal.schema import schematize_database_operation
    from ddtrace.internal.schema import schematize_service_name
    from ddtrace.internal.schema import schematize_url_operation
    from ddtrace.internal.schema.span_attribute_schema import cache_operation_v0
    from ddtrace.internal.schema.span_attribute_schema import cloud_api_operation_v0
    from ddtrace.internal.schema.span_attribute_schema import database_operation_v0
    from ddtrace.internal.schema.span_attribute_schema import service_name_v1
    from ddtrace.internal.schema.span_attribute_schema import url_operation_v0

    assert DEFAULT_SPAN_SERVICE_NAME == DEFAULT_SERVICE_NAME
    assert schematize_service_name == service_name_v1
    assert schematize_database_operation == database_operation_v0
    assert schematize_cache_operation == cache_operation_v0
    assert schematize_cloud_api_operation == cloud_api_operation_v0
    assert schematize_url_operation == url_operation_v0
