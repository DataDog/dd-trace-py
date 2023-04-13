import pytest

from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE_TAG_APPSEC
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE_TAG_TRACER
from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_DISTRIBUTION
from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_GENERATE_METRICS
from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_LOGS
from ddtrace.internal.telemetry.metrics_namespaces import TelemetryTypeError
from ddtrace.internal.utils.version import _pep440_to_semver
from tests.telemetry.test_writer import _get_request_body
from tests.utils import override_global_config


def _assert_metric(
    test_agent,
    expected_series,
    namespace=TELEMETRY_NAMESPACE_TAG_TRACER,
    type_paypload=TELEMETRY_TYPE_GENERATE_METRICS,
    seq_id=1,
):
    test_agent.telemetry_writer.periodic()
    events = test_agent.get_events()

    assert len([event for event in events if event["request_type"] == type_paypload]) == seq_id

    payload = {
        "namespace": namespace,
        "lib_language": "python",
        "lib_version": _pep440_to_semver(),
        "series": expected_series,
    }
    assert events[0]["request_type"] == type_paypload

    # Python 2.7 and Python 3.5 fail with dictionaries and lists order
    expected_body = _get_request_body(payload, type_paypload, seq_id)
    expected_body_sorted = expected_body["payload"]["series"].sort(key=lambda x: x["metric"], reverse=False)
    result_event = events[0]["payload"]["series"].sort(key=lambda x: x["metric"], reverse=False)

    assert result_event == expected_body_sorted


def _assert_logs(
    test_agent,
    expected_payload,
    seq_id=1,
):
    test_agent.telemetry_writer.periodic()
    events = test_agent.get_events()

    assert len([event for event in events if event["request_type"] == TELEMETRY_TYPE_LOGS]) == seq_id

    # Python 2.7 and Python 3.5 fail with dictionaries and lists order
    expected_body = _get_request_body(expected_payload, TELEMETRY_TYPE_LOGS, seq_id)
    expected_body_sorted = expected_body["payload"].sort(key=lambda x: x["message"], reverse=False)
    result_event = events[0]["payload"].sort(key=lambda x: x["message"], reverse=False)

    assert result_event == expected_body_sorted


def test_send_metric_flush_and_generate_metrics_series_is_restarted(test_agent_metrics_session, mock_time):
    """Check the queue of metrics is empty after run periodic method of PeriodicService"""
    with override_global_config(dict(_telemetry_metrics_enabled=True)):
        telemetry_writer = test_agent_metrics_session.telemetry_writer
        telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE_TAG_TRACER, "test-metric2", 1, {"a": "b"})
        expected_series = [
            {
                "common": True,
                "metric": "test-metric2",
                "points": [[1642544540, 1.0]],
                "tags": ["a:b"],
                "type": "count",
            },
        ]

        _assert_metric(test_agent_metrics_session, expected_series)

        telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE_TAG_TRACER, "test-metric2", 1, {"a": "b"})

        _assert_metric(test_agent_metrics_session, expected_series, seq_id=2)


def test_send_metric_datapoint_equal_type_and_tags_yields_single_series(test_agent_metrics_session, mock_time):
    """Check metrics datapoints and the aggregations by datapoint ID.
    A datapoint ID is at least: a metric name, a metric value, and the time at which the value was collected.
    But in Datadog, a datapoint also includes tags, which declare all the various scopes the datapoint belongs to
    https://www.datadoghq.com/blog/the-power-of-tagged-metrics/#whats-a-metric-tag
    """
    with override_global_config(dict(_telemetry_metrics_enabled=True)):
        telemetry_writer = test_agent_metrics_session.telemetry_writer
        telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE_TAG_TRACER, "test-metric", 2, {"a": "b"})
        telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE_TAG_TRACER, "test-metric", 3, {"a": "b"})

        expected_series = [
            {
                "common": True,
                "metric": "test-metric",
                "points": [[1642544540, 5.0]],
                "tags": ["a:b"],
                "type": "count",
            },
        ]

        _assert_metric(test_agent_metrics_session, expected_series)


def test_send_metric_datapoint_equal_type_different_tags_yields_multiple_series(test_agent_metrics_session, mock_time):
    """Check metrics datapoints and the aggregations by datapoint ID.
    A datapoint ID is at least: a metric name, a metric value, and the time at which the value was collected.
    But in Datadog, a datapoint also includes tags, which declare all the various scopes the datapoint belongs to
    https://www.datadoghq.com/blog/the-power-of-tagged-metrics/#whats-a-metric-tag
    """
    with override_global_config(dict(_telemetry_metrics_enabled=True)):
        telemetry_writer = test_agent_metrics_session.telemetry_writer
        telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE_TAG_TRACER, "test-metric", 4, {"a": "b"})
        telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE_TAG_TRACER, "test-metric", 5, {"a": "b", "c": "d"})
        telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE_TAG_TRACER, "test-metric", 6, {})

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
                "tags": ["a:b", "c:d"],
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

        _assert_metric(test_agent_metrics_session, expected_series)


