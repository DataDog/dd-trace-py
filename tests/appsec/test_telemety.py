import pytest

from ddtrace.appsec import _asm_request_context
from ddtrace.contrib.trace_utils import set_http_meta
from ddtrace.ext import SpanTypes
from ddtrace.internal.telemetry import telemetry_metrics_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE_TAG_APPSEC
from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_DISTRIBUTION
from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_GENERATE_METRICS
from tests.appsec.test_processor import Config
from tests.appsec.test_processor import RULES_GOOD_PATH
from tests.appsec.test_processor import _BLOCKED_IP
from tests.appsec.test_processor import _enable_appsec
from tests.utils import override_env
from tests.utils import override_global_config


@pytest.fixture
def mock_telemetry_metrics_writer():
    metrics_result = telemetry_metrics_writer._namespace._metrics_data
    assert len(metrics_result[TELEMETRY_TYPE_GENERATE_METRICS][TELEMETRY_NAMESPACE_TAG_APPSEC]) == 0
    assert len(metrics_result[TELEMETRY_TYPE_DISTRIBUTION][TELEMETRY_NAMESPACE_TAG_APPSEC]) == 0
    yield telemetry_metrics_writer
    telemetry_metrics_writer._flush_namespace_metrics()


def _assert_generate_metrics(metrics_result, is_rule_triggered=False, is_blocked_request=False):
    generate_metrics = metrics_result[TELEMETRY_TYPE_GENERATE_METRICS][TELEMETRY_NAMESPACE_TAG_APPSEC]
    assert len(generate_metrics) == 2, "Expected 2 generate_metrics"
    for metric_id, metric in generate_metrics.items():
        if metric.name == "waf.requests":
            assert metric._tags["rule_triggered"] is is_rule_triggered
            assert metric._tags["request_blocked"] is is_blocked_request
            assert len(metric._tags["waf_version"]) > 0
            assert len(metric._tags["event_rules_version"]) > 0
        elif metric.name == "event_rules.loaded":
            pass
        else:
            pytest.fail("Unexpected generate_metrics {}".format(metric.name))


def _assert_distributions_metrics(metrics_result, is_rule_triggered=False, is_blocked_request=False):
    distributions_metrics = metrics_result[TELEMETRY_TYPE_DISTRIBUTION][TELEMETRY_NAMESPACE_TAG_APPSEC]

    assert len(distributions_metrics) == 2, "Expected 2 distributions_metrics"
    for metric_id, metric in distributions_metrics.items():
        if metric.name in ["waf.duration", "waf.duration_ext"]:
            assert len(metric._points) >= 1
            assert type(metric._points[0]) is float
            assert metric._tags["rule_triggered"] is is_rule_triggered
            assert metric._tags["request_blocked"] is is_blocked_request
            assert len(metric._tags["waf_version"]) > 0
            assert len(metric._tags["event_rules_version"]) > 0
        else:
            pytest.fail("Unexpected distributions_metrics {}".format(metric.name))


def test_metrics_when_appsec_doesnt_runs(mock_telemetry_metrics_writer, tracer):
    with override_global_config(dict(_appsec_enabled=False, _telemetry_metrics_enabled=True)):
        tracer.configure(api_version="v0.4", appsec_enabled=False)
        mock_telemetry_metrics_writer._flush_namespace_metrics()
        with tracer.trace("test", span_type=SpanTypes.WEB) as span:
            set_http_meta(
                span,
                Config(),
            )
    metrics_data = mock_telemetry_metrics_writer._namespace._metrics_data
    assert len(metrics_data[TELEMETRY_TYPE_GENERATE_METRICS][TELEMETRY_NAMESPACE_TAG_APPSEC]) == 0
    assert len(metrics_data[TELEMETRY_TYPE_DISTRIBUTION][TELEMETRY_NAMESPACE_TAG_APPSEC]) == 0


def test_metrics_when_appsec_runs(mock_telemetry_metrics_writer, tracer):
    with override_global_config(dict(_appsec_enabled=True, _telemetry_metrics_enabled=True)):
        _enable_appsec(tracer)
        with tracer.trace("test", span_type=SpanTypes.WEB) as span:
            set_http_meta(
                span,
                Config(),
            )
    _assert_generate_metrics(mock_telemetry_metrics_writer._namespace._metrics_data)
    _assert_distributions_metrics(mock_telemetry_metrics_writer._namespace._metrics_data)


def test_metrics_when_appsec_attack(mock_telemetry_metrics_writer, tracer):
    with override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)), override_global_config(
        dict(_appsec_enabled=True, _telemetry_metrics_enabled=True)
    ):
        _enable_appsec(tracer)
        with tracer.trace("test", span_type=SpanTypes.WEB) as span:
            set_http_meta(span, Config(), request_cookies={"attack": "1' or '1' = '1'"})
    _assert_generate_metrics(mock_telemetry_metrics_writer._namespace._metrics_data, is_rule_triggered=True)
    _assert_distributions_metrics(mock_telemetry_metrics_writer._namespace._metrics_data, is_rule_triggered=True)


def test_metrics_when_appsec_block(mock_telemetry_metrics_writer, tracer):
    with override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)), override_global_config(
        dict(_appsec_enabled=True, _telemetry_metrics_enabled=True)
    ):
        _enable_appsec(tracer)
        with _asm_request_context.asm_request_context_manager(_BLOCKED_IP, {}):
            with tracer.trace("test", span_type=SpanTypes.WEB) as span:
                set_http_meta(
                    span,
                    Config(),
                )

    _assert_generate_metrics(
        mock_telemetry_metrics_writer._namespace._metrics_data, is_rule_triggered=True, is_blocked_request=True
    )
    _assert_distributions_metrics(
        mock_telemetry_metrics_writer._namespace._metrics_data, is_rule_triggered=True, is_blocked_request=True
    )
