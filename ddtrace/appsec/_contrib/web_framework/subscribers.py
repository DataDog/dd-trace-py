"""AppSec evaluation shared by synchronous web framework adapters."""

from collections.abc import Mapping
from types import TracebackType
from typing import Optional
from typing import cast
from urllib.parse import parse_qs

from ddtrace.appsec._asm_request_context import _on_context_ended
from ddtrace.appsec._asm_request_context import asm_request_context_set
from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_active_asm_context
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import set_blocked
from ddtrace.appsec._asm_request_context import set_waf_address
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.appsec._utils import Block_config
from ddtrace.contrib._events.web_framework import WebFrameworkBlockResponseEvent
from ddtrace.contrib._events.web_framework import WebFrameworkEvents
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib._events.web_framework import WebFrameworkRequestInspectEvent
from ddtrace.contrib._events.web_framework import WebFrameworkResponseInspectEvent
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.core.subscriber import ContextSubscriber
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.utils.http import _get_blocked_template


log = get_logger(__name__)


def _normalized_request_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Lowercase the request headers for the WAF, dropping cookies.

    Frameworks may decode header values lazily, so one malformed value can raise while the
    mapping is being consumed. That would abort request inspection for the whole request,
    so fall back to reading each header on its own and keep the rest of the payload.
    """
    try:
        return {key.lower(): value for key, value in headers.items() if key.lower() != "cookie"}
    except Exception:
        log.debug("error normalizing request headers, falling back to per-header reads", exc_info=True)

    normalized: dict[str, str] = {}
    try:
        keys = list(headers)
    except Exception:
        log.debug("error enumerating request headers", exc_info=True)
        return normalized

    for key in keys:
        lowered = key.lower()
        if lowered == "cookie":
            continue
        try:
            normalized[lowered] = headers[key]
        except UnicodeDecodeError as exc:
            # Keep the undecodable bytes visible to the WAF instead of dropping the header.
            normalized[lowered] = bytes(exc.object).decode(exc.encoding, "replace")
        except Exception:
            log.debug("error reading request header %r", key, exc_info=True)
    return normalized


def _check_blocked() -> None:
    if block := get_blocked():
        raise BlockingException(block)


def _block_request() -> None:
    if not get_blocked():
        set_blocked(Block_config())
    _check_blocked()


class WebFrameworkRequestInspectSubscriber(Subscriber):
    auto_register = False
    event_names = (WebFrameworkRequestInspectEvent.event_name,)

    @classmethod
    def on_event(cls, notification: WebFrameworkRequestInspectEvent) -> None:
        if not asm_config._asm_enabled or get_active_asm_context() is None:
            return
        event = notification.request_event
        headers = _normalized_request_headers(event.request_headers)
        asm_request_context_set(
            remote_ip=notification.peer_ip,
            headers=headers,
            headers_case_sensitive=False,
            block_request_callable=_block_request,
        )
        query = parse_qs(event.query or "", keep_blank_values=True)
        for address, value in (
            (SPAN_DATA_NAMES.REQUEST_METHOD, event.request_method),
            (SPAN_DATA_NAMES.REQUEST_URI_RAW, event.request_url),
            (SPAN_DATA_NAMES.REQUEST_ROUTE, event.request_route),
            (
                SPAN_DATA_NAMES.REQUEST_QUERY,
                {key: values[0] if len(values) == 1 else values for key, values in query.items()},
            ),
            (SPAN_DATA_NAMES.REQUEST_COOKIES, dict(notification.cookies)),
            (SPAN_DATA_NAMES.REQUEST_PATH_PARAMS, dict(notification.path_params)),
        ):
            if value is not None:
                set_waf_address(address, value)
        _check_blocked()
        call_waf_callback()
        _check_blocked()


def _set_response_addresses(status: int, headers: Mapping[str, str]) -> None:
    set_waf_address(SPAN_DATA_NAMES.RESPONSE_STATUS, str(status))
    set_waf_address(
        SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES,
        {key.lower(): value for key, value in headers.items() if key.lower() != "set-cookie"},
    )


class WebFrameworkResponseInspectSubscriber(Subscriber):
    auto_register = False
    event_names = (WebFrameworkResponseInspectEvent.event_name,)

    @classmethod
    def on_event(cls, notification: WebFrameworkResponseInspectEvent) -> None:
        if not asm_config._asm_enabled or get_active_asm_context() is None:
            return
        _check_blocked()
        event = notification.request_event
        if event.response_status_code is not None:
            _set_response_addresses(event.response_status_code, event.response_headers or {})
        call_waf_callback()
        _check_blocked()


class WebFrameworkBlockResponseSubscriber(Subscriber):
    auto_register = False
    event_names = (WebFrameworkBlockResponseEvent.event_name,)

    @classmethod
    def on_event(cls, event: WebFrameworkBlockResponseEvent) -> None:
        block = event.block
        if get_active_asm_context() is not None:
            set_blocked(cast(Block_config, block))
        if block.type == "none":
            body = b""
            headers = {"Content-Length": "0"}
            if block.location:
                headers["Location"] = block.location
        else:
            body = _get_blocked_template(block.content_type, block.block_id).encode("utf-8")
            headers = {"Content-Type": block.content_type, "Content-Length": str(len(body))}
        if get_active_asm_context() is not None:
            _set_response_addresses(block.status_code, headers)
        event.response = (block.status_code, headers, body)


class WebFrameworkRequestContextSubscriber(ContextSubscriber[WebFrameworkRequestEvent]):
    auto_register = False
    # Integrations that override the context name need their route here; cleanup is identical
    # for all of them, and a separate subclass would register a second listener name and run twice.
    event_names = (WebFrameworkEvents.WEB_REQUEST.value, "bottle.request")

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext[WebFrameworkRequestEvent],
        exc_info: tuple[Optional[type[BaseException]], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        _on_context_ended(ctx, exc_info)
