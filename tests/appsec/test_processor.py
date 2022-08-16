import json
import os.path

import pytest

from ddtrace.appsec._ddwaf import DDWaf
from ddtrace.appsec.processor import AppSecSpanProcessor
<<<<<<< HEAD
=======
from ddtrace.appsec.processor import DEFAULT_RULES
from ddtrace.appsec.processor import _transform_headers
from ddtrace.constants import USER_KEEP
from ddtrace.contrib.trace_utils import set_http_meta
>>>>>>> 399940a8 (feat(asm): fix segmentation fault parsing JSON in Python2 (#4082))
from ddtrace.ext import SpanTypes
from ddtrace.ext import priority
from tests.utils import override_env
from tests.utils import override_global_config
from tests.utils import snapshot


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
RULES_GOOD_PATH = os.path.join(ROOT_DIR, "rules-good.json")
RULES_BAD_PATH = os.path.join(ROOT_DIR, "rules-bad.json")
RULES_MISSING_PATH = os.path.join(ROOT_DIR, "nonexistent")


def test_enable(tracer):
    tracer._initialize_span_processors(appsec_enabled=True)
    with tracer.trace("test", span_type=SpanTypes.WEB.value) as span:
        span.set_tag("http.url", "http://example.com/.git")
        span.set_tag("http.status_code", "404")

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
            tracer._initialize_span_processors(appsec_enabled=True)

    # by default enable must not crash but display errors in the logs
    with override_global_config(dict(_raise=False)):
        with override_env(dict(DD_APPSEC_RULES=rule)):
            tracer._initialize_span_processors(appsec_enabled=True)


def test_retain_traces(tracer):
    tracer._initialize_span_processors(appsec_enabled=True)

    with tracer.trace("test", span_type=SpanTypes.WEB.value) as span:
        span.set_tag("http.url", "http://example.com/.git")
        span.set_tag("http.status_code", "404")

    assert span.context.sampling_priority == priority.USER_KEEP


def test_valid_json(tracer):
    tracer._initialize_span_processors(appsec_enabled=True)

    with tracer.trace("test", span_type=SpanTypes.WEB.value) as span:
        span.set_tag("http.url", "http://example.com/.git")
        span.set_tag("http.status_code", "404")

    assert "triggers" in json.loads(span.get_tag("_dd.appsec.json"))


@snapshot(include_tracer=True)
def test_appsec_span_tags_snapshot(tracer):
    tracer._initialize_span_processors(appsec_enabled=True)

    with tracer.trace("test", span_type=SpanTypes.WEB.value) as span:
        span.set_tag("http.url", "http://example.com/.git")
        span.set_tag("http.status_code", "404")

    assert "triggers" in json.loads(span.get_tag("_dd.appsec.json"))
<<<<<<< HEAD
=======


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


def test_ddwaf_not_raises_exception():
    with open(DEFAULT_RULES) as rules:
        rules_json = json.loads(rules.read())
        DDWaf(rules_json)
>>>>>>> 399940a8 (feat(asm): fix segmentation fault parsing JSON in Python2 (#4082))
