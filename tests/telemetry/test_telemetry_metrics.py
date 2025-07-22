import os
from time import sleep

from mock.mock import ANY

from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_DISTRIBUTION
from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_GENERATE_METRICS
from tests.utils import override_global_config


def _assert_metric(
    test_agent,
    expected_metrics,
    namespace=TELEMETRY_NAMESPACE.TRACERS,
    type_paypload=TELEMETRY_TYPE_GENERATE_METRICS,
):
    assert len(expected_metrics) > 0, "expected_metrics should not be empty"
    test_agent.telemetry_writer.periodic(force_flush=True)
    metrics_events = test_agent.get_events(type_paypload)
    assert len(metrics_events) > 0, "captured metrics events should not be empty"

    metrics = []
    for event in metrics_events:
        if event["payload"]["namespace"] == namespace.value:
            for metric in event["payload"]["series"]:
                metric["tags"].sort()
                metrics.append(metric)

    for expected_metric in expected_metrics:
        expected_metric["tags"].sort()
        assert expected_metric in metrics


def _assert_logs(test_agent, expected_logs):
    assert len(expected_logs) > 0, "expected_logs should not be empty"
    test_agent.telemetry_writer.periodic(force_flush=True)
    log_events = test_agent.get_events("logs")
    assert len(log_events) > 0, "captured log events should not be empty"

    captured_logs = []
    for event in log_events:
        captured_logs += event["payload"]["logs"]

    for expected_log in expected_logs:
        assert expected_log in captured_logs


def test_send_metric_flush_and_generate_metrics_series_is_restarted(telemetry_writer, test_agent_session, mock_time):
    """Check the queue of metrics is empty after run periodic method of PeriodicService"""
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.TRACERS, "test-metric2", 1, (("a", "b"),))
    expected_series = [
        {
            "common": True,
            "metric": "test-metric2",
            "points": [[1642544540, 1.0]],
            "tags": ["a:b"],
            "type": "count",
        },
    ]

    _assert_metric(test_agent_session, expected_series)

    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.TRACERS, "test-metric2", 1, (("a", "b"),))

    _assert_metric(test_agent_session, expected_series)


def test_send_metric_datapoint_equal_type_and_tags_yields_single_series(
    telemetry_writer, test_agent_session, mock_time
):
    """Check metrics datapoints and the aggregations by datapoint ID.
    A datapoint ID is at least: a metric name, a metric value, and the time at which the value was collected.
    But in Datadog, a datapoint also includes tags, which declare all the various scopes the datapoint belongs to
    https://www.datadoghq.com/blog/the-power-of-tagged-metrics/#whats-a-metric-tag
    """
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.TRACERS, "test-metric", 2, (("a", "b"),))
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.TRACERS, "test-metric", 3, (("a", "b"),))

    expected_series = [
        {
            "common": True,
            "metric": "test-metric",
            "points": [[1642544540, 5.0]],
            "tags": ["a:b"],
            "type": "count",
        },
    ]

    _assert_metric(test_agent_session, expected_series)


def test_send_metric_datapoint_equal_type_different_tags_yields_multiple_series(
    telemetry_writer, test_agent_session, mock_time
):
    """Check metrics datapoints and the aggregations by datapoint ID.
    A datapoint ID is at least: a metric name, a metric value, and the time at which the value was collected.
    But in Datadog, a datapoint also includes tags, which declare all the various scopes the datapoint belongs to
    https://www.datadoghq.com/blog/the-power-of-tagged-metrics/#whats-a-metric-tag
    """
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.TRACERS, "test-metric", 4, (("a", "b"),))
    telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE.TRACERS,
        "test-metric",
        5,
        (
            ("a", "b"),
            ("c", "True"),
        ),
    )
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.TRACERS, "test-metric", 6, tuple())

    expected_series = [
        {
            "common": True,
            "metric": "test-metric",
            "points": [[1642544540, 4.0]],
            "tags": ["a:b"],
            "type": "count",
        },
        {
            "common": True,
            "metric": "test-metric",
            "points": [[1642544540, 5.0]],
            "tags": ["a:b", "c:true"],
            "type": "count",
        },
        {
            "common": True,
            "metric": "test-metric",
            "points": [[1642544540, 6.0]],
            "tags": [],
            "type": "count",
        },
    ]

    _assert_metric(test_agent_session, expected_series)


