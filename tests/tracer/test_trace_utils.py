# -*- coding: utf-8 -*-
from ipaddress import ip_network

from hypothesis import given
from hypothesis.strategies import booleans
from hypothesis.strategies import dictionaries
from hypothesis.strategies import floats
from hypothesis.strategies import lists
from hypothesis.strategies import none
from hypothesis.strategies import recursive
from hypothesis.strategies import text
from hypothesis.strategies import tuples
import mock
import pytest

from ddtrace import Pin
from ddtrace import Tracer
from ddtrace import config
from ddtrace._trace.context import Context
from ddtrace._trace.span import Span
from ddtrace.appsec._constants import IAST
from ddtrace.contrib import trace_utils
from ddtrace.contrib.trace_utils import _get_request_header_client_ip
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.ext import net
from ddtrace.internal import core
from ddtrace.internal.compat import ensure_text
from ddtrace.propagation.http import HTTP_HEADER_PARENT_ID
from ddtrace.propagation.http import HTTP_HEADER_TRACE_ID
from ddtrace.settings import Config
from ddtrace.settings import IntegrationConfig
from tests.utils import override_global_config


@pytest.fixture
def int_config():
    c = Config()
    c._add("myint", dict())
    return c


@pytest.fixture
def span(tracer):
    span = tracer.trace(name="myint")
    yield span
    span.finish()


class TestHeaders(object):
    @pytest.fixture()
    def span(self):
        yield Span("some_span")

    @pytest.fixture()
    def config(self):
        yield Config()

    @pytest.fixture()
    def integration_config(self, config):
        yield IntegrationConfig(config, "test")

    def test_it_does_not_break_if_no_headers(self, span, integration_config):
        trace_utils._store_request_headers(None, span, integration_config)
        trace_utils._store_response_headers(None, span, integration_config)

    def test_it_does_not_break_if_headers_are_not_a_dict(self, span, integration_config):
        trace_utils._store_request_headers(list(), span, integration_config)
        trace_utils._store_response_headers(list(), span, integration_config)

    def test_it_accept_headers_as_list_of_tuples(self, span, integration_config):
        """
        :type span: Span
        :type integration_config: IntegrationConfig
        """
        integration_config.http.trace_headers(["Content-Type", "Max-Age"])
        trace_utils._store_request_headers([("Content-Type", "some;value;content-type")], span, integration_config)
        assert span.get_tag("http.request.headers.content-type") == "some;value;content-type"
        assert None is span.get_tag("http.request.headers.other")

    def test_store_multiple_request_headers_as_dict(self, span, integration_config):
        """
        :type span: Span
        :type integration_config: IntegrationConfig
        """
        integration_config.http.trace_headers(["Content-Type", "Max-Age"])
        trace_utils._store_request_headers(
            {
                "Content-Type": "some;value;content-type",
                "Max-Age": "some;value;max_age",
                "Other": "some;value;other",
            },
            span,
            integration_config,
        )
        assert span.get_tag("http.request.headers.content-type") == "some;value;content-type"
        assert span.get_tag("http.request.headers.max-age") == "some;value;max_age"
        assert None is span.get_tag("http.request.headers.other")

    def test_store_multiple_response_headers_as_dict(self, span, integration_config):
        """
        :type span: Span
        :type integration_config: IntegrationConfig
        """
        integration_config.http.trace_headers(["Content-Type", "Max-Age"])
        trace_utils._store_response_headers(
            {
                "Content-Type": "some;value;content-type",
                "Max-Age": "some;value;max_age",
                "Other": "some;value;other",
            },
            span,
            integration_config,
        )
        assert span.get_tag("http.response.headers.content-type") == "some;value;content-type"
        assert span.get_tag("http.response.headers.max-age") == "some;value;max_age"
        assert None is span.get_tag("http.response.headers.other")

    def test_numbers_in_headers_names_are_allowed(self, span, integration_config):
        """
        :type span: Span
        :type integration_config: IntegrationConfig
        """
        integration_config.http.trace_headers("Content-Type123")
        trace_utils._store_response_headers(
            {
                "Content-Type123": "some;value",
            },
            span,
            integration_config,
        )
        assert span.get_tag("http.response.headers.content-type123") == "some;value"

    def test_allowed_chars_not_replaced_in_tag_name(self, span, integration_config):
        """
        :type span: Span
        :type integration_config: IntegrationConfig
        """
        # See: https://docs.datadoghq.com/tagging/#defining-tags
        integration_config.http.trace_headers("C0n_t:e/nt-Type")
        trace_utils._store_response_headers(
            {
                "C0n_t:e/nt-Type": "some;value",
            },
            span,
            integration_config,
        )
        assert span.get_tag("http.response.headers.c0n_t:e/nt-type") == "some;value"

    def test_period_is_replaced_by_underscore(self, span, integration_config):
        """
        :type span: Span
        :type integration_config: IntegrationConfig
        """
        # Deviation from https://docs.datadoghq.com/tagging/#defining-tags in order to allow
        # consistent representation of headers having the period in the name.
        integration_config.http.trace_headers("api.token")
        trace_utils._store_response_headers(
            {
                "api.token": "some;value",
            },
            span,
            integration_config,
        )
        assert span.get_tag("http.response.headers.api_token") == "some;value"

    def test_non_allowed_chars_replaced(self, span, integration_config):
        """
        :type span: Span
        :type integration_config: IntegrationConfig
        """
        # See: https://docs.datadoghq.com/tagging/#defining-tags
        integration_config.http.trace_headers("C!#ontent-Type")
        trace_utils._store_response_headers(
            {
                "C!#ontent-Type": "some;value",
            },
            span,
            integration_config,
        )
        assert span.get_tag("http.response.headers.c__ontent-type") == "some;value"

    def test_key_trim_leading_trailing_spaced(self, span, integration_config):
        """
        :type span: Span
        :type integration_config: IntegrationConfig
        """
        integration_config.http.trace_headers("Content-Type")
        trace_utils._store_response_headers(
            {
                "   Content-Type   ": "some;value",
            },
            span,
            integration_config,
        )
        assert span.get_tag("http.response.headers.content-type") == "some;value"

    def test_value_not_trim_leading_trailing_spaced(self, span, integration_config):
        """
        :type span: Span
        :type integration_config: IntegrationConfig
        """
        integration_config.http.trace_headers("Content-Type")
        trace_utils._store_response_headers(
            {
                "Content-Type": "   some;value   ",
            },
            span,
            integration_config,
        )
        assert span.get_tag("http.response.headers.content-type") == "   some;value   "

    def test_no_whitelist(self, span, integration_config):
        """
        :type span: Span
        :type integration_config: IntegrationConfig
        """
        trace_utils._store_response_headers(
            {
                "Content-Type": "some;value",
            },
            span,
            integration_config,
        )
        assert span.get_tag("http.response.headers.content-type") is None

    def test_whitelist_exact(self, span, integration_config):
        """
        :type span: Span
        :type integration_config: IntegrationConfig
        """
        integration_config.http.trace_headers("content-type")
        trace_utils._store_response_headers(
            {
                "Content-Type": "some;value",
            },
            span,
            integration_config,
        )
        assert span.get_tag("http.response.headers.content-type") == "some;value"

    def test_whitelist_case_insensitive(self, span, integration_config):
        """
        :type span: Span
        :type integration_config: IntegrationConfig
        """
        integration_config.http.trace_headers("CoNtEnT-tYpE")
        trace_utils._store_response_headers(
            {
                "cOnTeNt-TyPe": "some;value",
            },
            span,
            integration_config,
        )
        assert span.get_tag("http.response.headers.content-type") == "some;value"


