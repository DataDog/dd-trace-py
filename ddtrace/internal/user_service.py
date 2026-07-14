from ddtrace.internal.getconfig import get_config
from ddtrace.internal.utils.formats import parse_tags_str


def is_user_provided_service() -> bool:
    service = get_config("DD_SERVICE")
    if not service:
        service = parse_tags_str(get_config("DD_TAGS")).get("service")
    if not service:
        service = get_config("OTEL_SERVICE_NAME")
    return service is not None
