import contextlib
from dataclasses import dataclass
from http.client import HTTPConnection
from http.client import HTTPResponse
import json
from typing import Any
from urllib.parse import urlsplit
import weakref

from ddtrace.appsec._asm_request_context import SSRF_CLIENT_TRANSPORT
from ddtrace.appsec._asm_request_context import SSRF_REQUEST_ANALYZED
from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_active_asm_context
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import open_rasp_subcontext_scope
from ddtrace.appsec._asm_request_context import resume_rasp_subcontext_scope
from ddtrace.appsec._asm_request_context import should_analyze_body_response
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._http_utils import build_headers
from ddtrace.appsec._http_utils import parse_http_response_body
from ddtrace.appsec._rasp import _must_block
from ddtrace.appsec._rasp import get_rasp_capability
from ddtrace.contrib._events.http_client import HttpClientEvents
from ddtrace.contrib._events.http_client import HttpClientResponseEvent
from ddtrace.contrib._events.http_client import HttpClientSendEvent
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.core.subscriber import ContextSubscriber
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


@dataclass
class _PendingResponse:
    """What getresponse needs to finish analyzing a request that no client above http.client owns."""

    env: "weakref.ref[Any]"
    holder: Any
    use_body: bool


# Keyed weakly by connection: getresponse is a separate call, and a connection that is dropped without
# reading its response must not keep the subcontext alive.
_PENDING_RESPONSES: "weakref.WeakKeyDictionary[HTTPConnection, _PendingResponse]" = weakref.WeakKeyDictionary()


class AppSecHttpClientRequestSubscriber(ContextSubscriber[HttpClientSendEvent]):
    auto_register = False
    event_names = (HttpClientEvents.HTTPLIB_REQUEST.value,)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext[HttpClientSendEvent]) -> None:
        event = ctx.event
        connection = event.connection
        if _PENDING_RESPONSES and connection is not None:
            # A new request on a kept-alive connection supersedes a response that was never read.
            _PENDING_RESPONSES.pop(connection, None)
        if not get_rasp_capability("ssrf") or ctx.find_item(SSRF_REQUEST_ANALYZED):
            return
        env = get_active_asm_context()
        if env is None:
            return

        upstream_url = ctx.find_item("full_url")
        url = upstream_url if upstream_url is not None else event.request_url
        if not isinstance(url, str) or not url:
            return
        if not _carries_a_host(url):
            url = _absolute_downstream_url(connection, event.request_url or url)

        # A client transport above (urllib, urllib3, a requests hop) owns the subcontext and analyzes the
        # final response; otherwise this request is the whole operation.
        standalone = not ctx.find_item(SSRF_CLIENT_TRANSPORT)
        use_body = should_analyze_body_response(env) if standalone else bool(ctx.find_item("use_body"))
        holder = open_rasp_subcontext_scope(inherit=not standalone)
        headers = event.request_headers
        addresses = {
            EXPLOIT_PREVENTION.ADDRESS.SSRF: url,
            "DOWN_REQ_METHOD": event.request_method,
            "DOWN_REQ_HEADERS": headers,
        }
        content_type = headers.get("Content-Type") or headers.get("content-type")
        if use_body and content_type == "application/json":
            with contextlib.suppress(Exception):
                addresses["DOWN_REQ_BODY"] = json.loads(event.request_body())

        result = call_waf_callback(addresses, crop_trace="request", rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_REQ)
        env.downstream_requests += 1
        core.discard_item("full_url")
        if result and _must_block(result.actions):
            raise BlockingException(get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SSRF, url)
        if standalone and connection is not None:
            _PENDING_RESPONSES[connection] = _PendingResponse(weakref.ref(env), holder, use_body)


class AppSecHttpClientResponseSubscriber(Subscriber):
    auto_register = False
    event_names = (HttpClientEvents.HTTPLIB_RESPONSE.value,)

    @classmethod
    def on_event(cls, event: HttpClientResponseEvent[HTTPResponse]) -> None:
        if not get_rasp_capability("ssrf"):
            return
        env = get_active_asm_context()
        if env is None:
            return
        response = event.response
        if response.__class__.__name__ != "HTTPResponse":
            return
        try:
            redirect = 300 <= response.status < 400
            if not (_PENDING_RESPONSES or redirect):
                return
            connection = event.get_connection()
            if connection is None:
                return
            pending = _PENDING_RESPONSES.pop(connection, None)
            if pending is not None and pending.env() is env:
                _analyze_standalone_response(response, pending)
            elif redirect:
                # A client above follows redirects itself and only analyzes the final response.
                call_waf_callback(
                    {"DOWN_RES_STATUS": str(response.status), "DOWN_RES_HEADERS": build_headers(response.getheaders())},
                    rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES,
                )
        except Exception:
            log.debug("Error analyzing http.client response", exc_info=True)


def _analyze_standalone_response(response: HTTPResponse, pending: _PendingResponse) -> None:
    addresses: dict[str, Any] = {
        "DOWN_RES_STATUS": str(response.status),
        "DOWN_RES_HEADERS": build_headers(response.getheaders()),
    }
    if pending.use_body:
        body = parse_http_response_body(response)
        if body is not None:
            addresses["DOWN_RES_BODY"] = body
    with core.context_with_data("appsec.httplib.response"):
        resume_rasp_subcontext_scope(pending.holder)
        call_waf_callback(addresses, rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES)


def _carries_a_host(url: str) -> bool:
    """Whether the URL has an authority, structurally.

    Not a substring test: a "://" inside a query string is the shape of the very payload SSRF
    rules exist to catch, so treating it as absolute would skip the host on the worst requests.
    """
    try:
        return bool(urlsplit(url).netloc)
    except Exception:
        return False


def _absolute_downstream_url(connection: Any, path: str) -> str:
    """Rebuild an absolute URL from the connection when only a request path is available.

    SSRF is a decision about the host, so a bare path is not something the WAF can evaluate.
    """
    try:
        # A CONNECT tunnel puts the proxy in host/port; the request is really for the tunnel
        # target, so SSRF has to be judged against that instead.
        host = getattr(connection, "_tunnel_host", None)
        port = getattr(connection, "_tunnel_port", None) if host else None
        if not host:
            host, port = connection.host, connection.port
        scheme = "https" if connection.default_port == 443 else "http"
        if ":" in host:
            # http.client stores an IPv6 literal unbracketed, but a URL needs the brackets back or
            # the authority does not parse and the WAF skips the address entirely.
            host = f"[{host}]"
        netloc = host if port in (None, connection.default_port) else f"{host}:{port}"
        return f"{scheme}://{netloc}{path}"
    except Exception:
        return path
