import pytest

from ddtrace import Pin, Tracer, config
from ddtrace.settings import Config
from ddtrace.ext import http
from ddtrace.compat import stringify
from ddtrace.contrib import trace_utils

from tests import override_global_config
from tests.tracer.test_tracer import get_dummy_tracer


@pytest.fixture
def int_config():
    c = Config()
    c._add("myint", dict())
    return c


@pytest.fixture
def tracer():
    tracer = get_dummy_tracer()
    return tracer


@pytest.fixture
def span(tracer):
    span = tracer.trace(name="myint")
    yield span
    span.finish()


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


def set_config_error_codes(error_codes):
    config.http_server.error_statuses = error_codes
    return config


@pytest.mark.parametrize(
    "method,url,status_code,query_params,headers",
    [
        ("GET", "http://localhost/", 0, None, None),
        ("GET", "http://localhost/", 200, None, None),
        (None, None, None, None, None),
    ],
)
def test_set_http_meta(span, int_config, method, url, status_code, query_params, headers):
    trace_utils.set_http_meta(
        span, int_config, method=method, url=url, status_code=status_code, query_params=query_params, headers=headers
    )
    if method is not None:
        assert span.meta[http.METHOD] == method
    else:
        assert http.METHOD not in span.meta

    if url is not None:
        assert span.meta[http.URL] == stringify(url)
    else:
        assert http.URL not in span.meta

    if status_code is not None:
        assert span.meta[http.STATUS_CODE] == str(status_code)
        if 500 <= int(status_code) < 600:
            assert span.error == 1
        else:
            assert span.error == 0
    else:
        assert http.STATUS_CODE not in span.meta

    if query_params is not None and int_config.trace_query_string:
        assert span.meta[http.QUERY_STRING] == query_params

    if headers is not None:
        for header, value in headers.items():
            tag = "http.request.headers" + header
            assert span.get_tag(tag) == value


@pytest.mark.parametrize(
    "error_codes,status_code,error",
    [("404-400", 400, 1), ("400-404", 400, 1), ("400-404", 500, 0), ("500-520", 530, 0), ("500-550         ", 530, 1)],
)
def test_set_http_meta_error_codes(span, int_config, error_codes, status_code, error):
    config.http_server.error_statuses = error_codes
    trace_utils.set_http_meta(span, int_config, status_code=status_code)
    assert span.error == error