def test_send_metric_datapoint_with_different_types(telemetry_writer, test_agent_session, mock_time):
    """Check metrics datapoints and the aggregations by datapoint ID.
    A datapoint ID is at least: a metric name, a metric value, and the time at which the value was collected.
    But in Datadog, a datapoint also includes tags, which declare all the various scopes the datapoint belongs to
    https://www.datadoghq.com/blog/the-power-of-tagged-metrics/#whats-a-metric-tag
    """
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.TRACERS, "test-metric", 1, (("a", "b"),))
    telemetry_writer.add_gauge_metric(TELEMETRY_NAMESPACE.TRACERS, "test-metric", 1, (("a", "b"),))

    expected_series = [
        {"common": True, "metric": "test-metric", "points": [[1642544540, 1.0]], "tags": ["a:b"], "type": "count"},
        {
            "common": True,
            "metric": "test-metric",
            "points": [[1642544540, 1.0]],
            "tags": ["a:b"],
            "type": "gauge",
            "interval": 10,
        },
    ]
    _assert_metric(test_agent_session, expected_series)


def test_send_tracers_count_metric(telemetry_writer, test_agent_session, mock_time):
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.TRACERS, "test-metric", 1, (("a", "b"),))
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.TRACERS, "test-metric", 1, (("a", "b"),))
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.TRACERS, "test-metric", 1, tuple())
    telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE.TRACERS,
        "test-metric",
        1,
        (
            ("hi", "HELLO"),
            ("NAME", "CANDY"),
        ),
    )

    expected_series = [
        {
            "common": True,
            "metric": "test-metric",
            "points": [[1642544540, 2.0]],
            "tags": ["a:b"],
            "type": "count",
        },
        {
            "common": True,
            "metric": "test-metric",
            "points": [[1642544540, 1.0]],
            "tags": [],
            "type": "count",
        },
        {
            "common": True,
            "metric": "test-metric",
            "points": [[1642544540, 1.0]],
            "tags": ["hi:hello", "name:candy"],
            "type": "count",
        },
    ]
    _assert_metric(test_agent_session, expected_series)


def test_send_appsec_rate_metric(telemetry_writer, test_agent_session, mock_time):
    telemetry_writer.add_rate_metric(
        TELEMETRY_NAMESPACE.APPSEC,
        "test-metric",
        6,
        (("hi", "HELLO"), ("NAME", "CANDY")),
    )
    telemetry_writer.add_rate_metric(TELEMETRY_NAMESPACE.APPSEC, "test-metric", 6, tuple())
    telemetry_writer.add_rate_metric(TELEMETRY_NAMESPACE.APPSEC, "test-metric", 6, tuple())

    expected_series = [
        {
            "common": True,
            "interval": 10,
            "metric": "test-metric",
            "points": [[1642544540, 0.6]],
            "tags": ["hi:hello", "name:candy"],
            "type": "rate",
        },
        {
            "common": True,
            "interval": 10,
            "metric": "test-metric",
            "points": [[1642544540, 1.2]],
            "tags": [],
            "type": "rate",
        },
    ]

    _assert_metric(test_agent_session, expected_series, namespace=TELEMETRY_NAMESPACE.APPSEC)


def test_send_appsec_gauge_metric(telemetry_writer, test_agent_session, mock_time):
    telemetry_writer.add_gauge_metric(
        TELEMETRY_NAMESPACE.APPSEC,
        "test-metric",
        5,
        (
            ("hi", "HELLO"),
            ("NAME", "CANDY"),
        ),
    )
    telemetry_writer.add_gauge_metric(TELEMETRY_NAMESPACE.APPSEC, "test-metric", 5, (("a", "b"),))
    telemetry_writer.add_gauge_metric(TELEMETRY_NAMESPACE.APPSEC, "test-metric", 6, tuple())

    expected_series = [
        {
            "common": True,
            "interval": 10,
            "metric": "test-metric",
            "points": [[1642544540, 5.0]],
            "tags": ["hi:hello", "name:candy"],
            "type": "gauge",
        },
        {
            "common": True,
            "interval": 10,
            "metric": "test-metric",
            "points": [[1642544540, 5.0]],
            "tags": ["a:b"],
            "type": "gauge",
        },
        {
            "common": True,
            "interval": 10,
            "metric": "test-metric",
            "points": [[1642544540, 6.0]],
            "tags": [],
            "type": "gauge",
        },
    ]
    _assert_metric(test_agent_session, expected_series, namespace=TELEMETRY_NAMESPACE.APPSEC)


