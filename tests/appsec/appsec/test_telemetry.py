import base64
import os
import struct
from time import sleep
from unittest import mock

import pytest

import ddtrace.appsec._asm_request_context as asm_request_context
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
import ddtrace.appsec._ddwaf.ddwaf_types
import ddtrace.appsec._ddwaf.waf
from ddtrace.appsec._deduplications import deduplication
from ddtrace.appsec._processor import AppSecSpanProcessor
from ddtrace.appsec._remoteconfiguration import AppSecCallback
from ddtrace.appsec._utils import DDWaf_result
from ddtrace.appsec._utils import _observator
from ddtrace.constants import APPSEC_ENV
from ddtrace.contrib.internal.trace_utils import set_http_meta
from ddtrace.ext import SpanTypes
from ddtrace.internal.appsec.product import _disable_asm
from ddtrace.internal.appsec.product import _enable_asm
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.trace import tracer
import tests.appsec.rules as rules
from tests.appsec.utils import asm_context
from tests.appsec.utils import build_payload
from tests.utils import override_env
from tests.utils import override_global_config


config_asm = {"_asm_enabled": True}
config_good_rules = {"_asm_static_rule_file": rules.RULES_GOOD_PATH, "_asm_enabled": True}
invalid_rule_update = [("ASM_DD", "Datadog/0/ASM/rules", {"rules": {"test": "invalid"}})]
invalid_error = """appsec.waf.error::update::rules::bad cast, expected 'array', obtained 'map'"""