@pytest.mark.parametrize(
    "pin,config_val,default,global_service,expected",
    [
        (Pin(), None, None, None, None),
        (Pin(), None, None, "global-svc", "global-svc"),
        (Pin(), None, "default-svc", None, "default-svc"),
        # Global service should have higher priority than the integration default.
        (Pin(), None, "default-svc", "global-svc", "global-svc"),
        (Pin(), "config-svc", "default-svc", None, "config-svc"),
        (Pin(service="pin-svc"), None, "default-svc", None, "pin-svc"),
        (Pin(service="pin-svc"), "config-svc", "default-svc", None, "pin-svc"),
    ],
)
def test_int_service(int_config, pin, config_val, default, global_service, expected):
    if config_val:
        int_config.myint.service = config_val

    if global_service:
        int_config.service = global_service

    assert trace_utils.int_service(pin, int_config.myint, default) == expected


def test_int_service_integration(int_config):
    pin = Pin()
    tracer = Tracer()
    assert trace_utils.int_service(pin, int_config.myint) is None

    with override_global_config(dict(service="global-svc")):
        assert trace_utils.int_service(pin, int_config.myint) is None

        with tracer.trace("something", service=trace_utils.int_service(pin, int_config.myint)) as s:
            assert s.service == "global-svc"


@pytest.mark.parametrize(
    "pin,config_val,default,expected",
    [
        (Pin(), None, "default-svc", "default-svc"),
        (Pin(), "config-svc", "default-svc", "config-svc"),
        (Pin(service="pin-svc"), None, "default-svc", "pin-svc"),
        (Pin(service="pin-svc"), "config-svc", "default-svc", "pin-svc"),
    ],
)
def test_ext_service(int_config, pin, config_val, default, expected):
    if config_val:
        int_config.myint.service = config_val

    assert trace_utils.ext_service(pin, int_config.myint, default) == expected


