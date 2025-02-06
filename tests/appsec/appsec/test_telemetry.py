import os
from time import sleep
from unittest import mock

import pytest

import ddtrace.appsec._asm_request_context as asm_request_context
from ddtrace.appsec._ddwaf import version
import ddtrace.appsec._ddwaf.ddwaf_types
from ddtrace.appsec._deduplications import deduplication
from ddtrace.appsec._processor import AppSecSpanProcessor
from ddtrace.contrib.internal.trace_utils import set_http_meta
from ddtrace.ext import SpanTypes
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_DISTRIBUTION
from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_GENERATE_METRICS
from ddtrace.trace import tracer
import tests.appsec.rules as rules
from tests.appsec.utils import asm_context
from tests.utils import override_global_config


config_asm = {"_asm_enabled": True}
config_good_rules = {"_asm_static_rule_file": rules.RULES_GOOD_PATH, "_asm_enabled": True}
invalid_rule_update = {"rules": {"test": "invalid"}}
invalid_error = """appsec.waf.error::update::rules::bad cast, expected 'array', obtained 'map'"""


def _assert_generate_metrics(metrics_result, is_rule_triggered=False, is_blocked_request=False):
    generate_metrics = metrics_result[TELEMETRY_TYPE_GENERATE_METRICS][TELEMETRY_NAMESPACE.APPSEC.value]
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
    distributions_metrics = metrics_result[TELEMETRY_TYPE_DISTRIBUTION][TELEMETRY_NAMESPACE.APPSEC.value]

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
        tracer._configure(api_version="v0.4", appsec_enabled=False)
        telemetry_writer._namespace.flush()
        with tracer.trace("test", span_type=SpanTypes.WEB) as span:
            set_http_meta(
                span,
                rules.Config(),
            )
    metrics_data = telemetry_writer._namespace._metrics_data
    assert len(metrics_data[TELEMETRY_TYPE_GENERATE_METRICS][TELEMETRY_NAMESPACE.APPSEC.value]) == 0
    assert len(metrics_data[TELEMETRY_TYPE_DISTRIBUTION][TELEMETRY_NAMESPACE.APPSEC.value]) == 0


def test_metrics_when_appsec_runs(telemetry_writer, tracer):
    telemetry_writer._namespace.flush()
    with asm_context(tracer=tracer, span_name="test", config=config_asm) as span:
        set_http_meta(
            span,
            rules.Config(),
        )
    _assert_generate_metrics(telemetry_writer._namespace._metrics_data)


def test_metrics_when_appsec_attack(telemetry_writer, tracer):
    telemetry_writer._namespace.flush()
    with asm_context(tracer=tracer, span_name="test", config=config_good_rules) as span:
        set_http_meta(span, rules.Config(), request_cookies={"attack": "1' or '1' = '1'"})
    _assert_generate_metrics(telemetry_writer._namespace._metrics_data, is_rule_triggered=True)


def test_metrics_when_appsec_block(telemetry_writer, tracer):
    telemetry_writer._namespace.flush()
    with asm_context(tracer=tracer, ip_addr=rules._IP.BLOCKED, span_name="test", config=config_good_rules) as span:
        set_http_meta(
            span,
            rules.Config(),
        )
    _assert_generate_metrics(telemetry_writer._namespace._metrics_data, is_rule_triggered=True, is_blocked_request=True)


def test_log_metric_error_ddwaf_init(telemetry_writer):
    with override_global_config(
        dict(
            _asm_enabled=True,
            _asm_deduplication_enabled=False,
            _asm_static_rule_file=os.path.join(rules.ROOT_DIR, "rules-with-2-errors.json"),
        )
    ):
        processor = AppSecSpanProcessor()
        processor.delayed_init()

        list_metrics_logs = list(telemetry_writer._logs)
        assert len(list_metrics_logs) == 1
        assert (
            list_metrics_logs[0]["message"] == "appsec.waf.error::init::rules::"
            """{"missing key 'conditions'": ['crs-913-110'], "missing key 'tags'": ['crs-942-100']}"""
        )
        assert "waf_version:{}".format(version()) in list_metrics_logs[0]["tags"]


