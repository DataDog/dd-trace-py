import contextlib
from http.client import HTTPResponse
import json
from typing import Any
from urllib.parse import urlsplit

from ddtrace.appsec._asm_request_context import SSRF_REQUEST_ANALYZED
from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_active_asm_context
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import open_rasp_subcontext_scope
from ddtrace.appsec._asm_request_context import should_analyze_body_response
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._http_utils import build_headers
from ddtrace.appsec._rasp import _must_block
from ddtrace.appsec._rasp import get_rasp_capability
from ddtrace.contrib._events.http_client import HttpClientEvents
from ddtrace.contrib._events.http_client import HttpClientResponseEvent
from ddtrace.contrib._events.http_client import HttpClientSendEvent
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.core.subscriber import ContextSubscriber
from ddtrace.internal.core.subscriber import Subscriber


class AppSecHttpClientRequestSubscriber(ContextSubscriber[HttpClientSendEvent]):
    auto_register = False
    event_names = (HttpClientEvents.HTTPLIB_REQUEST.value,)

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext[HttpClientSendEvent]) -> None:
        if not get_rasp_capability("ssrf") or ctx.find_item(SSRF_REQUEST_ANALYZED):
            return
        env = get_active_asm_context()
        if env is None:
            return

        event = ctx.event
        upstream_url = ctx.find_item("full_url")
        url = upstream_url if upstream_url is not None else event.request_url
        if not isinstance(url, str) or not url:
            return
        if not _carries_a_host(url):
            url = _absolute_downstream_url(event.connection, event.request_url or url)

        use_body = ctx.find_item("use_body") if upstream_url is not None else None
        if use_body is None:
            use_body = should_analyze_body_response(env)
        open_rasp_subcontext_scope(inherit=upstream_url is not None)
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


class AppSecHttpClientResponseSubscriber(Subscriber):
    auto_register = False
    event_names = (HttpClientEvents.HTTPLIB_RESPONSE.value,)

    @classmethod
    def on_event(cls, event: HttpClientResponseEvent[HTTPResponse]) -> None:
        if not get_rasp_capability("ssrf") or get_active_asm_context() is None:
            return
        response = event.response
        if response.__class__.__name__ != "HTTPResponse":
            return
        status = response.getcode()
        if 300 <= status < 400:
            call_waf_callback(
                {"DOWN_RES_STATUS": str(status), "DOWN_RES_HEADERS": build_headers(response.getheaders())},
                rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES,
            )


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
