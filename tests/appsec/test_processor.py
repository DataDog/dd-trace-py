import json
import os.path

import pytest

from ddtrace.appsec.processor import AppSecSpanProcessor
from ddtrace.ext import SpanTypes
from ddtrace.ext import priority
from tests.utils import override_env
from tests.utils import override_global_config


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
