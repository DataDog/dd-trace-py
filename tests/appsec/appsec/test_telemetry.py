import os
from time import sleep

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
from tests.appsec.appsec.test_processor import _enable_appsec
import tests.appsec.rules as rules
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


def test_metrics_when_appsec_doesnt_runs(telemetry_writer, tracer):
    with override_global_config(dict(_asm_enabled=False)):
        tracer.configure(api_version="v0.4", appsec_enabled=False)
        telemetry_writer._namespace.flush()
        with tracer.trace("test", span_type=SpanTypes.WEB) as span:
            set_http_meta(
                span,
                rules.Config(),
            )
    metrics_data = telemetry_writer._namespace._metrics_data
    assert len(metrics_data[TELEMETRY_TYPE_GENERATE_METRICS][TELEMETRY_NAMESPACE_TAG_APPSEC]) == 0
    assert len(metrics_data[TELEMETRY_TYPE_DISTRIBUTION][TELEMETRY_NAMESPACE_TAG_APPSEC]) == 0


def test_metrics_when_appsec_runs(telemetry_writer, tracer):
    with override_global_config(dict(_asm_enabled=True)):
        telemetry_writer._namespace.flush()
        _enable_appsec(tracer)
        with tracer.trace("test", span_type=SpanTypes.WEB) as span:
            set_http_meta(
                span,
                rules.Config(),
            )
    _assert_generate_metrics(telemetry_writer._namespace._metrics_data)


def test_metrics_when_appsec_attack(telemetry_writer, tracer):
    with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_GOOD_PATH)):
        telemetry_writer._namespace.flush()
        _enable_appsec(tracer)
        with tracer.trace("test", span_type=SpanTypes.WEB) as span:
            set_http_meta(span, rules.Config(), request_cookies={"attack": "1' or '1' = '1'"})
    _assert_generate_metrics(telemetry_writer._namespace._metrics_data, is_rule_triggered=True)


def test_metrics_when_appsec_block(telemetry_writer, tracer):
    with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_GOOD_PATH)):
        telemetry_writer._namespace.flush()
        _enable_appsec(tracer)
        with _asm_request_context.asm_request_context_manager(rules._IP.BLOCKED, {}):
            with tracer.trace("test", span_type=SpanTypes.WEB) as span:
                set_http_meta(
                    span,
                    rules.Config(),
                )

    _assert_generate_metrics(telemetry_writer._namespace._metrics_data, is_rule_triggered=True, is_blocked_request=True)


def test_log_metric_error_ddwaf_init(telemetry_writer):
    with override_global_config(
        dict(
            _asm_enabled=True,
            _deduplication_enabled=False,
            _asm_static_rule_file=os.path.join(rules.ROOT_DIR, "rules-with-2-errors.json"),
        )
    ):
        AppSecSpanProcessor()

        list_metrics_logs = list(telemetry_writer._logs)
        assert len(list_metrics_logs) == 1
        assert list_metrics_logs[0]["message"] == "WAF init error. Invalid rules"
        assert list_metrics_logs[0]["stack_trace"].startswith("DDWAF.__init__: invalid rules")
        assert "waf_version:{}".format(version()) in list_metrics_logs[0]["tags"]


def test_log_metric_error_ddwaf_timeout(telemetry_writer, tracer):
    with override_global_config(
        dict(
            _asm_enabled=True,
            _waf_timeout=0.0,
            _deduplication_enabled=False,
            _asm_static_rule_file=rules.RULES_GOOD_PATH,
        )
    ):
        _enable_appsec(tracer)
        with _asm_request_context.asm_request_context_manager(rules._IP.BLOCKED, {}):
            with tracer.trace("test", span_type=SpanTypes.WEB) as span:
                set_http_meta(
                    span,
                    rules.Config(),
                )

        list_metrics_logs = list(telemetry_writer._logs)
        assert len(list_metrics_logs) == 0

        generate_metrics = telemetry_writer._namespace._metrics_data[TELEMETRY_TYPE_GENERATE_METRICS][
            TELEMETRY_NAMESPACE_TAG_APPSEC
        ]

        timeout_found = False
        for _metric_id, metric in generate_metrics.items():
            if metric.name == "waf.requests":
                assert ("waf_timeout", "true") in metric._tags
                timeout_found = True
        assert timeout_found


def test_log_metric_error_ddwaf_update(telemetry_writer):
    with override_global_config(dict(_asm_enabled=True, _deduplication_enabled=False)):
        span_processor = AppSecSpanProcessor()
        span_processor._update_rules({})

        list_metrics_logs = list(telemetry_writer._logs)
        assert len(list_metrics_logs) == 1
        assert list_metrics_logs[0]["message"] == "Error updating ASM rules. Invalid rules"
        assert list_metrics_logs[0].get("stack_trace") is None
        assert "waf_version:{}".format(version()) in list_metrics_logs[0]["tags"]


def test_log_metric_error_ddwaf_update_deduplication(telemetry_writer):
    with override_global_config(dict(_asm_enabled=True)):
        span_processor = AppSecSpanProcessor()
        span_processor._update_rules({})
        telemetry_writer.reset_queues()
        span_processor = AppSecSpanProcessor()
        span_processor._update_rules({})
        list_metrics_logs = list(telemetry_writer._logs)
        assert len(list_metrics_logs) == 0


def test_log_metric_error_ddwaf_update_deduplication_timelapse(telemetry_writer):
    old_value = deduplication._time_lapse
    deduplication._time_lapse = 0.1
    try:
        with override_global_config(dict(_asm_enabled=True)):
            sleep(0.2)
            span_processor = AppSecSpanProcessor()
            span_processor._update_rules({})
            list_metrics_logs = list(telemetry_writer._logs)
            assert len(list_metrics_logs) == 1
            telemetry_writer.reset_queues()
            sleep(0.2)
            span_processor = AppSecSpanProcessor()
            span_processor._update_rules({})
            list_metrics_logs = list(telemetry_writer._logs)
            assert len(list_metrics_logs) == 1
    finally:
        deduplication._time_lapse = old_value
