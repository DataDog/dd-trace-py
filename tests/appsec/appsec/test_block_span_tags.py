from types import SimpleNamespace
from unittest import mock

import pytest

from ddtrace import config
from ddtrace.appsec._contrib.fastapi import _asgi_make_block_content
from ddtrace.appsec._contrib.flask import _on_flask_blocked_request
from ddtrace.appsec._contrib.flask import _wsgi_make_block_content
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.ext import net
from ddtrace.internal import core
from ddtrace.trace import Span
from tests.utils import override_http_config


_URL = "http://localhost/blocked"
_QUERY = "a=b"
_USER_AGENT = "block-test/1.0"
_HEADERS = {"user-agent": _USER_AGENT}
_ENVIRON = {"REQUEST_METHOD": "GET", "QUERY_STRING": _QUERY}

_DATADOG_KEYS = (http.STATUS_CODE, http.METHOD, http.URL, http.QUERY_STRING, http.USER_AGENT)
_OTEL_KEYS = (
    http.OTEL_RESPONSE_STATUS_CODE,
    http.OTEL_REQUEST_METHOD,
    http.OTEL_URL_PATH,
    http.OTEL_URL_SCHEME,
    http.OTEL_URL_QUERY,
    http.OTEL_USER_AGENT_ORIGINAL,
)
# Block paths must not resolve client IPs or store referrer/security headers; those tags were
# never written by them before OTel semantics were introduced.
_NEVER_SET_KEYS = (
    http.CLIENT_IP,
    "network.client.ip",
    http.REFERRER_HOSTNAME,
    http.OTEL_CLIENT_ADDRESS,
    "network.peer.address",
)


def _new_span() -> Span:
    return Span("block.test", span_type=SpanTypes.WEB)


def _assert_block_tags(span: Span, otel_enabled: bool) -> None:
    for key in _NEVER_SET_KEYS:
        assert span.get_tag(key) is None, key

    if otel_enabled:
        assert span.get_metric(http.OTEL_RESPONSE_STATUS_CODE) == 403
        assert span.get_tag(http.OTEL_REQUEST_METHOD) == "GET"
        assert span.get_tag(http.OTEL_URL_PATH) == "/blocked"
        assert span.get_tag(http.OTEL_URL_SCHEME) == "http"
        assert span.get_tag(net.SERVER_ADDRESS) == "localhost"
        assert span.get_metric(net.SERVER_PORT) == 80
        assert span.get_tag(http.OTEL_URL_QUERY) == _QUERY
        assert span.get_tag(http.OTEL_USER_AGENT_ORIGINAL) == _USER_AGENT
        for key in _DATADOG_KEYS:
            assert span.get_tag(key) is None, key
    else:
        assert span.get_tag(http.STATUS_CODE) == "403"
        assert span.get_tag(http.METHOD) == "GET"
        assert str(span.get_tag(http.URL)).startswith(_URL)
        assert span.get_tag(http.QUERY_STRING) == _QUERY
        assert span.get_tag(http.USER_AGENT) == _USER_AGENT
        for key in _OTEL_KEYS:
            assert span.get_tag(key) is None, key


@pytest.mark.parametrize("otel_enabled", [False, True])
def test_flask_blocked_request_tags(otel_enabled):
    span = _new_span()
    request = SimpleNamespace(base_url=_URL, query_string=_QUERY, method="GET", headers=_HEADERS)
    with (
        mock.patch.object(config, "_otel_trace_semantics_enabled", otel_enabled),
        override_http_config("flask", {"trace_query_string": True}),
        core.context_with_data("flask.block.test", flask_request=request, flask_config=config.flask),
    ):
        _on_flask_blocked_request(span)

    _assert_block_tags(span, otel_enabled)