@pytest.mark.subprocess(
    parametrize={
        "DD_TRACE_HEADER_TAGS": ["header1 header2 header3:third-header", "header1,header2,header3:third-header"]
    }
)
def test_set_http_meta_with_http_header_tags_config():
    from ddtrace import config
    from ddtrace._trace.span import Span
    from ddtrace.contrib.trace_utils import set_http_meta

    assert config.trace_http_header_tags == {
        "header1": "",
        "header2": "",
        "header3": "third-header",
    }, config.trace_http_header_tags
    integration_config = config.new_integration
    assert integration_config.is_header_tracing_configured

    # test request headers
    request_span = Span(name="new_integration.request")
    set_http_meta(
        request_span,
        integration_config,
        request_headers={"header1": "value1", "header2": "value2", "header3": "value3"},
    )
    assert request_span.get_tag("http.request.headers.header1") == "value1"
    assert request_span.get_tag("http.request.headers.header2") == "value2"
    assert request_span.get_tag("third-header") == "value3"

    # test response headers
    response_span = Span(name="new_integration.response")
    set_http_meta(
        response_span,
        integration_config,
        response_headers={"header1": "value1", "header2": "value2", "header3": "value3"},
    )
    assert response_span.get_tag("http.response.headers.header1") == "value1"
    assert response_span.get_tag("http.response.headers.header2") == "value2"
    assert response_span.get_tag("third-header") == "value3"


@pytest.mark.parametrize("appsec_enabled", [False, True])
@pytest.mark.parametrize("span_type", [SpanTypes.WEB, SpanTypes.HTTP, None])
@pytest.mark.parametrize(
    "method,url,status_code,status_msg,query,request_headers,response_headers,uri,path_params,cookies,target_host",
    [
        ("GET", "http://localhost/", 0, None, None, None, None, None, None, None, "localhost"),
        ("GET", "http://localhost/", 200, "OK", None, None, None, None, None, None, "localhost"),
        (None, None, None, None, None, None, None, None, None, None, None),
        (
            "GET",
            "http://localhost/",
            200,
            "OK",
            None,
            {"my-header": "value1"},
            {"resp-header": "val"},
            "http://localhost/",
            None,
            None,
            "localhost",
        ),
        (
            "GET",
            "http://localhost/",
            200,
            "OK",
            "q=test+query&q2=val",
            {"my-header": "value1"},
            {"resp-header": "val"},
            "http://localhost/search?q=test+query&q2=val",
            {"id": "val", "name": "vlad"},
            None,
            "localhost",
        ),
        ("GET", "http://user:pass@localhost/", 0, None, None, None, None, None, None, None, None),
        ("GET", "http://user@localhost/", 0, None, None, None, None, None, None, None, None),
        ("GET", "http://user:pass@localhost/api?q=test", 0, None, None, None, None, None, None, None, None),
        ("GET", "http://localhost/api@test", 0, None, None, None, None, None, None, None, None),
        ("GET", "http://localhost/?api@test", 0, None, None, None, None, None, None, None, None),
    ],
)
def test_set_http_meta(
    span,
    int_config,
    method,
    url,
    target_host,
    status_code,
    status_msg,
    query,
    request_headers,
    response_headers,
    uri,
    path_params,
    cookies,
    appsec_enabled,
    span_type,
):
    int_config.http.trace_headers(["my-header"])
    int_config.trace_query_string = True
    span.span_type = span_type
    with override_global_config({"_asm_enabled": appsec_enabled}):
        trace_utils.set_http_meta(
            span,
            int_config,
            method=method,
            url=url,
            target_host=target_host,
            status_code=status_code,
            status_msg=status_msg,
            query=query,
            raw_uri=uri,
            request_headers=request_headers,
            response_headers=response_headers,
            request_cookies=cookies,
            request_path_params=path_params,
        )
    if method is not None:
        assert span.get_tag(http.METHOD) == method
    else:
        assert http.METHOD not in span.get_tags()

    if target_host is not None:
        assert span.get_tag(net.TARGET_HOST) == target_host
    else:
        assert net.TARGET_HOST not in span.get_tags()

    if url is not None:
        if url.startswith("http://user"):
            # Remove any userinfo that may be in the original url
            expected_url = url[: url.index(":")] + "://" + url[url.index("@") + 1 :]
        else:
            expected_url = url

        if query and int_config.trace_query_string:
            assert span.get_tag(http.URL) == str(expected_url + "?" + query)
        else:
            assert span.get_tag(http.URL) == str(expected_url)
    else:
        assert http.URL not in span.get_tags()

    if status_code is not None:
        assert span.get_tag(http.STATUS_CODE) == str(status_code)
        if 500 <= int(status_code) < 600:
            assert span.error == 1
        else:
            assert span.error == 0
    else:
        assert http.STATUS_CODE not in span.get_tags()

    if status_msg is not None:
        assert span.get_tag(http.STATUS_MSG) == str(status_msg)

    if query is not None and int_config.trace_query_string:
        assert span.get_tag(http.QUERY_STRING) == query

    if request_headers is not None:
        for header, value in request_headers.items():
            tag = "http.request.headers." + header
            assert span.get_tag(tag) == value

    if appsec_enabled and span.span_type == SpanTypes.WEB:
        if uri is not None:
            assert core.get_item("http.request.uri", span=span) == uri
        if method is not None:
            assert core.get_item("http.request.method", span=span) == method
        if request_headers is not None:
            assert core.get_item("http.request.headers", span=span) == request_headers
        if response_headers is not None:
            assert core.get_item("http.response.headers", span=span) == response_headers
        if path_params is not None:
            assert core.get_item("http.request.path_params", span=span) == path_params


