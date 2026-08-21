from typing import TYPE_CHECKING
from typing import Optional

from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.internal.compat import ensure_binary
from ddtrace.internal.compat import ensure_text


if TYPE_CHECKING:
    from ddtrace.internal.settings.integration import IntegrationConfig


def httpx_url_to_str(url) -> str:
    """
    Helper to convert the httpx.URL parts from bytes to a str
    """
    scheme = url.raw_scheme
    host = url.raw_host
    port = url.port
    raw_path = url.raw_path
    url = scheme + b"://" + host
    if port is not None:
        url += b":" + ensure_binary(str(port))
    url += raw_path

    return ensure_text(url)


def httpx_get_service_name(request, integration_config: "IntegrationConfig") -> Optional[str]:
    if integration_config.split_by_domain:
        if hasattr(request.url, "netloc"):
            return ensure_text(request.url.netloc, errors="backslashreplace")

        service = ensure_binary(request.url.host)
        if request.url.port:
            service += b":" + ensure_binary(str(request.url.port))
        return ensure_text(service, errors="backslashreplace")
    return ext_service(None, integration_config)
