import json
import os.path
import sys

import pytest

from ddtrace.ext import priority
from tests.utils import override_env
from tests.utils import override_global_config


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


def test_enable(appsec, tracer):
    appsec.enable(tracer)
    assert appsec.AppSecProcessor.enabled

    with tracer.trace("test", span_type="web") as span:
        span.set_tag("http.url", "http://example.com/.git")
        span.set_tag("http.status_code", "404")

    assert span.get_metric("_dd.appsec.enabled") == 1.0

    appsec.disable()
    assert not appsec.AppSecProcessor.enabled

    with tracer.trace("test", span_type="web") as span:
        span.set_tag("http.url", "http://example.com/.git")
        span.set_tag("http.status_code", "404")

    assert span.get_metric("_dd.appsec.enabled") is None


def test_enable_import_failure():
    # Explicitly break the processor to simulate an import failure
    sys.modules["ddtrace.appsec.processor"] = {}
    try:
        sys.modules.pop("ddtrace.appsec", None)
        with pytest.raises(ImportError):
            import ddtrace.appsec

        with override_global_config(dict(_raise=False)):
            sys.modules.pop("ddtrace.appsec", None)
            import ddtrace.appsec  # noqa: F811

            # by default enable must not crash but display errors in the logs
            ddtrace.appsec.enable()

            assert ddtrace.appsec.AppSecProcessor is None
    finally:
        sys.modules.pop("ddtrace.appsec", None)
        sys.modules.pop("ddtrace.appsec.processor", None)


def test_enable_custom_rules(appsec):
    with override_env(dict(DD_APPSEC_RULES=os.path.join(ROOT_DIR, "rules-good.json"))):
        appsec.enable()

    assert appsec.AppSecProcessor.enabled


@pytest.mark.parametrize("rule,exc", [("nonexistent", IOError), ("rules-bad.json", ValueError)])
def test_enable_bad_rules(rule, exc, appsec):
    with override_env(dict(DD_APPSEC_RULES=os.path.join(ROOT_DIR, rule))):
        with pytest.raises(exc):
            appsec.enable()

        assert not appsec.AppSecProcessor.enabled

    with override_global_config(dict(_raise=False)):
        with override_env(dict(DD_APPSEC_RULES=os.path.join(ROOT_DIR, rule))):
            # by default enable must not crash but display errors in the logs
            appsec.enable()

            assert not appsec.AppSecProcessor.enabled


def test_retain_traces(tracer, appsec):
    appsec.enable(tracer)

    with tracer.trace("test", span_type="web") as span:
        span.set_tag("http.url", "http://example.com/.git")
        span.set_tag("http.status_code", "404")

    assert span.context.sampling_priority == priority.USER_KEEP


def test_valid_json(tracer, appsec):
    appsec.enable(tracer)

    with tracer.trace("test", span_type="web") as span:
        span.set_tag("http.url", "http://example.com/.git")
        span.set_tag("http.status_code", "404")

    assert "triggers" in json.loads(span.get_tag("_dd.appsec.json"))
