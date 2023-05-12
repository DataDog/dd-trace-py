def service_name_v0(v0_service_name):
    return v0_service_name


def service_name_v1(*_, **__):
    from ddtrace import config as dd_config

    return dd_config.service


def database_operation_v0(v0_operation, database_provider=None):
    return v0_operation


def database_operation_v1(v0_operation, database_provider=None):
    operation = "query"
    assert database_provider is not None, "You must specify a database provider, not 'None'"
    return "{}.{}".format(database_provider, operation)


def cache_operation_v0(v0_operation, cache_provider=None):
    return v0_operation


def cache_operation_v1(v0_operation, cache_provider=None):
    assert cache_provider is not None, "You must specify a cache provider, not 'None'"
    operation = "command"
    return "{}.{}".format(cache_provider, operation)


def cloud_api_operation_v0(v0_operation, cloud_provider=None, cloud_service=None):
    return v0_operation


def cloud_api_operation_v1(v0_operation, cloud_provider=None, cloud_service=None):
    return "{}.{}.request".format(cloud_provider, cloud_service)


_SPAN_ATTRIBUTE_TO_FUNCTION = {
    "v0": {
        "service_name": service_name_v0,
        "database_operation": database_operation_v0,
        "cache_operation": cache_operation_v0,
        "cloud_api_operation": cloud_api_operation_v0,
    },
    "v1": {
        "service_name": service_name_v1,
        "database_operation": database_operation_v1,
        "cache_operation": cache_operation_v1,
        "cloud_api_operation": cloud_api_operation_v1,
    },
}


_DEFAULT_SPAN_SERVICE_NAMES = {"v0": None, "v1": "unnamed-python-service"}