@pytest.mark.parametrize("otel_enabled", [False, True])
def test_flask_blocked_request_tags_bytes_query_string(otel_enabled):
    # Werkzeug exposes request.query_string as bytes.
    span = _new_span()
    request = SimpleNamespace(base_url=_URL, query_string=_QUERY.encode(), method="GET", headers=_HEADERS)
    with (
        mock.patch.object(config, "_otel_trace_semantics_enabled", otel_enabled),
        override_http_config("flask", {"trace_query_string": True}),
        core.context_with_data("flask.block.test", flask_request=request, flask_config=config.flask),
    ):
        _on_flask_blocked_request(span)

    query_key = http.OTEL_URL_QUERY if otel_enabled else http.QUERY_STRING
    assert span.get_tag(query_key) in (_QUERY, _QUERY.encode())
    _assert_block_tags(span, otel_enabled)


@pytest.mark.parametrize("otel_enabled", [False, True])
def test_flask_blocked_request_without_query_string(otel_enabled):
    # Flask sets URL tags on a blocked request only when a query string is present; the regular
    # request-start tagging covers the no-query case.
    span = _new_span()
    request = SimpleNamespace(base_url=_URL, query_string=b"", method="GET", headers=_HEADERS)
    with (
        mock.patch.object(config, "_otel_trace_semantics_enabled", otel_enabled),
        override_http_config("flask", {"trace_query_string": True}),
        core.context_with_data("flask.block.test", flask_request=request, flask_config=config.flask),
    ):
        _on_flask_blocked_request(span)

    if otel_enabled:
        assert span.get_metric(http.OTEL_RESPONSE_STATUS_CODE) == 403
        assert span.get_tag(http.OTEL_REQUEST_METHOD) == "GET"
        assert span.get_tag(http.OTEL_USER_AGENT_ORIGINAL) == _USER_AGENT
        for key in _DATADOG_KEYS:
            assert span.get_tag(key) is None, key
    else:
        assert span.get_tag(http.STATUS_CODE) == "403"
        assert span.get_tag(http.METHOD) == "GET"
        assert span.get_tag(http.USER_AGENT) == _USER_AGENT


@pytest.mark.parametrize("otel_enabled", [False, True])
def test_flask_blocked_request_tags_keep_status_when_request_unreadable(otel_enabled):
    span = _new_span()
    with (
        mock.patch.object(config, "_otel_trace_semantics_enabled", otel_enabled),
        core.context_with_data("flask.block.test", flask_request=None, flask_config=config.flask),
    ):
        _on_flask_blocked_request(span)

    if otel_enabled:
        assert span.get_metric(http.OTEL_RESPONSE_STATUS_CODE) == 403
    else:
        assert span.get_tag(http.STATUS_CODE) == "403"


@pytest.mark.parametrize("otel_enabled", [False, True])
def test_wsgi_block_content_tags(otel_enabled):
    span = _new_span()
    middleware = SimpleNamespace(_config=config.flask)
    items = {"middleware": middleware, "req_span": span, "headers": _HEADERS, "environ": _ENVIRON}
    ctx = mock.Mock(get_item=items.get)
    with (
        mock.patch.object(config, "_otel_trace_semantics_enabled", otel_enabled),
        override_http_config("flask", {"trace_query_string": True}),
    ):
        status, _, _ = _wsgi_make_block_content(ctx, lambda environ: _URL)

    assert status == 403
    _assert_block_tags(span, otel_enabled)


@pytest.mark.parametrize("otel_enabled", [False, True])
def test_asgi_block_content_tags(otel_enabled):
    span = _new_span()
    middleware = SimpleNamespace(_config=config.fastapi, integration_config=config.fastapi)
    items = {"middleware": middleware, "req_span": span, "headers": _HEADERS, "environ": _ENVIRON}
    ctx = mock.Mock(get_item=items.get)
    with (
        mock.patch.object(config, "_otel_trace_semantics_enabled", otel_enabled),
        override_http_config("fastapi", {"trace_query_string": True}),
    ):
        status, _, _ = _asgi_make_block_content(ctx, _URL)

    assert status == 403
    _assert_block_tags(span, otel_enabled)
