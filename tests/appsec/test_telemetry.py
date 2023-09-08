import os
import sys
from time import sleep

import mock
import pytest

from ddtrace.appsec import _asm_request_context
from ddtrace.appsec._ddwaf import version
from ddtrace.appsec._deduplications import deduplication
from ddtrace.appsec._processor import AppSecSpanProcessor
from ddtrace.contrib.trace_utils import set_http_meta
from ddtrace.ext import SpanTypes
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE_TAG_APPSEC
from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_DISTRIBUTION
from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_GENERATE_METRICS
from tests.appsec.test_processor import Config
from tests.appsec.test_processor import ROOT_DIR
from tests.appsec.test_processor import RULES_GOOD_PATH
from tests.appsec.test_processor import _IP
from tests.appsec.test_processor import _enable_appsec
from tests.utils import override_env
from tests.utils import override_global_config


def _assert_generate_metrics(metrics_result, is_rule_triggered=False, is_blocked_request=False):
    generate_metrics = metrics_result[TELEMETRY_TYPE_GENERATE_METRICS][TELEMETRY_NAMESPACE_TAG_APPSEC]
    assert len(generate_metrics) == 2, "Expected 2 generate_metrics"
    for _metric_id, metric in generate_metrics.items():
        if metric.name == "waf.requests":
            assert ("rule_triggered", str(is_rule_triggered).lower()) in metric._tags
            assert ("request_blocked", str(is_blocked_request).lower()) in metric._tags
            # assert metric._tags["request_truncated"] is False
            assert ("waf_timeout", "false") in metric._tags
            assert ("waf_version", version()) in metric._tags
            assert any("event_rules_version" in k for k, v in metric._tags)
        elif metric.name == "waf.init":
            assert len(metric._points) == 1
        else:
            pytest.fail("Unexpected generate_metrics {}".format(metric.name))


def _assert_distributions_metrics(metrics_result, is_rule_triggered=False, is_blocked_request=False):
    distributions_metrics = metrics_result[TELEMETRY_TYPE_DISTRIBUTION][TELEMETRY_NAMESPACE_TAG_APPSEC]

    assert len(distributions_metrics) == 2, "Expected 2 distributions_metrics"
    for _metric_id, metric in distributions_metrics.items():
        if metric.name in ["waf.duration", "waf.duration_ext"]:
            assert len(metric._points) >= 1
            assert type(metric._points[0]) is float
            assert metric._tags["rule_triggered"] == str(is_rule_triggered).lower()
            assert metric._tags["request_blocked"] == str(is_blocked_request).lower()
            assert len(metric._tags["waf_version"]) > 0
            assert len(metric._tags["event_rules_version"]) > 0
        else:
            pytest.fail("Unexpected distributions_metrics {}".format(metric.name))


def test_metrics_when_appsec_doesnt_runs(mock_telemetry_lifecycle_writer, tracer):
    with override_global_config(dict(_appsec_enabled=False)):
        tracer.configure(api_version="v0.4", appsec_enabled=False)
        mock_telemetry_lifecycle_writer._namespace.flush()
        with tracer.trace("test", span_type=SpanTypes.WEB) as span:
            set_http_meta(
                span,
                Config(),
            )
    metrics_data = mock_telemetry_lifecycle_writer._namespace._metrics_data
    assert len(metrics_data[TELEMETRY_TYPE_GENERATE_METRICS][TELEMETRY_NAMESPACE_TAG_APPSEC]) == 0
    assert len(metrics_data[TELEMETRY_TYPE_DISTRIBUTION][TELEMETRY_NAMESPACE_TAG_APPSEC]) == 0


def test_metrics_when_appsec_runs(mock_telemetry_lifecycle_writer, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        mock_telemetry_lifecycle_writer._namespace.flush()
        _enable_appsec(tracer)
        with tracer.trace("test", span_type=SpanTypes.WEB) as span:
            set_http_meta(
                span,
                Config(),
            )
    _assert_generate_metrics(mock_telemetry_lifecycle_writer._namespace._metrics_data)


def test_metrics_when_appsec_attack(mock_telemetry_lifecycle_writer, tracer):
    with override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)), override_global_config(dict(_appsec_enabled=True)):
        mock_telemetry_lifecycle_writer._namespace.flush()
        _enable_appsec(tracer)
        with tracer.trace("test", span_type=SpanTypes.WEB) as span:
            set_http_meta(span, Config(), request_cookies={"attack": "1' or '1' = '1'"})
    _assert_generate_metrics(mock_telemetry_lifecycle_writer._namespace._metrics_data, is_rule_triggered=True)