def test_send_metric_datapoint_equal_tags_different_type_throws_error(test_agent_metrics_session, mock_time):
    """Check metrics datapoints and the aggregations by datapoint ID.
    A datapoint ID is at least: a metric name, a metric value, and the time at which the value was collected.
    But in Datadog, a datapoint also includes tags, which declare all the various scopes the datapoint belongs to
    https://www.datadoghq.com/blog/the-power-of-tagged-metrics/#whats-a-metric-tag
    """
    with override_global_config(dict(_telemetry_metrics_enabled=True)):
        telemetry_writer = test_agent_metrics_session.telemetry_writer
        telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE_TAG_TRACER, "test-metric", 1, {"a": "b"})
        with pytest.raises(TelemetryTypeError) as e:
            telemetry_writer.add_gauge_metric(TELEMETRY_NAMESPACE_TAG_TRACER, "test-metric", 1, {"a": "b"})

            assert e.value.args[0] == (
                'Error: metric with name "test-metric" and type "count" '
                'exists. You can\'t create a new metric with this name an type "gauge"'
            )


def test_send_tracers_count_metric(test_agent_metrics_session, mock_time):
    with override_global_config(dict(_telemetry_metrics_enabled=True)):
        telemetry_writer = test_agent_metrics_session.telemetry_writer
        telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE_TAG_TRACER, "test-metric", 1, {"a": "b"})
        telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE_TAG_TRACER, "test-metric", 1, {"a": "b"})
        telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE_TAG_TRACER, "test-metric", 1, {})
        telemetry_writer.add_count_metric(
            TELEMETRY_NAMESPACE_TAG_TRACER, "test-metric", 1, {"hi": "HELLO", "NAME": "CANDY"}
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
                "tags": ["hi:HELLO", "NAME:CANDY"],
                "type": "count",
            },
        ]
        _assert_metric(test_agent_metrics_session, expected_series)


def test_send_appsec_rate_metric(test_agent_metrics_session, mock_time):
    with override_global_config(dict(_telemetry_metrics_enabled=True)):
        telemetry_writer = test_agent_metrics_session.telemetry_writer
        telemetry_writer.add_rate_metric(
            TELEMETRY_NAMESPACE_TAG_APPSEC, "test-metric", 1, {"hi": "HELLO", "NAME": "CANDY"}
        )
        telemetry_writer.add_rate_metric(TELEMETRY_NAMESPACE_TAG_APPSEC, "test-metric", 1, {})
        telemetry_writer.add_rate_metric(TELEMETRY_NAMESPACE_TAG_APPSEC, "test-metric", 1, {})

        expected_series = [
            {
                "common": True,
                "interval": 60,
                "metric": "test-metric",
                "points": [[1642544540, 0.016666666666666666]],
                "tags": ["hi:HELLO", "NAME:CANDY"],
                "type": "rate",
            },
            {
                "common": True,
                "interval": 60,
                "metric": "test-metric",
                "points": [[1642544540, 0.03333333333333333]],
                "tags": [],
                "type": "rate",
            },
        ]

        _assert_metric(test_agent_metrics_session, expected_series, namespace=TELEMETRY_NAMESPACE_TAG_APPSEC)


def test_send_appsec_gauge_metric(test_agent_metrics_session, mock_time):
    with override_global_config(dict(_telemetry_metrics_enabled=True)):
        telemetry_writer = test_agent_metrics_session.telemetry_writer
        telemetry_writer.add_gauge_metric(
            TELEMETRY_NAMESPACE_TAG_APPSEC, "test-metric", 5, {"hi": "HELLO", "NAME": "CANDY"}
        )
        telemetry_writer.add_gauge_metric(TELEMETRY_NAMESPACE_TAG_APPSEC, "test-metric", 5, {"a": "b"})
        telemetry_writer.add_gauge_metric(TELEMETRY_NAMESPACE_TAG_APPSEC, "test-metric", 6, {})

        expected_series = [
            {
                "common": True,
                "interval": 60,
                "metric": "test-metric",
                "points": [[1642544540, 5.0]],
                "tags": ["hi:HELLO", "NAME:CANDY"],
                "type": "gauge",
            },
            {
                "common": True,
                "interval": 60,
                "metric": "test-metric",
                "points": [[1642544540, 5.0]],
                "tags": ["a:b"],
                "type": "gauge",
            },
            {
                "common": True,
                "interval": 60,
                "metric": "test-metric",
                "points": [[1642544540, 6.0]],
                "tags": [],
                "type": "gauge",
            },
        ]
        _assert_metric(test_agent_metrics_session, expected_series, namespace=TELEMETRY_NAMESPACE_TAG_APPSEC)


