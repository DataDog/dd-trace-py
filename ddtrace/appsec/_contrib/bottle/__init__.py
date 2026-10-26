"""AppSec evaluation for Bottle's matched route callback lifecycle."""

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
from ddtrace.contrib._events.bottle import BottleBlockResponseEvent
from ddtrace.contrib._events.bottle import BottleRequestStartEvent
from ddtrace.contrib._events.bottle import BottleResponseEvent
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.core.subscriber import ContextSubscriber
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.utils.http import _get_blocked_template


def _check_blocked() -> None:
    if block := get_blocked():
        raise BlockingException(block)


def _block_request() -> None:
    if not get_blocked():
        set_blocked(Block_config())
    _check_blocked()


class BottleRequestStartSubscriber(Subscriber):
    auto_register = False
    event_names = (BottleRequestStartEvent.event_name,)

    @classmethod
    def on_event(cls, notification: BottleRequestStartEvent) -> None:
        if not asm_config._asm_enabled or get_active_asm_context() is None:
            return
        event = notification.request_event
        request = notification.request
        headers = {key.lower(): value for key, value in event.request_headers.items() if key.lower() != "cookie"}
        asm_request_context_set(
            remote_ip=request.environ.get("REMOTE_ADDR"),
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
            (SPAN_DATA_NAMES.REQUEST_COOKIES, dict(request.cookies)),
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


class BottleResponseSubscriber(Subscriber):
    auto_register = False
    event_names = (BottleResponseEvent.event_name,)

    @classmethod
    def on_event(cls, notification: BottleResponseEvent) -> None:
        if not asm_config._asm_enabled or get_active_asm_context() is None:
            return
        _check_blocked()
        event = notification.request_event
        if event.response_status_code is not None:
            _set_response_addresses(event.response_status_code, event.response_headers or {})
        call_waf_callback()
        _check_blocked()


class BottleBlockResponseSubscriber(Subscriber):
    auto_register = False
    event_names = (BottleBlockResponseEvent.event_name,)

    @classmethod
    def on_event(cls, event: BottleBlockResponseEvent) -> None:
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


class BottleRequestContextSubscriber(ContextSubscriber[WebFrameworkRequestEvent]):
    auto_register = False
    event_names = ("bottle.request",)

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext[WebFrameworkRequestEvent],
        exc_info: tuple[Optional[type[BaseException]], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        _on_context_ended(ctx, exc_info)


def listen() -> None:
    BottleRequestStartSubscriber.register()
    BottleResponseSubscriber.register()
    BottleBlockResponseSubscriber.register()
    BottleRequestContextSubscriber.register()
