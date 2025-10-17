from enum import Enum
import sys
from typing import Dict
from typing import Optional

from ddtrace.internal.constants import DEFAULT_SERVICE_NAME
from ddtrace.settings._inferred_base_service import detect_service


class SpanDirection(Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    PROCESSING = "processing"


def service_name_v0(v0_service_name):
    return v0_service_name


def service_name_v1(*_, **__):
    from ddtrace import config as dd_config

    return dd_config.service


def database_operation_v0(v0_operation, database_provider=None):
    return v0_operation


def database_operation_v1(v0_operation, database_provider=None):
    operation = "query"
    return f"{database_provider}.{operation}"


def cache_operation_v0(v0_operation, cache_provider=None):
    return v0_operation


def cache_operation_v1(v0_operation, cache_provider=None):
    operation = "command"
    return f"{cache_provider}.{operation}"


def cloud_api_operation_v0(v0_operation, cloud_provider=None, cloud_service=None):
    return v0_operation


def cloud_api_operation_v1(v0_operation, cloud_provider=None, cloud_service=None):
    return f"{cloud_provider}.{cloud_service}.request"


def cloud_faas_operation_v0(v0_operation, cloud_provider=None, cloud_service=None):
    return v0_operation


def cloud_faas_operation_v1(v0_operation, cloud_provider=None, cloud_service=None):
    return f"{cloud_provider}.{cloud_service}.invoke"


def cloud_messaging_operation_v0(v0_operation, cloud_provider=None, cloud_service=None, direction=None):
    return v0_operation


def cloud_messaging_operation_v1(v0_operation, cloud_provider=None, cloud_service=None, direction=None):
    if direction == SpanDirection.INBOUND:
        return f"{cloud_provider}.{cloud_service}.receive"
    elif direction == SpanDirection.OUTBOUND:
        return f"{cloud_provider}.{cloud_service}.send"
    elif direction == SpanDirection.PROCESSING:
        return f"{cloud_provider}.{cloud_service}.process"


def messaging_operation_v0(v0_operation, provider=None, service=None, direction=None):
    return v0_operation


def messaging_operation_v1(v0_operation, provider=None, direction=None):
    if direction == SpanDirection.INBOUND:
        return f"{provider}.receive"
    elif direction == SpanDirection.OUTBOUND:
        return f"{provider}.send"
    elif direction == SpanDirection.PROCESSING:
        return f"{provider}.process"


def url_operation_v0(v0_operation, protocol=None, direction=None):
    return v0_operation


def url_operation_v1(v0_operation, protocol=None, direction=None):
    server_or_client = {SpanDirection.INBOUND: "server", SpanDirection.OUTBOUND: "client"}[direction]
    return f"{protocol}.{server_or_client}.request"


_SPAN_ATTRIBUTE_TO_FUNCTION = {
    "v0": {
        "cache_operation": cache_operation_v0,
        "cloud_api_operation": cloud_api_operation_v0,
        "cloud_faas_operation": cloud_faas_operation_v0,
        "cloud_messaging_operation": cloud_messaging_operation_v0,
        "database_operation": database_operation_v0,
        "messaging_operation": messaging_operation_v0,
        "service_name": service_name_v0,
        "url_operation": url_operation_v0,
    },
    "v1": {
        "cache_operation": cache_operation_v1,
        "cloud_api_operation": cloud_api_operation_v1,
        "cloud_faas_operation": cloud_faas_operation_v1,
        "cloud_messaging_operation": cloud_messaging_operation_v1,
        "database_operation": database_operation_v1,
        "messaging_operation": messaging_operation_v1,
        "service_name": service_name_v1,
        "url_operation": url_operation_v1,
    },
}

_inferred_base_service: Optional[str] = detect_service(sys.argv)

_DEFAULT_SPAN_SERVICE_NAMES: Dict[str, Optional[str]] = {
    "v0": _inferred_base_service or None,
    "v1": _inferred_base_service or DEFAULT_SERVICE_NAME,
}