def test_send_appsec_distributions_metric(telemetry_writer, test_agent_session, mock_time):
    telemetry_writer.add_distribution_metric(TELEMETRY_NAMESPACE.APPSEC, "test-metric", 4, tuple())
    telemetry_writer.add_distribution_metric(TELEMETRY_NAMESPACE.APPSEC, "test-metric", 5, tuple())
    telemetry_writer.add_distribution_metric(TELEMETRY_NAMESPACE.APPSEC, "test-metric", 6, tuple())

    expected_series = [
        {
            "metric": "test-metric",
            "points": [4.0, 5.0, 6.0],
            "tags": [],
        }
    ]
    _assert_metric(
        test_agent_session,
        expected_series,
        namespace=TELEMETRY_NAMESPACE.APPSEC,
        type_paypload=TELEMETRY_TYPE_DISTRIBUTION,
    )


def test_send_metric_flush_and_distributions_series_is_restarted(telemetry_writer, test_agent_session, mock_time):
    """Check the queue of metrics is empty after run periodic method of PeriodicService"""
    telemetry_writer.add_distribution_metric(TELEMETRY_NAMESPACE.APPSEC, "test-metric", 4, tuple())
    telemetry_writer.add_distribution_metric(TELEMETRY_NAMESPACE.APPSEC, "test-metric", 5, tuple())
    telemetry_writer.add_distribution_metric(TELEMETRY_NAMESPACE.APPSEC, "test-metric", 6, tuple())
    expected_series = [
        {
            "metric": "test-metric",
            "points": [4.0, 5.0, 6.0],
            "tags": [],
        }
    ]

    _assert_metric(
        test_agent_session,
        expected_series,
        namespace=TELEMETRY_NAMESPACE.APPSEC,
        type_paypload=TELEMETRY_TYPE_DISTRIBUTION,
    )

    expected_series = [
        {
            "metric": "test-metric",
            "points": [1.0],
            "tags": [],
        }
    ]

    telemetry_writer.add_distribution_metric(TELEMETRY_NAMESPACE.APPSEC, "test-metric", 1, tuple())

    _assert_metric(
        test_agent_session,
        expected_series,
        namespace=TELEMETRY_NAMESPACE.APPSEC,
        type_paypload=TELEMETRY_TYPE_DISTRIBUTION,
    )


def test_send_log_metric_simple(telemetry_writer, test_agent_session, mock_time):
    """Check the queue of metrics is empty after run periodic method of PeriodicService"""
    with override_global_config(dict(_telemetry_dependency_collection=False)):
        telemetry_writer.add_log(TELEMETRY_LOG_LEVEL.WARNING, "test error 1")
        expected_payload = [
            {
                "level": "WARN",
                "message": "test error 1",
                "tracer_time": 1642544540,
            },
        ]

        _assert_logs(test_agent_session, expected_payload)


def test_send_log_metric_simple_tags(telemetry_writer, test_agent_session, mock_time):
    """Check the queue of metrics is empty after run periodic method of PeriodicService"""
    with override_global_config(dict(_telemetry_dependency_collection=False)):
        telemetry_writer.add_log(TELEMETRY_LOG_LEVEL.WARNING, "test error 1", tags={"a": "b", "c": "d"})
        expected_payload = [
            {
                "level": "WARN",
                "message": "test error 1",
                "tracer_time": 1642544540,
                "tags": "a:b,c:d",
            },
        ]

        _assert_logs(test_agent_session, expected_payload)


def test_send_multiple_log_metric(telemetry_writer, test_agent_session, mock_time):
    """Check the queue of metrics is empty after run periodic method of PeriodicService"""
    with override_global_config(dict(_telemetry_dependency_collection=False)):
        telemetry_writer.add_log(TELEMETRY_LOG_LEVEL.WARNING, "test error 1", "Traceback:\nValueError", {"a": "b"})
        expected_payload = [
            {
                "level": "WARN",
                "message": "test error 1",
                "stack_trace": "Traceback:\nValueError",
                "tracer_time": 1642544540,
                "tags": "a:b",
            },
        ]

        _assert_logs(test_agent_session, expected_payload)

        telemetry_writer.add_log(TELEMETRY_LOG_LEVEL.WARNING, "test error 1", "Traceback:\nValueError", {"a": "b"})

        _assert_logs(test_agent_session, expected_payload)


def test_send_multiple_log_metric_no_duplicates(telemetry_writer, test_agent_session, mock_time):
    with override_global_config(dict(_telemetry_dependency_collection=False)):
        for _ in range(10):
            telemetry_writer.add_log(TELEMETRY_LOG_LEVEL.WARNING, "test error 1", "Traceback:\nValueError", {"a": "b"})

        expected_payload = [
            {
                "level": "WARN",
                "message": "test error 1",
                "stack_trace": "Traceback:\nValueError",
                "tracer_time": 1642544540,
                "tags": "a:b",
            },
        ]

        _assert_logs(test_agent_session, expected_payload)


