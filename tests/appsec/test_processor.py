import json
import os.path

import pytest

from ddtrace.appsec.processor import AppSecSpanProcessor
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


def _enable_appsec(tracer):
    tracer._appsec_enabled = True
    # Hack: need to pass an argument to configure so that the processors are recreated
    tracer.configure(api_version="v0.4")
    return tracer


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


def test_headers_collection(tracer):
    _enable_appsec(tracer)

    class Config(object):
        def __init__(self):
            self.is_header_tracing_configured = False

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
        )

    assert span.get_tag("http.request.headers.hello") is None
    assert span.get_tag("http.request.headers.accept") == "something"
    assert span.get_tag("http.request.headers.x-forwarded-for") == "127.0.0.1"


@snapshot(include_tracer=True)
def test_appsec_span_tags_snapshot(tracer):
    _enable_appsec(tracer)

    with tracer.trace("test", span_type=SpanTypes.WEB) as span:
        span.set_tag("http.url", "http://example.com/.git")
        set_http_meta(span, {}, raw_uri="http://example.com/.git", status_code="404")

    assert "triggers" in json.loads(span.get_tag("_dd.appsec.json"))