@mock.patch("ddtrace.settings.config.log")
@pytest.mark.parametrize(
    "error_codes,status_code,error,log_call",
    [
        ("404-400", 400, 1, None),
        ("400-404", 400, 1, None),
        ("400-404", 500, 0, None),
        ("500-520", 530, 0, None),
        ("500-550         ", 530, 1, None),
        ("400-404,419", 419, 1, None),
        ("400,401,403", 401, 1, None),
        ("400-404,X", 0, 0, ("Error status codes was not a number %s", ["X"])),
        ("500-599", 200, 0, None),
    ],
)
def test_set_http_meta_custom_errors(mock_log, span, int_config, error_codes, status_code, error, log_call):
    config.http_server.error_statuses = error_codes
    trace_utils.set_http_meta(span, int_config, status_code=status_code)
    assert span.error == error
    if log_call:
        mock_log.exception.assert_called_once_with(*log_call)
    else:
        mock_log.exception.assert_not_called()


@mock.patch("ddtrace.contrib.trace_utils._store_headers")
def test_set_http_meta_no_headers(mock_store_headers, span, int_config):
    assert int_config.myint.is_header_tracing_configured is False
    trace_utils.set_http_meta(
        span,
        int_config.myint,
        request_headers={"HTTP_REQUEST_HEADER": "value", "user-agent": "dd-agent/1.0.0"},
        response_headers={"HTTP_RESPONSE_HEADER": "value"},
    )
    result_keys = list(span.get_tags().keys())
    result_keys.sort(reverse=True)
    assert result_keys == ["runtime-id", http.USER_AGENT]
    mock_store_headers.assert_not_called()


def test_set_http_meta_insecure_cookies_iast_disabled(span, int_config):
    with override_global_config(dict(_iast_enabled=False)):
        cookies = {"foo": "bar"}
        trace_utils.set_http_meta(span, int_config.myint, request_cookies=cookies)
        span_report = core.get_item(IAST.CONTEXT_KEY, span=span)
        assert not span_report


@mock.patch("ddtrace.contrib.trace_utils._store_headers")
@pytest.mark.parametrize(
    "user_agent_key,user_agent_value,expected_keys,expected",
    [
        ("http-user-agent", "dd-agent/1.0.0", ["runtime-id", http.USER_AGENT], "dd-agent/1.0.0"),
        ("http-user-agent", None, ["runtime-id"], None),
        ("useragent", True, ["runtime-id"], None),
        ("http-user-agent", False, ["runtime-id"], None),
        ("http-user-agent", [], ["runtime-id"], None),
        ("http-user-agent", {}, ["runtime-id"], None),
    ],
)
def test_set_http_meta_headers_useragent(
    mock_store_headers, user_agent_key, user_agent_value, expected_keys, expected, span, int_config
):
    int_config.myint.http._header_tags = {"enabled": True}
    assert int_config.myint.is_header_tracing_configured is True
    trace_utils.set_http_meta(
        span,
        int_config.myint,
        request_headers={user_agent_key: user_agent_value},
    )
    result_keys = list(span.get_tags().keys())
    result_keys.sort(reverse=True)
    assert result_keys == expected_keys
    assert span.get_tag(http.USER_AGENT) == expected
    mock_store_headers.assert_called()


@mock.patch("ddtrace.contrib.trace_utils._store_headers")
def test_set_http_meta_case_sensitive_headers(mock_store_headers, span, int_config):
    int_config.myint.http._header_tags = {"enabled": True}
    trace_utils.set_http_meta(
        span, int_config.myint, request_headers={"USER-AGENT": "dd-agent/1.0.0"}, headers_are_case_sensitive=True
    )
    result_keys = list(span.get_tags().keys())
    result_keys.sort(reverse=True)
    assert result_keys == ["runtime-id", http.USER_AGENT]
    assert span.get_tag(http.USER_AGENT) == "dd-agent/1.0.0"
    mock_store_headers.assert_called()


@mock.patch("ddtrace.contrib.trace_utils._store_headers")
def test_set_http_meta_case_sensitive_headers_notfound(mock_store_headers, span, int_config):
    int_config.myint.http._header_tags = {"enabled": True}
    trace_utils.set_http_meta(
        span, int_config.myint, request_headers={"USER-AGENT": "dd-agent/1.0.0"}, headers_are_case_sensitive=False
    )
    result_keys = list(span.get_tags().keys())
    result_keys.sort(reverse=True)
    assert result_keys == ["runtime-id"]
    assert not span.get_tag(http.USER_AGENT)
    mock_store_headers.assert_called()