def test_log_metric_error_ddwaf_timeout(telemetry_writer, tracer):
    config = dict(
        _asm_enabled=True,
        _waf_timeout=0.0,
        _asm_deduplication_enabled=False,
        _asm_static_rule_file=rules.RULES_GOOD_PATH,
    )
    with asm_context(tracer=tracer, ip_addr=rules._IP.BLOCKED, span_name="test", config=config) as span:
        set_http_meta(
            span,
            rules.Config(),
        )

    list_metrics_logs = list(telemetry_writer._logs)
    assert len(list_metrics_logs) == 0

    generate_metrics = telemetry_writer._namespace._metrics_data[TELEMETRY_TYPE_GENERATE_METRICS][
        TELEMETRY_NAMESPACE.APPSEC.value
    ]

    timeout_found = False
    for _metric_id, metric in generate_metrics.items():
        if metric.name == "waf.requests":
            assert ("waf_timeout", "true") in metric._tags
            timeout_found = True
    assert timeout_found


def test_log_metric_error_ddwaf_update(telemetry_writer):
    with override_global_config(dict(_asm_enabled=True, _asm_deduplication_enabled=False)):
        span_processor = AppSecSpanProcessor()
        span_processor._update_rules(invalid_rule_update)

        list_metrics_logs = list(telemetry_writer._logs)
        assert len(list_metrics_logs) == 1
        assert list_metrics_logs[0]["message"] == invalid_error
        assert "waf_version:{}".format(version()) in list_metrics_logs[0]["tags"]


unpatched_run = ddtrace.appsec._ddwaf.ddwaf_types.ddwaf_run


def _wrapped_run(*args, **kwargs):
    unpatched_run(*args, **kwargs)
    return -3


@mock.patch.object(ddtrace.appsec._ddwaf, "ddwaf_run", new=_wrapped_run)
def test_log_metric_error_ddwaf_internal_error(telemetry_writer):
    """Test that an internal error is logged when the WAF returns an internal error."""
    with override_global_config(dict(_asm_enabled=True, _asm_deduplication_enabled=False)):
        with tracer.trace("test", span_type=SpanTypes.WEB, service="test") as span:
            span_processor = AppSecSpanProcessor()
            span_processor.on_span_start(span)
            asm_request_context._call_waf(span, {})
            list_metrics_logs = list(telemetry_writer._logs)
            assert len(list_metrics_logs) == 1
            assert list_metrics_logs[0]["message"] == "appsec.waf.request::error::-3"
            assert "waf_version:{}".format(version()) in list_metrics_logs[0]["tags"]


def test_log_metric_error_ddwaf_update_deduplication(telemetry_writer):
    with override_global_config(dict(_asm_enabled=True)):
        span_processor = AppSecSpanProcessor()
        span_processor._update_rules(invalid_rule_update)
        telemetry_writer.reset_queues()
        span_processor = AppSecSpanProcessor()
        span_processor._update_rules(invalid_rule_update)
        list_metrics_logs = list(telemetry_writer._logs)
        assert len(list_metrics_logs) == 0


def test_log_metric_error_ddwaf_update_deduplication_timelapse(telemetry_writer):
    old_value = deduplication._time_lapse
    deduplication._time_lapse = 0.1
    try:
        with override_global_config(dict(_asm_enabled=True)):
            sleep(0.2)
            span_processor = AppSecSpanProcessor()
            span_processor._update_rules(invalid_rule_update)
            list_metrics_logs = list(telemetry_writer._logs)
            assert len(list_metrics_logs) == 1
            assert list_metrics_logs[0]["message"] == invalid_error
            telemetry_writer.reset_queues()
            sleep(0.2)
            span_processor = AppSecSpanProcessor()
            span_processor._update_rules(invalid_rule_update)
            list_metrics_logs = list(telemetry_writer._logs)
            assert len(list_metrics_logs) == 1
    finally:
        deduplication._time_lapse = old_value