def test_send_multiple_log_metric_no_duplicates_for_each_interval(telemetry_writer, test_agent_session, mock_time):
    with override_global_config(dict(_telemetry_dependency_collection=False)):
        for _ in range(10):
            telemetry_writer.add_log(TELEMETRY_LOG_LEVEL.WARNING, "test error 1")

        expected_payload = [
            {
                "level": "WARN",
                "message": "test error 1",
                "tracer_time": 1642544540,
            },
        ]

        _assert_logs(test_agent_session, expected_payload)

        for _ in range(10):
            telemetry_writer.add_log(TELEMETRY_LOG_LEVEL.WARNING, "test error 1")

        _assert_logs(test_agent_session, expected_payload)


def test_send_multiple_log_metric_no_duplicates_for_each_interval_check_time(telemetry_writer, test_agent_session):
    with override_global_config(dict(_telemetry_dependency_collection=False)):
        for _ in range(3):
            sleep(0.1)
            telemetry_writer.add_log(TELEMETRY_LOG_LEVEL.WARNING, "test error 1")

        expected_payload = [
            {
                "level": "WARN",
                "message": "test error 1",
                "tracer_time": ANY,
            },
        ]

        _assert_logs(test_agent_session, expected_payload)

        for _ in range(3):
            sleep(0.1)
            telemetry_writer.add_log(TELEMETRY_LOG_LEVEL.WARNING, "test error 1")

        _assert_logs(test_agent_session, expected_payload)


def test_http_propagation_extraction_telemetry(test_agent_session, ddtrace_run_python_code_in_subprocess):
    """Test that HTTP propagation extraction records telemetry metrics correctly"""
    code = """
from ddtrace.propagation.http import HTTPPropagator

# Create headers with different propagation styles
headers = {
    "x-datadog-trace-id": "123456789",
    "x-datadog-parent-id": "987654321",
    "x-datadog-sampling-priority": "1",
    "baggage": "key1=value1,key2=value2,key3=value3",
}

# Extract context from headers using default propagation styles
context = HTTPPropagator.extract(headers)
"""

    env = os.environ.copy()
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"

    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr

    # Get extraction telemetry metrics
    extraction_metrics = test_agent_session.get_metrics("context_header_style.extracted")
    assert extraction_metrics is not None
    assert len(extraction_metrics) == 2

    # Check datadog extraction metric
    datadog_metrics = [m for m in extraction_metrics if "header_style:datadog" in m["tags"]]
    assert len(datadog_metrics) == 1
    assert datadog_metrics[0]["type"] == "count"
    assert datadog_metrics[0]["points"][0][1] == 1.0
    assert "header_style:datadog" in datadog_metrics[0]["tags"]

    # Check baggage extraction metric
    baggage_metrics = [m for m in extraction_metrics if "header_style:baggage" in m["tags"]]
    assert len(baggage_metrics) == 1
    assert baggage_metrics[0]["type"] == "count"
    assert baggage_metrics[0]["points"][0][1] == 1.0
    assert "header_style:baggage" in baggage_metrics[0]["tags"]


def test_http_propagation_injection_telemetry(test_agent_session, ddtrace_run_python_code_in_subprocess):
    """Test that HTTP propagation injection records telemetry metrics correctly"""
    code = """
from ddtrace._trace.context import Context
from ddtrace.propagation.http import HTTPPropagator

# Create a context with trace and span IDs
context = Context(trace_id=123456789, span_id=987654321, sampling_priority=1)

# Add baggage items to test baggage injection as well
context.set_baggage_item("key1", "value1")
context.set_baggage_item("key2", "value2")

# Inject headers using default propagation styles
headers = {}
HTTPPropagator.inject(context, headers)
"""

    env = os.environ.copy()
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"

    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr

    # Get injection telemetry metrics
    injection_metrics = test_agent_session.get_metrics("context_header_style.injected")
    assert injection_metrics is not None
    assert len(injection_metrics) == 3  # datadog, tracecontext, and baggage (default injection styles)

    # Check datadog injection metric
    datadog_metrics = [m for m in injection_metrics if "header_style:datadog" in m["tags"]]
    assert len(datadog_metrics) == 1
    assert datadog_metrics[0]["type"] == "count"
    assert datadog_metrics[0]["points"][0][1] == 1.0
    assert "header_style:datadog" in datadog_metrics[0]["tags"]

    # Check tracecontext injection metric
    tracecontext_metrics = [m for m in injection_metrics if "header_style:tracecontext" in m["tags"]]
    assert len(tracecontext_metrics) == 1
    assert tracecontext_metrics[0]["type"] == "count"
    assert tracecontext_metrics[0]["points"][0][1] == 1.0
    assert "header_style:tracecontext" in tracecontext_metrics[0]["tags"]

    # Check baggage injection metric
    baggage_metrics = [m for m in injection_metrics if "header_style:baggage" in m["tags"]]
    assert len(baggage_metrics) == 1
    assert baggage_metrics[0]["type"] == "count"
    assert baggage_metrics[0]["points"][0][1] == 1.0
    assert "header_style:baggage" in baggage_metrics[0]["tags"]