@pytest.mark.parametrize(
    "header_env_var,headers_dict,expected",
    [
        (
            "",
            {"x-forwarded-for": "8.8.8.8"},
            "8.8.8.8",
        ),
        (
            "",
            {"x-forwarded-for": "8.8.8.8,127.0.0.1"},
            "8.8.8.8",
        ),
        (
            "",
            {"x-forwarded-for": "192.168.144.2"},
            "192.168.144.2",
        ),
        (
            "",
            {"x-forwarded-for": "127.0.0.1"},
            "127.0.0.1",
        ),
        (
            "",
            {"x-forwarded-for": "192.168.1.14,8.8.8.8,127.0.0.1"},
            "8.8.8.8",
        ),
        (
            "",
            {"x-forwarded-for": "192.168.1.14,127.0.0.1"},
            "192.168.1.14",
        ),
        ("", {"x-forwarded-for": "foobar"}, ""),
        ("x-real-ip", {"x-forwarded-for": "4.4.8.8"}, ""),
        ("x-real-ip", {"x-real-ip": "8.8.8.8"}, "8.8.8.8"),
        (
            "x-real-ip",
            {"x-forwarded-for": "4.4.4.4", "x-real-ip": "8.8.4.4"},
            "8.8.4.4",
        ),
    ],
)
def test_get_request_header_ip(header_env_var, headers_dict, expected, span):
    with override_global_config(dict(_asm_enabled=True, client_ip_header=header_env_var)):
        ip = trace_utils._get_request_header_client_ip(headers_dict, None, False)
        assert ip == expected


@pytest.mark.parametrize(
    "headers_dict,peer_ip,expected",
    [
        # Public IP in headers should be selected
        (
            {"x-forwarded-for": "8.8.8.8"},
            "127.0.0.1",
            "8.8.8.8",
        ),
        # Public IP in headers only should be selected
        (
            {"x-forwarded-for": "8.8.8.8"},
            "",
            "8.8.8.8",
        ),
        # Public peer IP, get preferred over loopback IP in headers
        (
            {"x-forwarded-for": "127.0.0.1"},
            "8.8.8.8",
            "8.8.8.8",
        ),
        # Public peer IP, get preferred over private IP in headers
        (
            {"x-forwarded-for": "192.168.1.1"},
            "8.8.8.8",
            "8.8.8.8",
        ),
        # Public IP on both, headers selected
        (
            {"x-forwarded-for": "8.8.8.8"},
            "8.8.4.4",
            "8.8.8.8",
        ),
        # Empty header IP, peer should be selected
        (
            {"x-forwarded-for": ""},
            "8.8.4.4",
            "8.8.4.4",
        ),
        # Empty header IP, peer should be selected even if loopback
        (
            {"x-forwarded-for": ""},
            "127.0.0.1",
            "127.0.0.1",
        ),
        # Empty headers, peer should be selected even if loopback
        (
            {},
            "127.0.0.1",
            "127.0.0.1",
        ),
        # None headers, peer should be selected even if loopback
        (
            None,
            "127.0.0.1",
            "127.0.0.1",
        ),
        # None everything, empty IP returned
        (
            None,
            None,
            "",
        ),
        # None headers, invalid peer IP, empty IP returned
        (
            None,
            "invalid",
            "",
        ),
        # Both invalid, empty IP returned
        (
            {"x-forwarded-for": "invalid"},
            "invalid",
            "",
        ),
        # Invalid IP in headers, peer ip should be selected even if loopback
        (
            {"x-forwarded-for": "invalid"},
            "127.0.0.1",
            "127.0.0.1",
        ),
    ],
)
def test_get_request_header_client_ip_peer_ip_selection(headers_dict, peer_ip, expected):
    ip = _get_request_header_client_ip(headers_dict, peer_ip, True)
    assert ip == expected


def test_set_http_meta_headers_ip_asm_disabled_env_default_false(span, int_config):
    with override_global_config(dict(_asm_enabled=False)):
        int_config.myint.http._header_tags = {"enabled": True}
        assert int_config.myint.is_header_tracing_configured is True
        trace_utils.set_http_meta(
            span,
            int_config.myint,
            request_headers={"x-real-ip": "8.8.8.8"},
        )
        result_keys = list(span.get_tags().keys())
        result_keys.sort(reverse=True)
        assert result_keys == ["runtime-id"]


def test_set_http_meta_headers_ip_asm_disabled_env_false(span, int_config):
    with override_global_config(dict(_asm_enabled=False, retrieve_client_ip=False)):
        int_config.myint.http._header_tags = {"enabled": True}
        assert int_config.myint.is_header_tracing_configured is True
        trace_utils.set_http_meta(
            span,
            int_config.myint,
            request_headers={"x-real-ip": "8.8.8.8"},
        )
        result_keys = list(span.get_tags().keys())
        result_keys.sort(reverse=True)
        assert result_keys == ["runtime-id"]


def test_set_http_meta_headers_ip_asm_disabled_env_true(span, int_config):
    with override_global_config(dict(_asm_enabled=False, retrieve_client_ip=True)):
        int_config.myint.http._header_tags = {"enabled": True}
        assert int_config.myint.is_header_tracing_configured is True
        trace_utils.set_http_meta(
            span,
            int_config.myint,
            request_headers={"x-real-ip": "8.8.8.8"},
        )
        result_keys = list(span.get_tags().keys())
        result_keys.sort(reverse=True)
        assert result_keys == ["runtime-id", "network.client.ip", http.CLIENT_IP]
        assert span.get_tag(http.CLIENT_IP) == "8.8.8.8"


