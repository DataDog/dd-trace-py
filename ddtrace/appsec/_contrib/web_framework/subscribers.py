"""AppSec inspection and blocking for the shared web framework protocol."""

from typing import Any
from typing import Union
from urllib.parse import parse_qs

from ddtrace.appsec import _asm_request_context as asm
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.appsec._utils import Block_config
from ddtrace.contrib._events.web_framework import WebFrameworkBlockResponseEvent
from ddtrace.contrib._events.web_framework import WebFrameworkRequestInspectEvent
from ddtrace.contrib._events.web_framework import WebFrameworkResponseInspectEvent
from ddtrace.contrib.internal.trace_utils import _get_request_header_client_ip
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.utils.http import _get_blocked_template


InspectionEvent = Union[
    WebFrameworkRequestInspectEvent, WebFrameworkResponseInspectEvent, WebFrameworkBlockResponseEvent
]


def _check_blocked() -> None:
    if block := asm.get_blocked():
        raise BlockingException(block)


def _block_request() -> None:
    # SDK blocking may be requested without a preceding WAF decision.
    if not asm.get_blocked():
        asm.set_blocked(Block_config())
    _check_blocked()


class AppSecWebFrameworkSubscriber(Subscriber):
    """Evaluate shared inspection events before the transport commits a response.

    The span processor owns the existing ASM context. These listeners neither
    create a request context nor finalize one, including for deferred responses.
    """

    auto_register = False
    event_names = (
        WebFrameworkRequestInspectEvent.event_name,
        WebFrameworkResponseInspectEvent.event_name,
        WebFrameworkBlockResponseEvent.event_name,
    )

    @classmethod
    def on_event(cls, notification: InspectionEvent) -> None:
        if not asm_config._asm_enabled:
            return
        if isinstance(notification, WebFrameworkBlockResponseEvent):
            block = notification.block
            if block.type == "none":
                body = b""
                headers = {"Content-Type": "text/plain; charset=utf-8", "Location": block.location}
            else:
                body = _get_blocked_template(block.content_type, block.block_id).encode("utf-8")
                headers = {"Content-Type": block.content_type}
            headers["Content-Length"] = str(len(body))
            notification.response = (block.status_code, headers, body)
            if asm.get_active_asm_context() is not None:
                asm.set_waf_address(SPAN_DATA_NAMES.RESPONSE_STATUS, str(block.status_code))
                asm.set_waf_address(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES, headers)
            return

        environment = asm.get_active_asm_context()
        if environment is None:
            return
        event = notification.request_event
        addresses: tuple[tuple[str, Any], ...]
        if isinstance(notification, WebFrameworkRequestInspectEvent):
            previous_block = environment.block_callable

            def restore_block_callable() -> None:
                environment.block_callable = previous_block

            notification.cleanup_callbacks.append(restore_block_callable)
            asm.set_block_request_callable(_block_request)
            client_ip = (
                _get_request_header_client_ip(event.request_headers, notification.peer_ip, event.headers_case_sensitive)
                or notification.peer_ip
            )
            asm.asm_request_context_set(client_ip, event.request_headers, event.headers_case_sensitive)
            addresses = (
                (SPAN_DATA_NAMES.REQUEST_URI_RAW, event.request_url),
                (SPAN_DATA_NAMES.REQUEST_METHOD, event.request_method),
                (SPAN_DATA_NAMES.REQUEST_ROUTE, event.request_route),
                (SPAN_DATA_NAMES.REQUEST_QUERY, parse_qs(event.query or "", keep_blank_values=True)),
                (SPAN_DATA_NAMES.REQUEST_COOKIES, notification.cookies),
                (SPAN_DATA_NAMES.REQUEST_PATH_PARAMS, notification.path_params),
            )
        else:
            addresses = (
                (
                    SPAN_DATA_NAMES.RESPONSE_STATUS,
                    str(event.response_status_code) if event.response_status_code is not None else None,
                ),
                (SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES, event.response_headers),
            )
        for address, value in addresses:
            if value is not None:
                asm.set_waf_address(address, value)
        # An earlier IP check or SDK call may already have made the decision.
        _check_blocked()
        asm.call_waf_callback()
        _check_blocked()