def test_send_appsec_distributions_metric(test_agent_metrics_session, mock_time):
    with override_global_config(dict(_telemetry_metrics_enabled=True)):
        telemetry_writer = test_agent_metrics_session.telemetry_writer
        telemetry_writer.add_distribution_metric(TELEMETRY_NAMESPACE_TAG_APPSEC, "test-metric", 4, {})
        telemetry_writer.add_distribution_metric(TELEMETRY_NAMESPACE_TAG_APPSEC, "test-metric", 5, {})
        telemetry_writer.add_distribution_metric(TELEMETRY_NAMESPACE_TAG_APPSEC, "test-metric", 6, {})

        expected_series = [
            {
                "metric": "test-metric",
                "points": [4.0, 5.0, 6.0],
                "tags": [],
            }
        ]
        _assert_metric(
            test_agent_metrics_session,
            expected_series,
            namespace=TELEMETRY_NAMESPACE_TAG_APPSEC,
            type_paypload=TELEMETRY_TYPE_DISTRIBUTION,
        )


def test_send_metric_flush_and_distributions_series_is_restarted(test_agent_metrics_session, mock_time):
    """Check the queue of metrics is empty after run periodic method of PeriodicService"""
    with override_global_config(dict(_telemetry_metrics_enabled=True)):
        telemetry_writer = test_agent_metrics_session.telemetry_writer
        telemetry_writer.add_distribution_metric(TELEMETRY_NAMESPACE_TAG_APPSEC, "test-metric", 4, {})
        telemetry_writer.add_distribution_metric(TELEMETRY_NAMESPACE_TAG_APPSEC, "test-metric", 5, {})
        telemetry_writer.add_distribution_metric(TELEMETRY_NAMESPACE_TAG_APPSEC, "test-metric", 6, {})
        expected_series = [
            {
                "metric": "test-metric",
                "points": [4.0, 5.0, 6.0],
                "tags": [],
            }
        ]

        _assert_metric(
            test_agent_metrics_session,
            expected_series,
            namespace=TELEMETRY_NAMESPACE_TAG_APPSEC,
            type_paypload=TELEMETRY_TYPE_DISTRIBUTION,
        )

        expected_series = [
            {
                "metric": "test-metric",
                "points": [1.0],
                "tags": [],
            }
        ]

        telemetry_writer.add_distribution_metric(TELEMETRY_NAMESPACE_TAG_APPSEC, "test-metric", 1, {})

        _assert_metric(
            test_agent_metrics_session,
            expected_series,
            namespace=TELEMETRY_NAMESPACE_TAG_APPSEC,
            type_paypload=TELEMETRY_TYPE_DISTRIBUTION,
            seq_id=2,
        )


def test_send_log_metric_simple(test_agent_metrics_session, mock_time):
    """Check the queue of metrics is empty after run periodic method of PeriodicService"""
    with override_global_config(dict(_telemetry_metrics_enabled=True)):
        telemetry_writer = test_agent_metrics_session.telemetry_writer
        telemetry_writer.add_log("WARNING", "test error 1")
        expected_payload = [
            {
                "level": "WARNING",
                "message": "test error 1",
                "tracer_time": 1642544540,
            },
        ]

        _assert_logs(test_agent_metrics_session, expected_payload)


def test_send_log_metric_simple_tags(test_agent_metrics_session, mock_time):
    """Check the queue of metrics is empty after run periodic method of PeriodicService"""
    with override_global_config(dict(_telemetry_metrics_enabled=True)):
        telemetry_writer = test_agent_metrics_session.telemetry_writer
        telemetry_writer.add_log("WARNING", "test error 1", tags={"a": "b", "c": "d"})
        expected_payload = [
            {
                "level": "WARNING",
                "message": "test error 1",
                "tracer_time": 1642544540,
                "tags": "a:b,c:d",
            },
        ]

        _assert_logs(test_agent_metrics_session, expected_payload)


def test_send_multiple_log_metric(test_agent_metrics_session, mock_time):
    """Check the queue of metrics is empty after run periodic method of PeriodicService"""
    with override_global_config(dict(_telemetry_metrics_enabled=True)):
        telemetry_writer = test_agent_metrics_session.telemetry_writer
        telemetry_writer.add_log("WARNING", "test error 1", "Traceback:\nValueError", {"a": "b"})
        expected_payload = [
            {
                "level": "WARNING",
                "message": "test error 1",
                "stack_trace": "Traceback:\nValueError",
                "tracer_time": 1642544540,
                "tags": "a:b",
            },
        ]

        _assert_logs(test_agent_metrics_session, expected_payload)

        telemetry_writer.add_log("WARNING", "test error 1", "Traceback:\nValueError", {"a": "b"})

        _assert_logs(test_agent_metrics_session, expected_payload, seq_id=2)