def test_ip_subnet_regression():
    del_ip = "1.2.3.4/32"
    req_ip = "10.2.3.4"

    del_ip = ensure_text(del_ip)
    req_ip = ensure_text(req_ip)

    assert not ip_network(req_ip).subnet_of(ip_network(del_ip))


@mock.patch("ddtrace.contrib.trace_utils._store_headers")
@pytest.mark.parametrize(
    "user_agent_value, expected_keys ,expected",
    [
        ("ㄲㄴㄷㄸ", ["runtime-id", http.USER_AGENT], "ㄲㄴㄷㄸ"),
        (b"", ["runtime-id"], None),
    ],
)
def test_set_http_meta_headers_useragent(  # noqa:F811
    mock_store_headers, user_agent_value, expected_keys, expected, span, int_config
):
    assert int_config.myint.is_header_tracing_configured is False
    trace_utils.set_http_meta(
        span,
        int_config.myint,
        request_headers={"user-agent": user_agent_value},
    )

    result_keys = list(span.get_tags().keys())
    result_keys.sort(reverse=True)
    assert result_keys == expected_keys
    assert span.get_tag(http.USER_AGENT) == expected
    mock_store_headers.assert_not_called()


@mock.patch("ddtrace.contrib.trace_utils.log")
@pytest.mark.parametrize(
    "val, bad",
    [
        ("asdf", True),
        (object(), True),
        ("234", False),
        ("100.0", True),
        ("-123", False),
    ],
)
def test_bad_http_code(mock_log, span, int_config, val, bad):
    trace_utils.set_http_meta(span, int_config, status_code=val)
    if bad:
        assert http.STATUS_CODE not in span.get_tags()
        mock_log.debug.assert_called_once_with("failed to convert http status code %r to int", val)
    else:
        assert span.get_tag(http.STATUS_CODE) == str(val)


@pytest.mark.parametrize(
    "props,default,expected",
    (
        # Not configured, take the default
        ({}, None, False),
        ({}, False, False),
        ({}, True, True),
        # _enabled only, take provided value
        ({"distributed_tracing_enabled": True}, None, True),
        ({"distributed_tracing_enabled": True}, False, True),
        ({"distributed_tracing_enabled": True}, True, True),
        ({"distributed_tracing_enabled": None}, None, False),
        ({"distributed_tracing_enabled": None}, True, True),
        ({"distributed_tracing_enabled": None}, False, False),
        ({"distributed_tracing_enabled": False}, None, False),
        ({"distributed_tracing_enabled": False}, False, False),
        ({"distributed_tracing_enabled": False}, True, False),
        # non-_enabled only, take provided value
        ({"distributed_tracing": True}, None, True),
        ({"distributed_tracing": True}, False, True),
        ({"distributed_tracing": True}, True, True),
        ({"distributed_tracing": None}, None, False),
        ({"distributed_tracing": None}, True, True),
        ({"distributed_tracing": None}, False, False),
        ({"distributed_tracing": False}, None, False),
        ({"distributed_tracing": False}, False, False),
        ({"distributed_tracing": False}, True, False),
        # Prefer _enabled value
        ({"distributed_tracing_enabled": True, "distributed_tracing": False}, None, True),
        ({"distributed_tracing_enabled": True, "distributed_tracing": True}, None, True),
        ({"distributed_tracing_enabled": True, "distributed_tracing": None}, None, True),
        ({"distributed_tracing_enabled": None, "distributed_tracing": True}, None, True),
        ({"distributed_tracing_enabled": None, "distributed_tracing": None}, None, False),
        ({"distributed_tracing_enabled": None, "distributed_tracing": None}, False, False),
        ({"distributed_tracing_enabled": None, "distributed_tracing": None}, True, True),
        ({"distributed_tracing_enabled": False, "distributed_tracing": True}, None, False),
        ({"distributed_tracing_enabled": False, "distributed_tracing": False}, None, False),
    ),
)
def test_distributed_tracing_enabled(int_config, props, default, expected):
    kwargs = {}
    if default is not None:
        kwargs["default"] = default

    for key, value in props.items():
        int_config.myint[key] = value

    assert trace_utils.distributed_tracing_enabled(int_config.myint, **kwargs) == expected, (props, default, expected)


def test_activate_distributed_headers_enabled(int_config):
    tracer = Tracer()
    int_config.myint["distributed_tracing_enabled"] = True
    headers = {
        HTTP_HEADER_PARENT_ID: "12345",
        HTTP_HEADER_TRACE_ID: "678910",
    }
    trace_utils.activate_distributed_headers(tracer, int_config=int_config.myint, request_headers=headers)
    context = tracer.context_provider.active()
    assert context.trace_id == 678910
    assert context.span_id == 12345

    trace_utils.activate_distributed_headers(
        tracer, int_config=int_config.myint, request_headers=headers, override=True
    )
    context = tracer.context_provider.active()
    assert context.trace_id == 678910
    assert context.span_id == 12345


