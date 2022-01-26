import json
import os.path
import sys

import pytest

from ddtrace.appsec.processor import AppSecSpanProcessor
from ddtrace.ext import priority
from tests.utils import override_env
from tests.utils import override_global_config


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


def test_enable(tracer):
    tracer._initialize_span_processors(appsec_enabled=True)
    with tracer.trace("test", span_type="web") as span:
        span.set_tag("http.url", "http://example.com/.git")
        span.set_tag("http.status_code", "404")

    assert span.get_metric("_dd.appsec.enabled") == 1.0


def test_enable_import_failure(tracer):
    # Explicitly break the processor to simulate an import failure
    sys.modules["ddtrace.appsec.processor"] = {}
    try:
        sys.modules.pop("ddtrace.appsec", None)
        with pytest.raises(ImportError):
            tracer._initialize_span_processors(appsec_enabled=True)

        with override_global_config(dict(_raise=False)):
            sys.modules.pop("ddtrace.appsec", None)
            tracer._initialize_span_processors(appsec_enabled=True)

    finally:
        sys.modules.pop("ddtrace.appsec", None)
        sys.modules.pop("ddtrace.appsec.processor", None)


def test_enable_custom_rules():
    with override_env(dict(DD_APPSEC_RULES=os.path.join(ROOT_DIR, "rules-good.json"))):
        processor = AppSecSpanProcessor()

    assert processor.enabled
    assert processor.rules == os.path.join(ROOT_DIR, "rules-good.json")


@pytest.mark.parametrize("rule,exc", [("nonexistent", IOError), ("rules-bad.json", ValueError)])
def test_enable_bad_rules(rule, exc, tracer):
    with override_env(dict(DD_APPSEC_RULES=os.path.join(ROOT_DIR, rule))):
        with pytest.raises(exc):
            tracer._initialize_span_processors(appsec_enabled=True)

    # by default enable must not crash but display errors in the logs
    with override_global_config(dict(_raise=False)):
        with override_env(dict(DD_APPSEC_RULES=os.path.join(ROOT_DIR, rule))):
            tracer._initialize_span_processors(appsec_enabled=True)


def test_retain_traces(tracer):
    tracer._initialize_span_processors(appsec_enabled=True)

    with tracer.trace("test", span_type="web") as span:
        span.set_tag("http.url", "http://example.com/.git")
        span.set_tag("http.status_code", "404")

    assert span.context.sampling_priority == priority.USER_KEEP


def test_valid_json(tracer):
    tracer._initialize_span_processors(appsec_enabled=True)

    with tracer.trace("test", span_type="web") as span:
        span.set_tag("http.url", "http://example.com/.git")
        span.set_tag("http.status_code", "404")

    assert "triggers" in json.loads(span.get_tag("_dd.appsec.json"))
