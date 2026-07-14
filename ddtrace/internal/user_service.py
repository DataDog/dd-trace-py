from ddtrace.internal.telemetry import get_config as _get_config
from ddtrace.internal.utils.formats import parse_tags_str


def is_user_provided_service() -> bool:
    service = _get_config("DD_SERVICE")
    if not service:
        service = parse_tags_str(_get_config("DD_TAGS")).get("service")
    if not service:
        service = _get_config("OTEL_SERVICE_NAME")
    return service is not None