def test_activate_distributed_headers_disabled(int_config):
    tracer = Tracer()
    int_config.myint["distributed_tracing_enabled"] = False
    headers = {
        HTTP_HEADER_PARENT_ID: "12345",
        HTTP_HEADER_TRACE_ID: "678910",
    }
    trace_utils.activate_distributed_headers(tracer, int_config=int_config.myint, request_headers=headers)
    assert tracer.context_provider.active() is None

    trace_utils.activate_distributed_headers(
        tracer, int_config=int_config.myint, request_headers=headers, override=False
    )
    assert tracer.context_provider.active() is None


def test_activate_distributed_headers_no_headers(int_config):
    tracer = Tracer()
    int_config.myint["distributed_tracing_enabled"] = True

    trace_utils.activate_distributed_headers(tracer, int_config=int_config.myint, request_headers=None)
    assert tracer.context_provider.active() is None


def test_activate_distributed_headers_override_true(int_config):
    tracer = Tracer()
    int_config.myint["distributed_tracing_enabled"] = False
    headers = {
        HTTP_HEADER_PARENT_ID: "12345",
        HTTP_HEADER_TRACE_ID: "678910",
    }
    trace_utils.activate_distributed_headers(
        tracer, int_config=int_config.myint, request_headers=headers, override=True
    )
    context = tracer.context_provider.active()
    assert context.trace_id == 678910
    assert context.span_id == 12345


def test_activate_distributed_headers_override_false(int_config):
    tracer = Tracer()
    int_config.myint["distributed_tracing_enabled"] = True
    headers = {
        HTTP_HEADER_PARENT_ID: "12345",
        HTTP_HEADER_TRACE_ID: "678910",
    }
    trace_utils.activate_distributed_headers(
        tracer, int_config=int_config.myint, request_headers=headers, override=False
    )
    assert tracer.context_provider.active() is None


def test_activate_distributed_headers_existing_context(int_config):
    tracer = Tracer()
    int_config.myint["distributed_tracing_enabled"] = True

    headers = {
        HTTP_HEADER_PARENT_ID: "12345",
        HTTP_HEADER_TRACE_ID: "678910",
    }

    ctx = Context(trace_id=678910, span_id=823923)  # Note: Span id is different
    tracer.context_provider.activate(ctx)

    trace_utils.activate_distributed_headers(tracer, int_config=int_config.myint, request_headers=headers)
    assert tracer.context_provider.active() == ctx


def test_activate_distributed_headers_existing_context_different_trace_id(int_config):
    tracer = Tracer()
    int_config.myint["distributed_tracing_enabled"] = True

    headers = {
        HTTP_HEADER_PARENT_ID: "12345",
        HTTP_HEADER_TRACE_ID: "678910",
    }

    ctx = Context(trace_id=3473873, span_id=678308)  # Note: Trace id is different
    tracer.context_provider.activate(ctx)

    trace_utils.activate_distributed_headers(tracer, int_config=int_config.myint, request_headers=headers)
    new_ctx = tracer.context_provider.active()
    assert new_ctx != ctx
    assert new_ctx is not None
    assert new_ctx.trace_id == 678910
    assert new_ctx.span_id == 12345


def test_sanitized_url_in_http_meta(span, int_config):
    FULL_URL = "http://example.com/search?q=test+query#frag?ment"
    STRIPPED_URL = "http://example.com/search#frag?ment"

    int_config.http_tag_query_string = False
    trace_utils.set_http_meta(
        span,
        int_config,
        method="GET",
        url=FULL_URL,
        status_code=200,
    )
    assert span.get_tag(http.URL) == STRIPPED_URL

    int_config.http_tag_query_string = True
    trace_utils.set_http_meta(
        span,
        int_config,
        method="GET",
        url=FULL_URL,
        status_code=200,
    )
    assert span.get_tag(http.URL) == FULL_URL


def test_url_in_http_meta(span, int_config):
    SENSITIVE_QS_URL = "http://example.com/search?token=03cb9f67dbbc4cb8b963629951e10934&q=query#frag?ment"
    REDACTED_URL = "http://example.com/search?<redacted>&q=query#frag?ment"
    STRIPPED_URL = "http://example.com/search#frag?ment"

    int_config.http_tag_query_string = True
    with override_global_config({"global_query_string_obfuscation_disabled": False}):
        trace_utils.set_http_meta(
            span,
            int_config,
            method="GET",
            url=SENSITIVE_QS_URL,
            status_code=200,
        )
        assert span.get_tag(http.URL) == REDACTED_URL
    with override_global_config({"global_query_string_obfuscation_disabled": True}):
        trace_utils.set_http_meta(
            span,
            int_config,
            method="GET",
            url=SENSITIVE_QS_URL,
            status_code=200,
        )
        assert span.get_tag(http.URL) == SENSITIVE_QS_URL

    int_config.http_tag_query_string = False
    with override_global_config({"global_query_string_obfuscation_disabled": False}):
        trace_utils.set_http_meta(
            span,
            int_config,
            method="GET",
            url=SENSITIVE_QS_URL,
            status_code=200,
        )
        assert span.get_tag(http.URL) == STRIPPED_URL
    with override_global_config({"global_query_string_obfuscation_disabled": True}):
        trace_utils.set_http_meta(
            span,
            int_config,
            method="GET",
            url=SENSITIVE_QS_URL,
            status_code=200,
        )
        assert span.get_tag(http.URL) == STRIPPED_URL


