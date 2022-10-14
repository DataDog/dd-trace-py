import json
import os.path

import pytest
from six import ensure_binary

from ddtrace.appsec._ddwaf import DDWaf
from ddtrace.appsec.processor import AppSecSpanProcessor
from ddtrace.appsec.processor import DEFAULT_APPSEC_OBFUSCATION_PARAMETER_KEY_REGEXP
from ddtrace.appsec.processor import DEFAULT_APPSEC_OBFUSCATION_PARAMETER_VALUE_REGEXP
from ddtrace.appsec.processor import DEFAULT_RULES
from ddtrace.appsec.processor import DEFAULT_WAF_TIMEOUT
from ddtrace.appsec.processor import _transform_headers
from ddtrace.constants import APPSEC_JSON
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


@pytest.fixture
def tracer_appsec(tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        yield _enable_appsec(tracer)


def _enable_appsec(tracer):
    tracer._appsec_enabled = True
    # Hack: need to pass an argument to configure so that the processors are recreated
    tracer.configure(api_version="v0.4")
    return tracer


class Config(object):
    def __init__(self):
        self.is_header_tracing_configured = False


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


def test_enable(tracer_appsec):
    tracer = tracer_appsec

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
    # with override_env(dict(DD_APPSEC_RULES=rule)):
    #     with pytest.raises(exc):
    #         _enable_appsec(tracer)

    # by default enable must not crash but display errors in the logs
    with override_global_config(dict(_raise=False)):
        with override_env(dict(DD_APPSEC_RULES=rule)):
            _enable_appsec(tracer)


def test_retain_traces(tracer_appsec):
    tracer = tracer_appsec

    with tracer.trace("test", span_type=SpanTypes.WEB) as span:
        set_http_meta(span, {}, raw_uri="http://example.com/.git", status_code="404")

    assert span.context.sampling_priority == USER_KEEP


def test_valid_json(tracer_appsec):
    tracer = tracer_appsec

    with tracer.trace("test", span_type=SpanTypes.WEB) as span:
        set_http_meta(span, {}, raw_uri="http://example.com/.git", status_code="404")

    assert "triggers" in json.loads(span.get_tag(APPSEC_JSON))


def test_header_attack(tracer_appsec):
    tracer = tracer_appsec

    with override_env(dict(DD_TRACE_CLIENT_IP_HEADER_DISABLED="False")):
        with tracer.trace("test", span_type=SpanTypes.WEB) as span:
            set_http_meta(
                span,
                Config(),
                request_headers={
                    "User-Agent": "Arachni/v1",
                    "user-agent": "aa",
                    "x-forwarded-for": "8.8.8.8",
                },
            )

        assert "triggers" in json.loads(span.get_tag(APPSEC_JSON))
        assert span.get_tag("actor.ip") == "8.8.8.8"


def test_headers_collection(tracer_appsec):
    tracer = tracer_appsec

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


@snapshot(
    include_tracer=True,
    ignores=[
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
    ],
)
def test_appsec_cookies_no_collection_snapshot(tracer):
    # We use tracer instead of tracer_appsec because snapshot is looking for tracer fixture and not understands
    # other fixtures
    with override_global_config(dict(_appsec_enabled=True)):
        _enable_appsec(tracer)
        with tracer.trace("test", span_type=SpanTypes.WEB) as span:
            set_http_meta(
                span,
                {},
                raw_uri="http://example.com/.git",
                status_code="404",
                request_cookies={"cookie1": "im the cookie1"},
            )

        assert "triggers" in json.loads(span.get_tag(APPSEC_JSON))


@snapshot(
    include_tracer=True,
    ignores=[
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
    ],
)
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

        assert "triggers" in json.loads(span.get_tag(APPSEC_JSON))


@snapshot(
    include_tracer=True,
    ignores=[
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
    ],
)
def test_appsec_span_tags_snapshot(tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        _enable_appsec(tracer)
        with tracer.trace("test", span_type=SpanTypes.WEB) as span:
            span.set_tag("http.url", "http://example.com/.git")
            set_http_meta(span, {}, raw_uri="http://example.com/.git", status_code="404")

        assert "triggers" in json.loads(span.get_tag(APPSEC_JSON))


@snapshot(
    include_tracer=True,
    ignores=[
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
        "meta._dd.appsec.event_rules.errors",
    ],
)
def test_appsec_span_tags_snapshot_with_errors(tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        with override_env(dict(DD_APPSEC_RULES=os.path.join(ROOT_DIR, "rules-with-2-errors.json"))):
            _enable_appsec(tracer)
            with tracer.trace("test", span_type=SpanTypes.WEB) as span:
                span.set_tag("http.url", "http://example.com/.git")
                set_http_meta(span, {}, raw_uri="http://example.com/.git", status_code="404")

        assert span.get_tag(APPSEC_JSON) is None


def test_appsec_span_rate_limit(tracer):

    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_TRACE_RATE_LIMIT="1")):
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

        assert span1.get_tag(APPSEC_JSON) is not None
        assert span2.get_tag(APPSEC_JSON) is not None
        assert span3.get_tag(APPSEC_JSON) is None


def test_ddwaf_not_raises_exception():
    with open(DEFAULT_RULES) as rules:
        rules_json = json.loads(rules.read())
        DDWaf(
            rules_json,
            ensure_binary(DEFAULT_APPSEC_OBFUSCATION_PARAMETER_KEY_REGEXP),
            ensure_binary(DEFAULT_APPSEC_OBFUSCATION_PARAMETER_VALUE_REGEXP),
        )


def test_obfuscation_parameter_key_empty():
    with override_env(dict(DD_APPSEC_OBFUSCATION_PARAMETER_KEY_REGEXP="")):
        processor = AppSecSpanProcessor()

    assert processor.enabled


def test_obfuscation_parameter_value_empty():
    with override_env(dict(DD_APPSEC_OBFUSCATION_PARAMETER_VALUE_REGEXP="")):
        processor = AppSecSpanProcessor()

    assert processor.enabled


def test_obfuscation_parameter_key_and_value_empty():
    with override_env(
        dict(DD_APPSEC_OBFUSCATION_PARAMETER_KEY_REGEXP="", DD_APPSEC_OBFUSCATION_PARAMETER_VALUE_REGEXP="")
    ):
        processor = AppSecSpanProcessor()

    assert processor.enabled


def test_obfuscation_parameter_key_invalid_regex():
    with override_env(dict(DD_APPSEC_OBFUSCATION_PARAMETER_KEY_REGEXP="(")):
        processor = AppSecSpanProcessor()

    assert processor.enabled


def test_obfuscation_parameter_invalid_regex():
    with override_env(dict(DD_APPSEC_OBFUSCATION_PARAMETER_VALUE_REGEXP="(")):
        processor = AppSecSpanProcessor()

    assert processor.enabled


def test_obfuscation_parameter_key_and_value_invalid_regex():
    with override_env(
        dict(DD_APPSEC_OBFUSCATION_PARAMETER_KEY_REGEXP="(", DD_APPSEC_OBFUSCATION_PARAMETER_VALUE_REGEXP="(")
    ):
        processor = AppSecSpanProcessor()

    assert processor.enabled


def test_obfuscation_parameter_value_unconfigured_not_matching(tracer_appsec):
    tracer = tracer_appsec

    with tracer.trace("test", span_type=SpanTypes.WEB) as span:
        set_http_meta(span, Config(), raw_uri="http://example.com/.git?hello=goodbye", status_code="404")

    assert "triggers" in json.loads(span.get_tag("_dd.appsec.json"))

    assert "hello" in span.get_tag("_dd.appsec.json")
    assert "goodbye" in span.get_tag("_dd.appsec.json")
    assert "<Redacted>" not in span.get_tag("_dd.appsec.json")


def test_obfuscation_parameter_value_unconfigured_matching(tracer_appsec):
    tracer = tracer_appsec

    with tracer.trace("test", span_type=SpanTypes.WEB) as span:
        set_http_meta(span, Config(), raw_uri="http://example.com/.git?password=goodbye", status_code="404")

    assert "triggers" in json.loads(span.get_tag("_dd.appsec.json"))

    assert "password" not in span.get_tag("_dd.appsec.json")
    assert "goodbye" not in span.get_tag("_dd.appsec.json")
    assert "<Redacted>" in span.get_tag("_dd.appsec.json")


def test_obfuscation_parameter_value_configured_not_matching(tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env(
        dict(DD_APPSEC_OBFUSCATION_PARAMETER_VALUE_REGEXP="token")
    ):

        _enable_appsec(tracer)

        with tracer.trace("test", span_type=SpanTypes.WEB) as span:
            set_http_meta(span, Config(), raw_uri="http://example.com/.git?password=goodbye", status_code="404")

        assert "triggers" in json.loads(span.get_tag("_dd.appsec.json"))

        assert "password" in span.get_tag("_dd.appsec.json")
        assert "goodbye" in span.get_tag("_dd.appsec.json")
        assert "<Redacted>" not in span.get_tag("_dd.appsec.json")


def test_obfuscation_parameter_value_configured_matching(tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env(
        dict(DD_APPSEC_OBFUSCATION_PARAMETER_VALUE_REGEXP="token")
    ):

        _enable_appsec(tracer)

        with tracer.trace("test", span_type=SpanTypes.WEB) as span:
            set_http_meta(span, Config(), raw_uri="http://example.com/.git?token=goodbye", status_code="404")

        assert "triggers" in json.loads(span.get_tag("_dd.appsec.json"))

        assert "token" not in span.get_tag("_dd.appsec.json")
        assert "goodbye" not in span.get_tag("_dd.appsec.json")
        assert "<Redacted>" in span.get_tag("_dd.appsec.json")


def test_ddwaf_run():
    with open(RULES_GOOD_PATH) as rules:
        rules_json = json.loads(rules.read())
        _ddwaf = DDWaf(rules_json, b"", b"")
        data = {
            "server.request.query": {},
            "server.request.headers.no_cookies": {"user-agent": "werkzeug/2.1.2", "host": "localhost"},
            "server.request.cookies": {"attack": "1' or '1' = '1'"},
            "server.response.headers.no_cookies": {"content-type": "text/html; charset=utf-8", "content-length": "207"},
        }
        res, total_time, total_overall_runtime = _ddwaf.run(data, DEFAULT_WAF_TIMEOUT)  # res is a serialized json
        print(total_time)
        print(total_overall_runtime)
        assert res.startswith('[{"rule":{"id":"crs-942-100"')
        assert total_time > 0
        assert total_overall_runtime > 0
        assert total_overall_runtime > total_time


def test_ddwaf_info():
    with open(RULES_GOOD_PATH) as rules:
        rules_json = json.loads(rules.read())
        _ddwaf = DDWaf(rules_json, b"", b"")

        info = _ddwaf.info
        assert info["loaded"] == 3
        assert info["failed"] == 0
        assert info["errors"] == {}
        assert info["version"] == ""


def test_ddwaf_info_with_2_errors():
    with open(os.path.join(ROOT_DIR, "rules-with-2-errors.json")) as rules:
        rules_json = json.loads(rules.read())
        _ddwaf = DDWaf(rules_json, b"", b"")

        info = _ddwaf.info
        assert info["loaded"] == 1
        assert info["failed"] == 2
        # Compare dict contents insensitive to ordering
        expected_dict = sorted(
            {"missing key 'conditions'": ["crs-913-110"], "missing key 'tags'": ["crs-942-100"]}.items()
        )
        assert sorted(info["errors"].items()) == expected_dict
        assert info["version"] == "5.5.5"


def test_ddwaf_info_with_3_errors():
    with open(os.path.join(ROOT_DIR, "rules-with-3-errors.json")) as rules:
        rules_json = json.loads(rules.read())
        _ddwaf = DDWaf(rules_json, b"", b"")

        info = _ddwaf.info
        assert info["loaded"] == 1
        assert info["failed"] == 2
        assert info["errors"] == {"missing key 'name'": ["crs-942-100", "crs-913-120"]}
