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
from tests.appsec.utils import build_payload
from tests.utils import override_global_config


config_asm = {"_asm_enabled": True}
config_good_rules = {"_asm_static_rule_file": rules.RULES_GOOD_PATH, "_asm_enabled": True}
invalid_rule_update = [("ASM_DD", "Datadog/0/ASM/rules", {"rules": {"test": "invalid"}})]
invalid_error = """appsec.waf.error::update::rules::bad cast, expected 'array', obtained 'map'"""


def _assert_generate_metrics(metrics_result, is_rule_triggered=False, is_blocked_request=False, is_updated=0):
    metric_update = 0
    generate_metrics = metrics_result[TELEMETRY_TYPE_GENERATE_METRICS][TELEMETRY_NAMESPACE.APPSEC.value]
    assert (
        len(generate_metrics) == 3 + is_updated
    ), f"Expected {3 + is_updated} generate_metrics, got {[m['metric'] for m in generate_metrics]}"
    for metric in generate_metrics:
        metric_name = metric["metric"]
        if metric_name == "waf.requests":
            assert f"rule_triggered:{str(is_rule_triggered).lower()}" in metric["tags"]
            assert f"request_blocked:{str(is_blocked_request).lower()}" in metric["tags"]
            # assert not any(tag.startswith("request_truncated") for tag in metric.["tags"])
            assert "waf_timeout:false" in metric["tags"]
            assert f"waf_version:{version()}" in metric["tags"]
            assert any("event_rules_version:" in t for t in metric["tags"])
        elif metric_name == "waf.init":
            assert len(metric["points"]) == 1
            assert f"waf_version:{version()}" in metric["tags"]
            assert "success:true" in metric["tags"]
            assert any("event_rules_version" in t for t in metric["tags"])
            assert len(metric["tags"]) == 3
        elif metric_name == "waf.updates":
            assert len(metric["points"]) == 1
            assert f"waf_version:{version()}" in metric["tags"]
            assert "success:true" in metric["tags"]
            assert any("event_rules_version" in t for t in metric["tags"])
            assert len(metric["tags"]) == 3
            metric_update += 1
        elif metric_name == "api_security.missing_route":
            assert len(metric["points"]) == 1
            assert "framework:test" in metric["tags"] or "framework:flask" in metric["tags"], metric["tags"]
            assert len(metric["tags"]) == 1
        else:
            pytest.fail("Unexpected generate_metrics {}".format(metric_name))
    assert metric_update == is_updated


def _assert_distributions_metrics(metrics_result, is_rule_triggered=False, is_blocked_request=False):
    distributions_metrics = metrics_result[TELEMETRY_TYPE_DISTRIBUTION][TELEMETRY_NAMESPACE.APPSEC.value]

    assert len(distributions_metrics) == 2, "Expected 2 distributions_metrics"
    for metric in distributions_metrics:
        if metric["metric"] in ["waf.duration", "waf.duration_ext"]:
            assert len(metric["points"]) >= 1
            assert isinstance(metric["points"][0], float)
            assert f"rule_triggered:{str(is_rule_triggered).lower()}" in metric["tags"]
            assert f"request_blocked:{str(is_blocked_request).lower()}" in metric["tags"]
            assert f"waf_version:{version()}" in metric["tags"]
            assert any("event_rules_version" in t for t in metric["tags"])
        else:
            pytest.fail("Unexpected distributions_metrics {}".format(metric["metric"]))


def test_metrics_when_appsec_doesnt_runs(telemetry_writer, tracer):
    with override_global_config(dict(_asm_enabled=False)):
        tracer.configure(appsec_enabled=False)
        telemetry_writer._namespace.flush()
        with tracer.trace("test", span_type=SpanTypes.WEB) as span:
            set_http_meta(
                span,
                rules.Config(),
            )
    metrics_data = telemetry_writer._namespace.flush()
    assert len(metrics_data[TELEMETRY_TYPE_GENERATE_METRICS]) == 0
    assert len(metrics_data[TELEMETRY_TYPE_DISTRIBUTION]) == 0


def test_metrics_when_appsec_runs(telemetry_writer, tracer):
    telemetry_writer._namespace.flush()
    with asm_context(tracer=tracer, span_name="test", config=config_asm) as span:
        set_http_meta(
            span,
            rules.Config(),
        )
    _assert_generate_metrics(telemetry_writer._namespace.flush())


def test_metrics_when_appsec_attack(telemetry_writer, tracer):
    telemetry_writer._namespace.flush()
    with asm_context(tracer=tracer, span_name="test", config=config_good_rules) as span:
        set_http_meta(span, rules.Config(), request_cookies={"attack": "1' or '1' = '1'"})
    _assert_generate_metrics(telemetry_writer._namespace.flush(), is_rule_triggered=True)


def test_metrics_when_appsec_block(telemetry_writer, tracer):
    telemetry_writer._namespace.flush()
    with asm_context(tracer=tracer, ip_addr=rules._IP.BLOCKED, span_name="test", config=config_good_rules) as span:
        set_http_meta(span, rules.Config())
    _assert_generate_metrics(telemetry_writer._namespace.flush(), is_rule_triggered=True, is_blocked_request=True)