def test_metrics_when_appsec_block(mock_telemetry_lifecycle_writer, tracer):
    with override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)), override_global_config(dict(_appsec_enabled=True)):
        mock_telemetry_lifecycle_writer._namespace.flush()
        _enable_appsec(tracer)
        with _asm_request_context.asm_request_context_manager(_IP.BLOCKED, {}):
            with tracer.trace("test", span_type=SpanTypes.WEB) as span:
                set_http_meta(
                    span,
                    Config(),
                )

    _assert_generate_metrics(
        mock_telemetry_lifecycle_writer._namespace._metrics_data, is_rule_triggered=True, is_blocked_request=True
    )


def test_log_metric_error_ddwaf_init(mock_logs_telemetry_lifecycle_writer):
    with override_global_config(dict(_appsec_enabled=True)), override_env(
        dict(
            _DD_APPSEC_DEDUPLICATION_ENABLED="false", DD_APPSEC_RULES=os.path.join(ROOT_DIR, "rules-with-2-errors.json")
        )
    ):
        AppSecSpanProcessor()

        list_metrics_logs = list(mock_logs_telemetry_lifecycle_writer._logs)
        assert len(list_metrics_logs) == 1
        assert list_metrics_logs[0]["message"] == "WAF init error. Invalid rules"
        assert list_metrics_logs[0]["stack_trace"].startswith("DDWAF.__init__: invalid rules")
        assert "waf_version:{}".format(version()) in list_metrics_logs[0]["tags"]


def test_log_metric_error_ddwaf_timeout(mock_logs_telemetry_lifecycle_writer, tracer):
    with override_env(
        dict(_DD_APPSEC_DEDUPLICATION_ENABLED="false", DD_APPSEC_RULES=RULES_GOOD_PATH)
    ), override_global_config(dict(_appsec_enabled=True, _waf_timeout=0.0)):
        _enable_appsec(tracer)
        with _asm_request_context.asm_request_context_manager(_IP.BLOCKED, {}):
            with tracer.trace("test", span_type=SpanTypes.WEB) as span:
                set_http_meta(
                    span,
                    Config(),
                )

        list_metrics_logs = list(mock_logs_telemetry_lifecycle_writer._logs)
        assert len(list_metrics_logs) == 1
        assert list_metrics_logs[0]["message"] == "WAF run. Timeout errors"
        assert list_metrics_logs[0].get("stack_trace") is None
        assert "waf_version:{}".format(version()) in list_metrics_logs[0]["tags"]


@pytest.mark.skipif(sys.version_info < (3, 6, 0), reason="Python 3.6+ only")
def test_log_metric_error_ddwaf_update(mock_logs_telemetry_lifecycle_writer):
    with override_env(dict(_DD_APPSEC_DEDUPLICATION_ENABLED="false")), override_global_config(
        dict(_appsec_enabled=True)
    ):
        span_processor = AppSecSpanProcessor()
        span_processor._update_rules({})

        list_metrics_logs = list(mock_logs_telemetry_lifecycle_writer._logs)
        assert len(list_metrics_logs) == 1
        assert list_metrics_logs[0]["message"] == "Error updating ASM rules. Invalid rules"
        assert list_metrics_logs[0].get("stack_trace") is None
        assert "waf_version:{}".format(version()) in list_metrics_logs[0]["tags"]


@pytest.mark.skipif(sys.version_info < (3, 6, 0), reason="Python 3.6+ only")
def test_log_metric_error_ddwaf_update_deduplication(mock_logs_telemetry_lifecycle_writer):
    with override_global_config(dict(_appsec_enabled=True)):
        span_processor = AppSecSpanProcessor()
        span_processor._update_rules({})
        mock_logs_telemetry_lifecycle_writer.reset_queues()
        span_processor = AppSecSpanProcessor()
        span_processor._update_rules({})
        list_metrics_logs = list(mock_logs_telemetry_lifecycle_writer._logs)
        assert len(list_metrics_logs) == 0


@pytest.mark.skipif(sys.version_info < (3, 6, 0), reason="Python 3.6+ only")
@mock.patch.object(deduplication, "get_last_time_reported")
def test_log_metric_error_ddwaf_update_deduplication_timelapse(
    mock_last_time_reported, mock_logs_telemetry_lifecycle_writer
):
    old_value = deduplication._time_lapse
    deduplication._time_lapse = 0.3
    mock_last_time_reported.return_value = 1592357416.0
    try:
        with override_global_config(dict(_appsec_enabled=True)):
            span_processor = AppSecSpanProcessor()
            span_processor._update_rules({})
            mock_logs_telemetry_lifecycle_writer.reset_queues()
            sleep(0.4)
            span_processor = AppSecSpanProcessor()
            span_processor._update_rules({})
            list_metrics_logs = list(mock_logs_telemetry_lifecycle_writer._logs)
            assert len(list_metrics_logs) == 1
    finally:
        deduplication._time_lapse = old_value
