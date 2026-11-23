from enum import Enum
from typing import Callable
from typing import Optional
from typing import Protocol
from typing import TypedDict
from typing import overload


class SpanDirection(Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    PROCESSING = "processing"


def service_name_v0(v0_service_name: Optional[str]) -> Optional[str]:
    return v0_service_name


def service_name_v1(*_: object, **__: object) -> Optional[str]:
    from ddtrace.internal.settings._config import config as dd_config

    return dd_config.service


def database_operation_v0(v0_operation: str, database_provider: Optional[str] = None) -> str:
    return v0_operation


def database_operation_v1(v0_operation: str, database_provider: Optional[str] = None) -> str:
    operation = "query"
    return f"{database_provider}.{operation}"


def cache_operation_v0(v0_operation: str, cache_provider: Optional[str] = None) -> str:
    return v0_operation


def cache_operation_v1(v0_operation: str, cache_provider: Optional[str] = None) -> str:
    operation = "command"
    return f"{cache_provider}.{operation}"


def cloud_api_operation_v0(
    v0_operation: str, cloud_provider: Optional[str] = None, cloud_service: Optional[str] = None
) -> str:
    return v0_operation


def cloud_api_operation_v1(
    v0_operation: str, cloud_provider: Optional[str] = None, cloud_service: Optional[str] = None
) -> str:
    return f"{cloud_provider}.{cloud_service}.request"


def cloud_faas_operation_v0(
    v0_operation: str, cloud_provider: Optional[str] = None, cloud_service: Optional[str] = None
) -> str:
    return v0_operation


def cloud_faas_operation_v1(
    v0_operation: str, cloud_provider: Optional[str] = None, cloud_service: Optional[str] = None
) -> str:
    return f"{cloud_provider}.{cloud_service}.invoke"


def cloud_messaging_operation_v0(
    v0_operation: str,
    cloud_provider: Optional[str] = None,
    cloud_service: Optional[str] = None,
    direction: Optional[SpanDirection] = None,
) -> str:
    return v0_operation


def cloud_messaging_operation_v1(
    v0_operation: str,
    cloud_provider: Optional[str] = None,
    cloud_service: Optional[str] = None,
    direction: Optional[SpanDirection] = None,
) -> Optional[str]:
    if direction == SpanDirection.INBOUND:
        return f"{cloud_provider}.{cloud_service}.receive"
    elif direction == SpanDirection.OUTBOUND:
        return f"{cloud_provider}.{cloud_service}.send"
    elif direction == SpanDirection.PROCESSING:
        return f"{cloud_provider}.{cloud_service}.process"
    return None


def messaging_operation_v0(
    v0_operation: str,
    provider: Optional[str] = None,
    service: Optional[str] = None,
    direction: Optional[SpanDirection] = None,
) -> str:
    return v0_operation


@overload
def messaging_operation_v1(v0_operation: str, provider: Optional[str] = None, *, direction: SpanDirection) -> str: ...


@overload
def messaging_operation_v1(
    v0_operation: str, provider: Optional[str] = None, direction: Optional[SpanDirection] = None
) -> Optional[str]: ...


def messaging_operation_v1(
    v0_operation: str, provider: Optional[str] = None, direction: Optional[SpanDirection] = None
) -> Optional[str]:
    if direction == SpanDirection.INBOUND:
        return f"{provider}.receive"
    elif direction == SpanDirection.OUTBOUND:
        return f"{provider}.send"
    elif direction == SpanDirection.PROCESSING:
        return f"{provider}.process"
    return None


def url_operation_v0(
    v0_operation: str, protocol: Optional[str] = None, direction: Optional[SpanDirection] = None
) -> str:
    return v0_operation


def url_operation_v1(
    v0_operation: str, protocol: Optional[str] = None, direction: Optional[SpanDirection] = None
) -> str:
    directions: dict[Optional[SpanDirection], str] = {SpanDirection.INBOUND: "server", SpanDirection.OUTBOUND: "client"}
    server_or_client = directions[direction]
    return f"{protocol}.{server_or_client}.request"


class _MessagingOperation(Protocol):
    @overload
    def __call__(self, v0_operation: str, provider: Optional[str] = None, *, direction: SpanDirection) -> str: ...

    @overload
    def __call__(
        self, v0_operation: str, provider: Optional[str] = None, *, direction: Optional[SpanDirection] = None
    ) -> Optional[str]: ...


class _SpanAttributeFunctions(TypedDict):
    cache_operation: Callable[..., str]
    cloud_api_operation: Callable[..., str]
    cloud_faas_operation: Callable[..., str]
    cloud_messaging_operation: Callable[..., Optional[str]]
    database_operation: Callable[..., str]
    messaging_operation: _MessagingOperation
    service_name: Callable[..., Optional[str]]
    url_operation: Callable[..., str]


_SPAN_ATTRIBUTE_TO_FUNCTION: dict[str, _SpanAttributeFunctions] = {
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