def test_redacted_url_in_http_meta(span, int_config):
    SENSITIVE_QS_URL = "http://example.com/search?token=03cb9f67dbbc4cb8b963629951e10934&q=query#frag?ment"
    STRIPPED_URL = "http://example.com/search#frag?ment"
    REDACTED_QS_URL = "http://example.com/search?<redacted>&q=query#frag?ment"

    int_config.http_tag_query_string = False
    trace_utils.set_http_meta(
        span,
        int_config,
        method="GET",
        url=SENSITIVE_QS_URL,
        status_code=200,
    )
    assert span.get_tag(http.URL) == STRIPPED_URL

    int_config.http_tag_query_string = True
    trace_utils.set_http_meta(
        span,
        int_config,
        method="GET",
        url=SENSITIVE_QS_URL,
        status_code=200,
    )
    assert span.get_tag(http.URL) == REDACTED_QS_URL


def test_redacted_query_string_as_argument_in_http_meta(span, int_config):
    BASE_URL = "http://example.com/search"
    SENSITIVE_QS = "token=03cb9f67dbbc4cb8b963629951e10934&q=query"
    REDACTED_QS = "<redacted>&q=query"
    FRAGMENT = "frag?ment"
    SENSITIVE_URL = BASE_URL + "?" + SENSITIVE_QS + "#" + FRAGMENT
    REDACTED_URL = BASE_URL + "?" + REDACTED_QS + "#" + FRAGMENT
    STRIPPED_URL = BASE_URL + "#" + FRAGMENT

    int_config.http_tag_query_string = False
    trace_utils.set_http_meta(
        span,
        int_config,
        method="GET",
        url=SENSITIVE_URL,
        query=SENSITIVE_QS,
        status_code=200,
    )
    assert span.get_tag(http.URL) == STRIPPED_URL

    int_config.http_tag_query_string = True
    trace_utils.set_http_meta(
        span,
        int_config,
        method="GET",
        url=SENSITIVE_URL,
        query=SENSITIVE_QS,
        status_code=200,
    )
    assert span.get_tag(http.URL) == REDACTED_URL


@mock.patch("ddtrace.internal.utils.http.redact_query_string")
def test_empty_query_string_in_http_meta_should_not_call_redact_function(mock_redact_query_string, span, int_config):
    URL = "http://example.com/search#frag?ment"
    EMPTY_QS = ""
    NONE_QS = None

    int_config.http_tag_query_string = True
    trace_utils.set_http_meta(
        span,
        int_config,
        method="GET",
        url=URL,
        status_code=200,
    )
    mock_redact_query_string.assert_not_called()
    assert span.get_tag(http.URL) == URL

    trace_utils.set_http_meta(
        span,
        int_config,
        method="GET",
        url=URL,
        query=EMPTY_QS,
        status_code=200,
    )
    mock_redact_query_string.assert_not_called()
    assert span.get_tag(http.URL) == URL

    trace_utils.set_http_meta(
        span,
        int_config,
        method="GET",
        url=URL,
        query=NONE_QS,
        status_code=200,
    )
    mock_redact_query_string.assert_not_called()
    assert span.get_tag(http.URL) == URL


# This generates a list of (key, value) tuples, with values given by nested
# dictionaries
@given(
    lists(
        tuples(
            text(),
            recursive(
                none() | booleans() | floats() | text(),
                lambda children: lists(children, min_size=1) | dictionaries(text(), children, min_size=1),
                max_leaves=10,
            ),
        ),
        max_size=4,
    )
)
def test_set_flattened_tags_is_flat(items):
    """Ensure that flattening of a nested dict results in a normalized, 1-level dict"""
    span = Span("test")
    trace_utils.set_flattened_tags(span, items)
    assert isinstance(span.get_tags(), dict)
    assert not any(isinstance(v, dict) for v in span.get_tags().values())


def test_set_flattened_tags_keys():
    """Ensure expected keys in flattened dictionary"""
    d = dict(A=1, B=2, C=dict(A=3, B=4, C=dict(A=5, B=6)))
    e = dict(A=1, B=2, C_A=3, C_B=4, C_C_A=5, C_C_B=6)
    span = Span("test")
    trace_utils.set_flattened_tags(span, d.items(), sep="_")
    assert span.get_metrics() == e


def test_set_flattened_tags_exclude_policy():
    """Ensure expected keys in flattened dictionary with exclusion set"""
    d = dict(A=1, B=2, C=dict(A=3, B=4, C=dict(A=5, B=6)))
    e = dict(A=1, B=2, C_B=4)
    span = Span("test")

    trace_utils.set_flattened_tags(span, d.items(), sep="_", exclude_policy=lambda tag: tag in {"C_A", "C_C"})
    assert span.get_metrics() == e