def test_baggage_max_items_telemetry(test_agent_session, ddtrace_run_python_code_in_subprocess):
    """Test that baggage max items limit violation records telemetry correctly"""
    code = """
from ddtrace._trace.context import Context
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.internal.constants import DD_TRACE_BAGGAGE_MAX_ITEMS

# Test max items exceeded
baggage_items = {}
for i in range(DD_TRACE_BAGGAGE_MAX_ITEMS + 5):  # Exceed max items
    baggage_items[f"key{i}"] = f"value{i}"

context_items = Context(trace_id=123456789, span_id=987654321, baggage=baggage_items)
headers_items = {}
HTTPPropagator.inject(context_items, headers_items)
"""

    env = os.environ.copy()
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"

    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr

    # Get baggage truncation metrics
    truncated_metrics = test_agent_session.get_metrics("context_header_style.truncated")

    # Check max items exceeded metric
    assert truncated_metrics is not None
    item_truncated_metrics = [
        m for m in truncated_metrics if "truncation_reason:baggage_item_count_exceeded" in m["tags"]
    ]
    assert len(item_truncated_metrics) == 1
    assert item_truncated_metrics[0]["type"] == "count"
    assert item_truncated_metrics[0]["points"][0][1] == 1.0
    assert "truncation_reason:baggage_item_count_exceeded" in item_truncated_metrics[0]["tags"]


def test_baggage_max_bytes_telemetry(test_agent_session, ddtrace_run_python_code_in_subprocess):
    """Test that baggage max bytes limit violation records telemetry correctly"""
    code = """
from ddtrace._trace.context import Context
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.internal.constants import DD_TRACE_BAGGAGE_MAX_BYTES

# Test max bytes exceeded
large_value = "x" * (DD_TRACE_BAGGAGE_MAX_BYTES // 2)  # Large value to exceed bytes limit
baggage_bytes = {"key1": large_value, "key2": large_value, "key3": large_value}

context_bytes = Context(trace_id=123456789, span_id=987654321, baggage=baggage_bytes)
headers_bytes = {}
HTTPPropagator.inject(context_bytes, headers_bytes)
"""

    env = os.environ.copy()
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"

    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr

    # Get baggage truncation metrics
    truncated_metrics = test_agent_session.get_metrics("context_header_style.truncated")

    # Check max bytes exceeded metric
    assert truncated_metrics is not None
    byte_truncated_metrics = [
        m for m in truncated_metrics if "truncation_reason:baggage_byte_count_exceeded" in m["tags"]
    ]
    assert len(byte_truncated_metrics) == 1
    assert byte_truncated_metrics[0]["type"] == "count"
    assert byte_truncated_metrics[0]["points"][0][1] == 1.0
    assert "truncation_reason:baggage_byte_count_exceeded" in byte_truncated_metrics[0]["tags"]


def test_baggage_malformed_telemetry(test_agent_session, ddtrace_run_python_code_in_subprocess):
    """Test that malformed baggage headers record telemetry correctly"""
    code = """
from ddtrace.propagation.http import HTTPPropagator

# Test malformed baggage extraction
malformed_headers = {"baggage": "invalid,test=value"}
HTTPPropagator.extract(malformed_headers)
"""

    env = os.environ.copy()
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"

    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr

    # Get malformed baggage metrics
    malformed_metrics = test_agent_session.get_metrics("context_header_style.malformed")

    # Check malformed baggage metric
    assert malformed_metrics is not None
    assert len(malformed_metrics) == 1
    assert malformed_metrics[0]["type"] == "count"
    assert malformed_metrics[0]["points"][0][1] == 1.0
    assert "header_style:baggage" in malformed_metrics[0]["tags"]
