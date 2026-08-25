from typing import TYPE_CHECKING
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Optional

from wrapt import wrap_function_wrapper as _w

from ddtrace.contrib._events.http_client import HttpClientRequestEvent
from ddtrace.contrib._events.http_client import HttpClientSendEvent
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.internal import core
from ddtrace.internal.compat import ensure_binary
from ddtrace.internal.compat import ensure_text
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.wrappers import unwrap as _u


if TYPE_CHECKING:
    from ddtrace.internal.settings.integration import IntegrationConfig


# Keep this module independent of the optional httpx and httpx2 packages and
# product code. Each integration passes its library module and configuration explicitly.
class HttpxPatcher:
    def __init__(
        self,
        module: Any,
        integration_config: "IntegrationConfig",
        request_event_name: Optional[str] = None,
        send_event_name: Optional[str] = None,
    ) -> None:
        self._module = module
        self._integration_config = integration_config
        self._request_event_name = request_event_name
        self._send_event_name = send_event_name

    def _wrapped_sync_send_single_request(
        self,
        wrapped: Callable[..., Any],
        instance: Any,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        request = get_argument_value(args, kwargs, 0, "request")

        with core.context_with_event(
            event=HttpClientSendEvent(
                request_url=httpx_url_to_str(request.url),
                request_method=request.method,
                request_headers=request.headers,
                request_body=lambda: request.content,
            ),
            context_name_override=self._send_event_name,
        ) as ctx:
            response = None
            try:
                response = wrapped(*args, **kwargs)
                return response
            finally:
                if response is not None:
                    ctx.event.set_response(response)

    async def _wrapped_async_send_single_request(
        self,
        wrapped: Callable[..., Awaitable[Any]],
        instance: Any,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        request = get_argument_value(args, kwargs, 0, "request")

        with core.context_with_event(
            event=HttpClientSendEvent(
                request_url=httpx_url_to_str(request.url),
                request_method=request.method,
                request_headers=request.headers,
                request_body=lambda: request.content,
            ),
            context_name_override=self._send_event_name,
        ) as ctx:
            response = None
            try:
                response = await wrapped(*args, **kwargs)
                return response
            finally:
                if response is not None:
                    ctx.event.set_response(response)

    async def _wrapped_async_send(
        self,
        wrapped: Callable[..., Awaitable[Any]],
        instance: Any,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        request = get_argument_value(args, kwargs, 0, "request")

        with core.context_with_event(
            HttpClientRequestEvent(
                http_operation="http.request",
                service=httpx_get_service_name(request, self._integration_config),
                component=self._integration_config.integration_name,
                request_method=request.method,
                request_headers=request.headers,
                integration_config=self._integration_config,
                request_url=httpx_url_to_str(request.url),
                query=ensure_text(request.url.query),
                target_host=request.url.host,
            ),
            context_name_override=self._request_event_name,
        ) as ctx:
            response = None
            try:
                response = await wrapped(*args, **kwargs)
                return response
            finally:
                if response is not None:
                    ctx.event.set_response(response)

    def _wrapped_sync_send(
        self,
        wrapped: Callable[..., Any],
        instance: Any,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        request = get_argument_value(args, kwargs, 0, "request")

        with core.context_with_event(
            HttpClientRequestEvent(
                component=self._integration_config.integration_name,
                http_operation="http.request",
                service=httpx_get_service_name(request, self._integration_config),
                request_method=request.method,
                request_headers=request.headers,
                integration_config=self._integration_config,
                request_url=httpx_url_to_str(request.url),
                query=ensure_text(request.url.query),
                target_host=request.url.host,
            ),
            context_name_override=self._request_event_name,
        ) as ctx:
            response = None
            try:
                response = wrapped(*args, **kwargs)
                return response
            finally:
                if response is not None:
                    ctx.event.set_response(response)

    def patch(self) -> None:
        if getattr(self._module, "_datadog_patch", False):
            return

        self._module._datadog_patch = True

        _w(self._module.Client, "send", self._wrapped_sync_send)
        _w(self._module.AsyncClient, "send", self._wrapped_async_send)
        _w(self._module.Client, "_send_single_request", self._wrapped_sync_send_single_request)
        _w(self._module.AsyncClient, "_send_single_request", self._wrapped_async_send_single_request)

    def unpatch(self) -> None:
        if not getattr(self._module, "_datadog_patch", False):
            return

        self._module._datadog_patch = False

        _u(self._module.AsyncClient, "send")
        _u(self._module.Client, "send")
        _u(self._module.Client, "_send_single_request")
        _u(self._module.AsyncClient, "_send_single_request")


def httpx_url_to_str(url: Any) -> str:
    """Convert URL byte components to a string."""
    scheme = url.raw_scheme
    host = url.raw_host
    port = url.port
    raw_path = url.raw_path
    url = scheme + b"://" + host
    if port is not None:
        url += b":" + ensure_binary(str(port))
    url += raw_path

    return ensure_text(url)


def httpx_get_service_name(request: Any, integration_config: "IntegrationConfig") -> Optional[str]:
    if integration_config.split_by_domain:
        if hasattr(request.url, "netloc"):
            return ensure_text(request.url.netloc, errors="backslashreplace")

        service = ensure_binary(request.url.host)
        if request.url.port:
            service += b":" + ensure_binary(str(request.url.port))
        return ensure_text(service, errors="backslashreplace")
    return ext_service(None, integration_config)
