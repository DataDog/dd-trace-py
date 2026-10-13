import contextlib
from dataclasses import dataclass
from types import TracebackType
from typing import Any
from typing import ClassVar
from typing import Optional
from typing import cast

from ddtrace.appsec._asm_request_context import _get_asm_context
from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import open_rasp_subcontext_scope
from ddtrace.appsec._asm_request_context import should_analyze_body_response
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._metrics import report_rasp_skipped
from ddtrace.appsec._rasp import get_rasp_capability
from ddtrace.contrib._events.http import _HttpResponseWithStatusCode
from ddtrace.contrib._events.http_client import HttpClientEvents
from ddtrace.contrib._events.http_client import HttpClientRequestEvent
from ddtrace.internal import core
from ddtrace.internal.core.subscriber import ContextSubscriber


APPSEC_REQUESTS_CONTEXT_KEY = "appsec.requests.operation"


@dataclass
class _RequestsOperation:
    activation: object
    pending_url: Optional[str]
    use_body: bool


class AppSecRequestsSubscriber(ContextSubscriber[HttpClientRequestEvent]):
    auto_register = False
    event_names = (HttpClientEvents.REQUESTS_REQUEST.value,)
    activation: ClassVar[Optional[object]] = None

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext[HttpClientRequestEvent]) -> None:
        activation = cls.activation
        if activation is None or not get_rasp_capability("ssrf"):
            return

        url = ctx.event.request_url
        if not (isinstance(url, str) and url):
            return

        asm_context = _get_asm_context()
        if asm_context is None:
            report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SSRF, False)
            return

        ctx.set_item(
            APPSEC_REQUESTS_CONTEXT_KEY,
            _RequestsOperation(activation, url, should_analyze_body_response(asm_context)),
        )
        open_rasp_subcontext_scope(inherit=False)

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext[HttpClientRequestEvent],
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        # A later activation cannot finish an operation that was disabled in flight.
        operation = ctx.get_item(APPSEC_REQUESTS_CONTEXT_KEY)
        if cls.activation is None or operation is None or operation.activation is not cls.activation:
            return
        if exc_info[0] is not None or not get_rasp_capability("ssrf"):
            return

        response = ctx.event.response
        if response is None or response.__class__.__name__ != "Response":
            return
        response = cast(_HttpResponseWithStatusCode, response)
        if 300 <= response.status_code < 400:
            return

        addresses: dict[str, Any] = {
            "DOWN_RES_STATUS": str(response.status_code),
            "DOWN_RES_HEADERS": dict(response.headers),
        }
        if operation.use_body:
            with contextlib.suppress(Exception):
                addresses["DOWN_RES_BODY"] = response.json()
        call_waf_callback(addresses, rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES)


class AppSecRequestsSendSubscriber(ContextSubscriber[HttpClientRequestEvent]):
    auto_register = False
    event_names = (HttpClientEvents.HTTP_REQUEST.value,)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext[HttpClientRequestEvent]) -> None:
        if ctx.event.component != "requests" or not get_rasp_capability("ssrf"):
            return
        operation = ctx.find_item(APPSEC_REQUESTS_CONTEXT_KEY)
        if operation is None or operation.activation is not AppSecRequestsSubscriber.activation:
            return
        # Publish only inside the send: authentication can perform independent calls during preparation.
        # The first hop retains the original input; redirected hops use their own prepared URL.
        ctx.set_item("full_url", operation.pending_url or ctx.event.request_url)
        operation.pending_url = None
        ctx.set_item("use_body", operation.use_body)
