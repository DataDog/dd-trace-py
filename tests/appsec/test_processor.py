import json
import os.path

import pytest

from ddtrace.appsec.processor import AppSecSpanProcessor
from ddtrace.appsec.processor import _transform_headers
from ddtrace.constants import USER_KEEP
from ddtrace.contrib.trace_utils import set_http_meta
from ddtrace.ext import SpanTypes
from tests.utils import override_env
from tests.utils import override_global_config
from tests.utils import snapshot


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
RULES_GOOD_PATH = os.path.join(ROOT_DIR, "rules-good.json")
RULES_BAD_PATH = os.path.join(ROOT_DIR, "rules-bad.json")
RULES_MISSING_PATH = os.path.join(ROOT_DIR, "nonexistent")


class Config(object):
    def __init__(self):
        self.is_header_tracing_configured = False


def _enable_appsec(tracer):
    tracer._appsec_enabled = True
    # Hack: need to pass an argument to configure so that the processors are recreated
    tracer.configure(api_version="v0.4")
    return tracer


def test_transform_headers():
    transformed = _transform_headers(
        {
            "hello": "world",
            "Foo": "bar1",
            "foo": "bar2",
            "fOO": "bar3",
            "BAR": "baz",
            "COOKIE": "secret",
        },
    )
    assert set(transformed.keys()) == {"hello", "bar", "foo"}
    assert transformed["hello"] == "world"
    assert transformed["bar"] == "baz"
    assert set(transformed["foo"]) == {"bar1", "bar2", "bar3"}


def test_enable(tracer):
    _enable_appsec(tracer)

    with tracer.trace("test", span_type=SpanTypes.WEB) as span:
        set_http_meta(span, {}, raw_uri="http://example.com/.git", status_code="404")

    assert span.get_metric("_dd.appsec.enabled") == 1.0


def test_enable_custom_rules():
    with override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
        processor = AppSecSpanProcessor()

    assert processor.enabled
    assert processor.rules == RULES_GOOD_PATH


@pytest.mark.parametrize("rule,exc", [(RULES_MISSING_PATH, IOError), (RULES_BAD_PATH, ValueError)])
def test_enable_bad_rules(rule, exc, tracer):
    with override_env(dict(DD_APPSEC_RULES=rule)):
        with pytest.raises(exc):
            _enable_appsec(tracer)

    # by default enable must not crash but display errors in the logs
    with override_global_config(dict(_raise=False)):
        with override_env(dict(DD_APPSEC_RULES=rule)):
            _enable_appsec(tracer)


def test_retain_traces(tracer):
    _enable_appsec(tracer)

    with tracer.trace("test", span_type=SpanTypes.WEB) as span:
        set_http_meta(span, {}, raw_uri="http://example.com/.git", status_code="404")

    assert span.context.sampling_priority == USER_KEEP


def test_valid_json(tracer):
    _enable_appsec(tracer)

    with tracer.trace("test", span_type=SpanTypes.WEB) as span:
        set_http_meta(span, {}, raw_uri="http://example.com/.git", status_code="404")

    assert "triggers" in json.loads(span.get_tag("_dd.appsec.json"))


def test_header_attack(tracer):
    _enable_appsec(tracer)

    with tracer.trace("test", span_type=SpanTypes.WEB) as span:
        set_http_meta(span, Config(), request_headers={"User-Agent": "Arachni/v1", "user-agent": "aa"})

    assert "triggers" in json.loads(span.get_tag("_dd.appsec.json"))


def test_headers_collection(tracer):
    _enable_appsec(tracer)

    with tracer.trace("test", span_type=SpanTypes.WEB) as span:

        set_http_meta(
            span,
            Config(),
            raw_uri="http://example.com/.git",
            status_code="404",
            request_headers={
                "hello": "world",
                "accept": "something",
                "x-Forwarded-for": "127.0.0.1",
            },
            response_headers={
                "foo": "bar",
                "Content-Length": "500",
            },
        )

    assert span.get_tag("http.request.headers.hello") is None
    assert span.get_tag("http.request.headers.accept") == "something"
    assert span.get_tag("http.request.headers.x-forwarded-for") == "127.0.0.1"
    assert span.get_tag("http.response.headers.content-length") == "500"
    assert span.get_tag("http.response.headers.foo") is None


@snapshot(include_tracer=True)
def test_appsec_cookies_no_collection_snapshot(tracer):
    _enable_appsec(tracer)
    with tracer.trace("test", span_type=SpanTypes.WEB) as span:
        set_http_meta(
            span,
            {},
            raw_uri="http://example.com/.git",
            status_code="404",
            request_cookies={"cookie1": "im the cookie1"},
        )

    assert "triggers" in json.loads(span.get_tag("_dd.appsec.json"))


@snapshot(include_tracer=True)
def test_appsec_body_no_collection_snapshot(tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        _enable_appsec(tracer)
        with tracer.trace("test", span_type=SpanTypes.WEB) as span:
            set_http_meta(
                span,
                {},
                raw_uri="http://example.com/.git",
                status_code="404",
                request_body={"somekey": "somekey value"},
            )

        assert "triggers" in json.loads(span.get_tag("_dd.appsec.json"))


@snapshot(include_tracer=True)
def test_appsec_span_tags_snapshot(tracer):
    _enable_appsec(tracer)

    with tracer.trace("test", span_type=SpanTypes.WEB) as span:
        span.set_tag("http.url", "http://example.com/.git")
        set_http_meta(span, {}, raw_uri="http://example.com/.git", status_code="404")

    assert "triggers" in json.loads(span.get_tag("_dd.appsec.json"))


def test_appsec_span_rate_limit(tracer):
    with override_env(dict(DD_APPSEC_TRACE_RATE_LIMIT="1")):
        _enable_appsec(tracer)

        # we have 2 spans going through with a rate limit of 1: this is because the first span will update the rate
        # limiter last update timestamp. In other words, we need a first call to reset the rate limiter's clock
        # DEV: aligning rate limiter clock with this span (this
        #      span will go through as it is linked to the init window)
        with tracer.trace("test", span_type=SpanTypes.WEB) as span1:
            set_http_meta(span1, {}, raw_uri="http://example.com/.git", status_code="404")

        with tracer.trace("test", span_type=SpanTypes.WEB) as span2:
            set_http_meta(span2, {}, raw_uri="http://example.com/.git", status_code="404")
            span2.start_ns = span1.start_ns + 1

        with tracer.trace("test", span_type=SpanTypes.WEB) as span3:
            set_http_meta(span3, {}, raw_uri="http://example.com/.git", status_code="404")
            span2.start_ns = span1.start_ns + 2

        assert span1.get_tag("_dd.appsec.json") is not None
        assert span2.get_tag("_dd.appsec.json") is not None
        assert span3.get_tag("_dd.appsec.json") is None