def test_metrics_when_appsec_block_custom(telemetry_writer, tracer):
    telemetry_writer._namespace.flush()
    with asm_context(tracer=tracer, ip_addr=rules._IP.BLOCKED, span_name="test", config=config_asm) as span:
        from ddtrace.appsec._remoteconfiguration import _appsec_callback

        actions = {
            "actions": [{"id": "block", "type": "block_request", "parameters": {"status_code": 429, "type": "json"}}]
        }
        _appsec_callback(
            [
                build_payload("ASM", actions, "actions"),
            ],
            tracer,
        )
        set_http_meta(span, rules.Config(), request_headers={"User-Agent": "Arachni/v1.5.1"})
    _assert_generate_metrics(
        telemetry_writer._namespace.flush(), is_rule_triggered=True, is_blocked_request=False, is_updated=1
    )


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

    generate_metrics = telemetry_writer._namespace.flush()[TELEMETRY_TYPE_GENERATE_METRICS][
        TELEMETRY_NAMESPACE.APPSEC.value
    ]

    timeout_found = False
    for metric in generate_metrics:
        if metric["metric"] == "waf.requests":
            assert "waf_timeout:true" in metric["tags"]
            timeout_found = True
    assert timeout_found


def test_log_metric_error_ddwaf_update(telemetry_writer):
    with override_global_config(dict(_asm_enabled=True, _asm_deduplication_enabled=False)):
        span_processor = AppSecSpanProcessor()
        span_processor._update_rules([], invalid_rule_update)

        list_metrics_logs = list(telemetry_writer._logs)
        assert len(list_metrics_logs) == 1
        assert list_metrics_logs[0]["message"] == invalid_error
        assert "waf_version:{}".format(version()) in list_metrics_logs[0]["tags"]


unpatched_run = ddtrace.appsec._ddwaf.ddwaf_types.ddwaf_run


def _wrapped_run(*args, **kwargs):
    unpatched_run(*args, **kwargs)
    return -3


@mock.patch.object(ddtrace.appsec._ddwaf.waf, "ddwaf_run", new=_wrapped_run)
def test_log_metric_error_ddwaf_internal_error(telemetry_writer):
    """Test that an internal error is logged when the WAF returns an internal error."""
    with override_global_config(dict(_asm_enabled=True, _asm_deduplication_enabled=False)):
        with tracer.trace("test", span_type=SpanTypes.WEB, service="test") as span:
            span_processor = AppSecSpanProcessor()
            span_processor.on_span_start(span)
            asm_request_context._call_waf(span, {})
            list_telemetry_logs = list(telemetry_writer._logs)
            assert len(list_telemetry_logs) == 0
            assert span.get_tag("_dd.appsec.waf.error") == "-3"
            metrics_result = telemetry_writer._namespace.flush()
            list_telemetry_metrics = metrics_result.get(TELEMETRY_TYPE_GENERATE_METRICS, {}).get(
                TELEMETRY_NAMESPACE.APPSEC.value, {}
            )
            error_metrics = [m for m in list_telemetry_metrics if m["metric"] == "waf.error"]
            assert len(error_metrics) == 1, error_metrics
            assert len(error_metrics[0]["tags"]) == 3
            assert f"waf_version:{version()}" in error_metrics[0]["tags"]
            assert "waf_error:-3" in error_metrics[0]["tags"]
            assert any(tag.startswith("event_rules_version:") for tag in error_metrics[0]["tags"])


def test_log_metric_error_ddwaf_update_deduplication(telemetry_writer):
    with override_global_config(dict(_asm_enabled=True)):
        span_processor = AppSecSpanProcessor()
        span_processor._update_rules([], invalid_rule_update)
        telemetry_writer.reset_queues()
        span_processor = AppSecSpanProcessor()
        span_processor._update_rules([], invalid_rule_update)
        list_metrics_logs = list(telemetry_writer._logs)
        assert len(list_metrics_logs) == 0


def test_log_metric_error_ddwaf_update_deduplication_timelapse(telemetry_writer):
    old_value = deduplication._time_lapse
    deduplication._time_lapse = 0.1
    try:
        with override_global_config(dict(_asm_enabled=True)):
            sleep(0.2)
            span_processor = AppSecSpanProcessor()
            span_processor._update_rules([], invalid_rule_update)
            list_metrics_logs = list(telemetry_writer._logs)
            assert len(list_metrics_logs) == 1
            assert list_metrics_logs[0]["message"] == invalid_error
            telemetry_writer.reset_queues()
            sleep(0.2)
            span_processor = AppSecSpanProcessor()
            span_processor._update_rules([], invalid_rule_update)
            list_metrics_logs = list(telemetry_writer._logs)
            assert len(list_metrics_logs) == 1
    finally:
        deduplication._time_lapse = old_value