# The native worker serializes distributions ("sketches" request type) as a base64-encoded
# DDSketch protobuf rather than a raw list of points. The values themselves are histogrammed
# and lossy, but the total number of recorded values is recoverable by summing the store bin
# counts (plus any zero count). We use that to assert how many points landed in a series.
def _read_varint(b, i):
    shift = 0
    result = 0
    while True:
        byte = b[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            break
        shift += 7
    return result, i


def _decode_store_count(b):
    i = 0
    total = 0.0
    while i < len(b):
        tag, i = _read_varint(b, i)
        field = tag >> 3
        wt = tag & 7
        if wt == 2:
            ln, i = _read_varint(b, i)
            sub = b[i : i + ln]
            i += ln
            if field == 2:  # contiguousBinCounts: packed doubles
                total += sum(struct.unpack("<%dd" % (ln // 8), sub))
        elif wt == 0:
            _, i = _read_varint(b, i)
        elif wt == 1:
            i += 8
        elif wt == 5:
            i += 4
    return total


def _decode_sketch_count(sketch_b64):
    b = base64.b64decode(sketch_b64)
    i = 0
    total = 0.0
    while i < len(b):
        tag, i = _read_varint(b, i)
        field = tag >> 3
        wt = tag & 7
        if wt == 2:
            ln, i = _read_varint(b, i)
            sub = b[i : i + ln]
            i += ln
            if field in (2, 3):  # positiveValues / negativeValues stores
                total += _decode_store_count(sub)
        elif wt == 0:
            _, i = _read_varint(b, i)
        elif wt == 1:
            v = struct.unpack("<d", b[i : i + 8])[0]
            i += 8
            if field == 4:  # zeroCount
                total += v
        elif wt == 5:
            i += 4
    return total


def _get_appsec_metrics(test_agent_session, telemetry_writer):
    """Flush the native worker and return the appsec-namespace generate-metrics series.

    The in-process telemetry_writer fixture also emits the ``enabled`` metric on each worker
    interval, which is noise for these tests, so it is filtered out (the previous Cython-based
    assertion did the same).
    """
    telemetry_writer.periodic(force_flush=True)
    metrics = []
    for series in test_agent_session.get_metrics():
        if series.get("namespace") != TELEMETRY_NAMESPACE.APPSEC.value:
            continue
        if series["metric"] == "enabled":
            continue
        metrics.append(series)
    return metrics


def _get_appsec_distributions(test_agent_session, telemetry_writer):
    """Flush and return the appsec-namespace distribution ("sketches") series with decoded counts."""
    telemetry_writer.periodic(force_flush=True)
    series = []
    for event in test_agent_session.get_events("sketches"):
        for s in event["payload"]["series"]:
            if s.get("namespace") == TELEMETRY_NAMESPACE.APPSEC.value:
                series.append(s)
    return series


def _get_appsec_logs(test_agent_session, telemetry_writer):
    """Flush and return all telemetry log entries captured by the session."""
    telemetry_writer.periodic(force_flush=True)
    logs = []
    for event in test_agent_session.get_events("logs"):
        logs += event["payload"]["logs"]
    return logs


def _assert_generate_metrics(generate_metrics, is_rule_triggered=False, is_blocked_request=False, expected_name=[]):
    version = asm_config._ddwaf_version

    names = sorted([m["metric"] for m in generate_metrics])
    expected = sorted(expected_name)
    assert names == expected, f"Expected metrics names {expected}, got {names}"
    for metric in generate_metrics:
        metric_name = metric["metric"]
        if metric_name == "waf.requests":
            assert f"rule_triggered:{str(is_rule_triggered).lower()}" in metric["tags"]
            assert f"request_blocked:{str(is_blocked_request).lower()}" in metric["tags"]
            assert "waf_timeout:false" in metric["tags"]
            assert f"waf_version:{version}" in metric["tags"]
            assert any("event_rules_version:" in t for t in metric["tags"])
        elif metric_name == "waf.init":
            assert len(metric["points"]) == 1
            assert f"waf_version:{version}" in metric["tags"]
            assert "success:true" in metric["tags"]
            assert any("event_rules_version" in t for t in metric["tags"])
            assert len(metric["tags"]) == 3
        elif metric_name == "waf.updates":
            assert len(metric["points"]) == 1
            assert f"waf_version:{version}" in metric["tags"]
            assert "success:true" in metric["tags"]
            assert any("event_rules_version" in t for t in metric["tags"])
            assert len(metric["tags"]) == 3
        elif metric_name == "api_security.missing_route":
            assert len(metric["points"]) == 1
            assert "framework:test" in metric["tags"] or "framework:flask" in metric["tags"], metric["tags"]
            assert len(metric["tags"]) == 1
        else:
            pytest.fail("Unexpected generate_metrics {}".format(metric_name))


def test_metrics_when_appsec_doesnt_runs(telemetry_writer, test_agent_session, tracer):
    with override_global_config(dict(_asm_enabled=False)):
        tracer.configure(appsec_enabled=False)
        with tracer.trace("test", span_type=SpanTypes.WEB) as span:
            set_http_meta(
                span,
                rules.Config(),
            )
    assert _get_appsec_metrics(test_agent_session, telemetry_writer) == []
    assert _get_appsec_distributions(test_agent_session, telemetry_writer) == []


def test_metrics_when_appsec_runs(telemetry_writer, test_agent_session, tracer):
    with asm_context(tracer=tracer, span_name="test", config=config_asm) as span:
        set_http_meta(
            span,
            rules.Config(),
        )
    _assert_generate_metrics(
        _get_appsec_metrics(test_agent_session, telemetry_writer),
        expected_name=["api_security.missing_route", "waf.init", "waf.requests"],
    )


def test_metrics_when_appsec_attack(telemetry_writer, test_agent_session, tracer):
    with asm_context(tracer=tracer, span_name="test", config=config_good_rules) as span:
        set_http_meta(span, rules.Config(), request_cookies={"attack": "1' or '1' = '1'"})
    _assert_generate_metrics(
        _get_appsec_metrics(test_agent_session, telemetry_writer),
        is_rule_triggered=True,
        expected_name=["api_security.missing_route", "waf.init", "waf.requests"],
    )


def test_metrics_when_appsec_block(telemetry_writer, test_agent_session, tracer):
    with asm_context(tracer=tracer, ip_addr=rules._IP.BLOCKED, span_name="test", config=config_good_rules) as span:
        set_http_meta(span, rules.Config())
    _assert_generate_metrics(
        _get_appsec_metrics(test_agent_session, telemetry_writer),
        is_rule_triggered=True,
        is_blocked_request=True,
        expected_name=["waf.init", "waf.requests"],
    )


def test_metrics_when_appsec_block_custom(telemetry_writer, test_agent_session, tracer):
    appsec_callback = AppSecCallback(_enable_asm, _disable_asm)
    with asm_context(tracer=tracer, ip_addr=rules._IP.BLOCKED, span_name="test", config=config_asm) as span:
        actions = {
            "actions": [{"id": "block", "type": "block_request", "parameters": {"status_code": 429, "type": "json"}}]
        }
        appsec_callback(
            [
                build_payload("ASM", actions, "actions"),
            ],
        )
        # using a header to trigger the block of default rules
        set_http_meta(span, rules.Config(), request_headers={"User-Agent": "dd-test-scanner-log-block"})
    _assert_generate_metrics(
        _get_appsec_metrics(test_agent_session, telemetry_writer),
        is_rule_triggered=True,
        is_blocked_request=True,
        expected_name=["waf.init", "waf.requests", "waf.updates"],
    )


@pytest.mark.parametrize(
    "user_id,user_login,report_missing_login,expected_metric_names",
    [
        # both absent, login reporting enabled → both fire
        (None, None, True, {"instrum.user_auth.missing_user_login", "instrum.user_auth.missing_user_id"}),
        # both absent, login reporting disabled → only missing_user_id fires
        (None, None, False, {"instrum.user_auth.missing_user_id"}),
        # id present, login absent → only missing_user_login (present id suppresses missing_user_id)
        ("123", None, True, {"instrum.user_auth.missing_user_login"}),
        # login present, id absent → no metrics (login is a valid fallback for id)
        (None, "fred", True, set()),
        # both present → no metrics (happy path)
        ("123", "fred", True, set()),
    ],
)
def test_report_user_auth_missing(
    telemetry_writer, test_agent_session, user_id, user_login, report_missing_login, expected_metric_names
):
    from ddtrace.appsec import _metrics

    _metrics.report_user_auth_missing("django", "login_failure", user_id, user_login, report_missing_login)

    metrics = _get_appsec_metrics(test_agent_session, telemetry_writer)
    user_auth_metrics = [m for m in metrics if m["metric"].startswith("instrum.user_auth.")]
    assert {m["metric"] for m in user_auth_metrics} == expected_metric_names
    assert len(user_auth_metrics) == len(expected_metric_names), user_auth_metrics
    for metric in user_auth_metrics:
        assert "framework:django" in metric["tags"]
        assert "event_type:login_failure" in metric["tags"]


def test_waf_duration_distribution_metrics(telemetry_writer, test_agent_session, tracer):
    with asm_context(tracer=tracer, span_name="test", config=config_asm) as span:
        set_http_meta(span, rules.Config())

    distributions_metrics = _get_appsec_distributions(test_agent_session, telemetry_writer)
    waf_metrics = {metric["metric"]: metric for metric in distributions_metrics if metric["metric"].startswith("waf.")}

    assert set(waf_metrics) == {"waf.duration", "waf.duration_ext"}
    for metric in waf_metrics.values():
        # The native worker stores distributions as a DDSketch; assert at least one value was
        # recorded rather than on the (lossy) value itself.
        assert _decode_sketch_count(metric["sketch_b64"]) >= 1
        assert f"waf_version:{asm_config._ddwaf_version}" in metric["tags"]
        assert any(tag.startswith("event_rules_version:") for tag in metric["tags"])
        assert len(metric["tags"]) == 2


def test_rasp_duration_distribution_metrics(telemetry_writer, test_agent_session, tracer):
    with asm_context(tracer=tracer, span_name="test", config=config_asm):
        waf_result = DDWaf_result(0, [], {}, 12.5, 20.25, False, _observator(), {})
        asm_request_context.set_waf_telemetry_results(
            "rules_rasp",
            False,
            waf_result,
            EXPLOIT_PREVENTION.TYPE.SQLI,
            False,
        )
        waf_result = DDWaf_result(0, [], {}, 3.0, 4.0, False, _observator(), {})
        asm_request_context.set_waf_telemetry_results(
            "rules_rasp",
            False,
            waf_result,
            EXPLOIT_PREVENTION.TYPE.LFI,
            False,
        )

    distributions_metrics = _get_appsec_distributions(test_agent_session, telemetry_writer)
    rasp_metrics = {
        metric["metric"]: metric for metric in distributions_metrics if metric["metric"].startswith("rasp.")
    }

    assert set(rasp_metrics) == {"rasp.duration", "rasp.duration_ext"}
    # The two RASP runs (SQLI + LFI) are accumulated into a single per-request duration value
    # (12.5 + 3.0 = 15.5 and 20.25 + 4.0 = 24.25) and emitted as one distribution point each.
    # The native worker stores distributions as a DDSketch (lossy on the value), so assert that
    # exactly one value was recorded rather than on the summed value itself.
    assert _decode_sketch_count(rasp_metrics["rasp.duration"]["sketch_b64"]) == 1
    assert _decode_sketch_count(rasp_metrics["rasp.duration_ext"]["sketch_b64"]) == 1
    for metric in rasp_metrics.values():
        assert f"waf_version:{asm_config._ddwaf_version}" in metric["tags"]
        assert any(tag.startswith("event_rules_version:") for tag in metric["tags"])
        assert len(metric["tags"]) == 2


def test_log_metric_error_ddwaf_init(telemetry_writer, test_agent_session):
    with override_global_config(
        dict(
            _asm_enabled=True,
            _asm_deduplication_enabled=False,
            _asm_static_rule_file=os.path.join(rules.ROOT_DIR, "rules-with-2-errors.json"),
        )
    ):
        processor = AppSecSpanProcessor()
        processor.delayed_init()

    list_metrics_logs = _get_appsec_logs(test_agent_session, telemetry_writer)
    init_logs = [
        log
        for log in list_metrics_logs
        if log["message"] == "appsec.waf.error::init::rules::"
        """{"missing key 'conditions'": ['crs-913-110'], "missing key 'tags'": ['crs-942-100']}"""
    ]
    assert len(init_logs) == 1
    assert "waf_version:{}".format(asm_config._ddwaf_version) in init_logs[0]["tags"]


def test_log_metric_error_ddwaf_timeout(telemetry_writer, test_agent_session, tracer):
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

    generate_metrics = _get_appsec_metrics(test_agent_session, telemetry_writer)

    timeout_found = False
    for metric in generate_metrics:
        if metric["metric"] == "waf.requests":
            assert "waf_timeout:true" in metric["tags"]
            timeout_found = True
    assert timeout_found


def test_log_metric_error_ddwaf_update(telemetry_writer, test_agent_session):
    with override_global_config(dict(_asm_enabled=True, _asm_deduplication_enabled=False)):
        span_processor = AppSecSpanProcessor()
        span_processor._update_rules([], invalid_rule_update)

    list_metrics_logs = _get_appsec_logs(test_agent_session, telemetry_writer)
    update_logs = [log for log in list_metrics_logs if log["message"] == invalid_error]
    assert len(update_logs) == 1
    assert "waf_version:{}".format(asm_config._ddwaf_version) in update_logs[0]["tags"]


unpatched_run = ddtrace.appsec._ddwaf.ddwaf_types.ddwaf_context_eval


def _wrapped_run(*args, **kwargs):
    unpatched_run(*args, **kwargs)
    return -3


@mock.patch.object(ddtrace.appsec._ddwaf.waf, "ddwaf_context_eval", new=_wrapped_run)
def test_log_metric_error_ddwaf_internal_error(telemetry_writer, test_agent_session):
    """Test that an internal error is logged when the WAF returns an internal error."""

    with override_global_config(dict(_asm_enabled=True, _asm_deduplication_enabled=False)):
        with tracer.trace("test", span_type=SpanTypes.WEB, service="test") as span:
            span_processor = AppSecSpanProcessor()
            span_processor.on_span_start(span)
            asm_request_context._call_waf(span, {})
            assert span.get_tag("_dd.appsec.waf.error") == "-3"

    list_telemetry_metrics = _get_appsec_metrics(test_agent_session, telemetry_writer)
    error_metrics = [m for m in list_telemetry_metrics if m["metric"] == "waf.error"]
    assert len(error_metrics) == 1, error_metrics
    assert len(error_metrics[0]["tags"]) == 3
    assert f"waf_version:{asm_config._ddwaf_version}" in error_metrics[0]["tags"]
    assert "waf_error:-3" in error_metrics[0]["tags"]
    assert any(tag.startswith("event_rules_version:") for tag in error_metrics[0]["tags"])


def test_log_metric_error_ddwaf_update_deduplication(telemetry_writer, test_agent_session):
    with override_global_config(dict(_asm_enabled=True)):
        span_processor = AppSecSpanProcessor()
        span_processor._update_rules([], invalid_rule_update)
        # Drop the first (pre-dedup) log so the session only reflects the deduplicated second call.
        _get_appsec_logs(test_agent_session, telemetry_writer)
        test_agent_session.clear()
        span_processor = AppSecSpanProcessor()
        span_processor._update_rules([], invalid_rule_update)
        list_metrics_logs = [
            log for log in _get_appsec_logs(test_agent_session, telemetry_writer) if log["message"] == invalid_error
        ]
        assert len(list_metrics_logs) == 0


def test_log_metric_error_ddwaf_update_deduplication_timelapse(telemetry_writer, test_agent_session):
    old_value = deduplication._time_lapse
    deduplication._time_lapse = 0.1
    try:
        with override_global_config(dict(_asm_enabled=True)):
            sleep(0.2)
            span_processor = AppSecSpanProcessor()
            span_processor._update_rules([], invalid_rule_update)
            list_metrics_logs = [
                log for log in _get_appsec_logs(test_agent_session, telemetry_writer) if log["message"] == invalid_error
            ]
            assert len(list_metrics_logs) == 1
            test_agent_session.clear()
            sleep(0.2)
            span_processor = AppSecSpanProcessor()
            span_processor._update_rules([], invalid_rule_update)
            list_metrics_logs = [
                log for log in _get_appsec_logs(test_agent_session, telemetry_writer) if log["message"] == invalid_error
            ]
            assert len(list_metrics_logs) == 1
    finally:
        deduplication._time_lapse = old_value


@pytest.mark.parametrize(
    "environment,appsec_enabled,rc_enabled,expected_result,ssi_enabled,expected_origin",
    (
        ({}, False, False, 0, False, APPSEC.ENABLED_ORIGIN_DEFAULT),
        ({APPSEC_ENV: "true"}, True, False, 1, False, APPSEC.ENABLED_ORIGIN_ENV),
        ({}, True, False, 1, False, APPSEC.ENABLED_ORIGIN_DEFAULT),
        ({}, True, True, 1, False, APPSEC.ENABLED_ORIGIN_DEFAULT),
        ({}, False, True, 1, False, APPSEC.ENABLED_ORIGIN_RC),
        ({"_DD_PY_SSI_INJECT": "true"}, False, True, 1, True, APPSEC.ENABLED_ORIGIN_RC),
        ({APPSEC_ENV: "true"}, True, True, 1, False, APPSEC.ENABLED_ORIGIN_ENV),
        # 0 because RC should not change the value if env var is set
        ({APPSEC_ENV: "true"}, False, True, 0, False, APPSEC.ENABLED_ORIGIN_ENV),
        # SSI set but AppSec disabled and no RC: origin remains UNKNOWN and value 0
        ({"_DD_PY_SSI_INJECT": "1"}, False, False, 0, True, APPSEC.ENABLED_ORIGIN_DEFAULT),
        # APPSEC_ENV present with value "false" still counts as ENV origin by implementation
        ({APPSEC_ENV: "false"}, True, False, 1, False, APPSEC.ENABLED_ORIGIN_ENV),
        # APPSEC_ENV present with empty value still counts as ENV origin by implementation
        ({APPSEC_ENV: ""}, True, False, 1, False, APPSEC.ENABLED_ORIGIN_ENV),
        # APPSEC_ENV present and SSI set but AppSec disabled: not enabled => origin ENV and value 0
        ({APPSEC_ENV: "true", "_DD_PY_SSI_INJECT": "1"}, False, False, 0, True, APPSEC.ENABLED_ORIGIN_ENV),
    ),
)
def test_appsec_enabled_metric(
    environment,
    appsec_enabled,
    rc_enabled,
    expected_result,
    ssi_enabled,
    expected_origin,
    telemetry_writer,
    test_agent_session,
    tracer,
):
    """DD_APPSEC_ENABLED is reported change-driven with the current value/origin.

    ASM enablement is reported to telemetry whenever it changes — via ``_report_asm_enabled``,
    called from the appsec enable/disable paths — rather than re-reported on every telemetry
    periodic dispatch. This drives ``_report_asm_enabled`` directly to assert the value/origin
    it emits for each configuration combination.
    """
    from ddtrace.appsec._listeners import _report_asm_enabled

    # Restore defaults and enabling telemetry appsec service
    with override_global_config({"_asm_enabled": True}):
        tracer.configure(appsec_enabled=appsec_enabled)

    # Start the test
    with (
        override_env(environment),
        override_global_config(
            dict(_asm_enabled=appsec_enabled, _remote_config_enabled=rc_enabled, _lib_was_injected=ssi_enabled)
        ),
    ):
        tracer.configure(appsec_enabled=appsec_enabled, appsec_enabled_origin=APPSEC.ENABLED_ORIGIN_DEFAULT)
        if rc_enabled:
            _enable_asm()

        # Drain telemetry queued while configuring, then capture only the change-driven
        # DD_APPSEC_ENABLED report for the final state.
        telemetry_writer.periodic(force_flush=True)
        test_agent_session.clear()
        _report_asm_enabled()
        telemetry_writer.periodic(force_flush=True)

        configurations = test_agent_session.get_configurations("DD_APPSEC_ENABLED", remove_seq_id=True, effective=True)
        # The native worker stringifies configuration values.
        assert configurations == [
            {"name": "DD_APPSEC_ENABLED", "origin": expected_origin, "value": str(expected_result)}
        ]

        # Restore defaults
        tracer.configure(appsec_enabled=appsec_enabled, appsec_enabled_origin=APPSEC.ENABLED_ORIGIN_DEFAULT)
