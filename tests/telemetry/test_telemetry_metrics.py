from time import sleep

from mock.mock import ANY

from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE_TAG_APPSEC
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE_TAG_TRACER
from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_DISTRIBUTION
from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_GENERATE_METRICS
from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_LOGS
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

    filtered_events = [event for event in events if event["request_type"] != "app-dependencies-loaded"]

    payload = {
        "namespace": namespace,
        "series": expected_series,
    }
    assert filtered_events[0]["request_type"] == type_paypload

    # Python 2.7 and Python 3.5 fail with dictionaries and lists order
    expected_body = _get_request_body(payload, type_paypload, seq_id)
    expected_body_sorted = expected_body["payload"]["series"]
    for metric in expected_body_sorted:
        metric["tags"].sort()
    expected_body_sorted.sort(key=lambda x: (x["metric"], x["tags"], x.get("type")), reverse=False)

    filtered_events.sort(key=lambda x: x["seq_id"], reverse=True)
    result_event = filtered_events[0]["payload"]["series"]
    for metric in result_event:
        metric["tags"].sort()
    result_event.sort(key=lambda x: (x["metric"], x["tags"], x.get("type")), reverse=False)

    assert result_event == expected_body_sorted


def _assert_logs(
    test_agent,
    expected_payload,
    seq_id=1,
):
    test_agent.telemetry_writer.periodic()
    events = test_agent.get_events()

    expected_body = _get_request_body({"logs": expected_payload}, TELEMETRY_TYPE_LOGS, seq_id)
    expected_body["payload"]["logs"].sort(key=lambda x: x["message"], reverse=False)
    expected_body_sorted = expected_body["payload"]["logs"]

    events[0]["payload"]["logs"].sort(key=lambda x: x["message"], reverse=False)
    result_event = events[0]["payload"]["logs"]

    assert result_event == expected_body_sorted


def test_send_metric_flush_and_generate_metrics_series_is_restarted(telemetry_writer, test_agent_session, mock_time):
    """Check the queue of metrics is empty after run periodic method of PeriodicService"""
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE_TAG_TRACER, "test-metric2", 1, (("a", "b"),))
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

    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE_TAG_TRACER, "test-metric2", 1, (("a", "b"),))

    _assert_metric(test_agent_session, expected_series, seq_id=2)


def test_send_metric_datapoint_equal_type_and_tags_yields_single_series(
    telemetry_writer, test_agent_session, mock_time
):
    """Check metrics datapoints and the aggregations by datapoint ID.
    A datapoint ID is at least: a metric name, a metric value, and the time at which the value was collected.
    But in Datadog, a datapoint also includes tags, which declare all the various scopes the datapoint belongs to
    https://www.datadoghq.com/blog/the-power-of-tagged-metrics/#whats-a-metric-tag
    """
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE_TAG_TRACER, "test-metric", 2, (("a", "b"),))
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE_TAG_TRACER, "test-metric", 3, (("a", "b"),))

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
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE_TAG_TRACER, "test-metric", 4, (("a", "b"),))
    telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE_TAG_TRACER,
        "test-metric",
        5,
        (
            ("a", "b"),
            ("c", "True"),
        ),
    )
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE_TAG_TRACER, "test-metric", 6, tuple())

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
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE_TAG_TRACER, "test-metric", 1, (("a", "b"),))
    telemetry_writer.add_gauge_metric(TELEMETRY_NAMESPACE_TAG_TRACER, "test-metric", 1, (("a", "b"),))

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
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE_TAG_TRACER, "test-metric", 1, (("a", "b"),))
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE_TAG_TRACER, "test-metric", 1, (("a", "b"),))
    telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE_TAG_TRACER, "test-metric", 1, tuple())
    telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE_TAG_TRACER,
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
        TELEMETRY_NAMESPACE_TAG_APPSEC,
        "test-metric",
        6,
        (("hi", "HELLO"), ("NAME", "CANDY")),
    )
    telemetry_writer.add_rate_metric(TELEMETRY_NAMESPACE_TAG_APPSEC, "test-metric", 6, tuple())
    telemetry_writer.add_rate_metric(TELEMETRY_NAMESPACE_TAG_APPSEC, "test-metric", 6, tuple())

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

    _assert_metric(test_agent_session, expected_series, namespace=TELEMETRY_NAMESPACE_TAG_APPSEC)


def test_send_appsec_gauge_metric(telemetry_writer, test_agent_session, mock_time):
    telemetry_writer.add_gauge_metric(
        TELEMETRY_NAMESPACE_TAG_APPSEC,
        "test-metric",
        5,
        (
            ("hi", "HELLO"),
            ("NAME", "CANDY"),
        ),
    )
    telemetry_writer.add_gauge_metric(TELEMETRY_NAMESPACE_TAG_APPSEC, "test-metric", 5, (("a", "b"),))
    telemetry_writer.add_gauge_metric(TELEMETRY_NAMESPACE_TAG_APPSEC, "test-metric", 6, tuple())

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
    _assert_metric(test_agent_session, expected_series, namespace=TELEMETRY_NAMESPACE_TAG_APPSEC)


def test_send_appsec_distributions_metric(telemetry_writer, test_agent_session, mock_time):
    telemetry_writer.add_distribution_metric(TELEMETRY_NAMESPACE_TAG_APPSEC, "test-metric", 4, tuple())
    telemetry_writer.add_distribution_metric(TELEMETRY_NAMESPACE_TAG_APPSEC, "test-metric", 5, tuple())
    telemetry_writer.add_distribution_metric(TELEMETRY_NAMESPACE_TAG_APPSEC, "test-metric", 6, tuple())

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
        namespace=TELEMETRY_NAMESPACE_TAG_APPSEC,
        type_paypload=TELEMETRY_TYPE_DISTRIBUTION,
    )


def test_send_metric_flush_and_distributions_series_is_restarted(telemetry_writer, test_agent_session, mock_time):
    """Check the queue of metrics is empty after run periodic method of PeriodicService"""
    telemetry_writer.add_distribution_metric(TELEMETRY_NAMESPACE_TAG_APPSEC, "test-metric", 4, tuple())
    telemetry_writer.add_distribution_metric(TELEMETRY_NAMESPACE_TAG_APPSEC, "test-metric", 5, tuple())
    telemetry_writer.add_distribution_metric(TELEMETRY_NAMESPACE_TAG_APPSEC, "test-metric", 6, tuple())
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

    telemetry_writer.add_distribution_metric(TELEMETRY_NAMESPACE_TAG_APPSEC, "test-metric", 1, tuple())

    _assert_metric(
        test_agent_session,
        expected_series,
        namespace=TELEMETRY_NAMESPACE_TAG_APPSEC,
        type_paypload=TELEMETRY_TYPE_DISTRIBUTION,
        seq_id=2,
    )


def test_send_log_metric_simple(telemetry_writer, test_agent_session, mock_time):
    """Check the queue of metrics is empty after run periodic method of PeriodicService"""
    with override_global_config(dict(_telemetry_dependency_collection=False)):
        telemetry_writer.add_log("WARNING", "test error 1")
        expected_payload = [
            {
                "level": "WARNING",
                "message": "test error 1",
                "tracer_time": 1642544540,
            },
        ]

        _assert_logs(test_agent_session, expected_payload)


def test_send_log_metric_simple_tags(telemetry_writer, test_agent_session, mock_time):
    """Check the queue of metrics is empty after run periodic method of PeriodicService"""
    with override_global_config(dict(_telemetry_dependency_collection=False)):
        telemetry_writer.add_log("WARNING", "test error 1", tags={"a": "b", "c": "d"})
        expected_payload = [
            {
                "level": "WARNING",
                "message": "test error 1",
                "tracer_time": 1642544540,
                "tags": "a:b,c:d",
            },
        ]

        _assert_logs(test_agent_session, expected_payload)


def test_send_multiple_log_metric(telemetry_writer, test_agent_session, mock_time):
    """Check the queue of metrics is empty after run periodic method of PeriodicService"""
    with override_global_config(dict(_telemetry_dependency_collection=False)):
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

        _assert_logs(test_agent_session, expected_payload)

        telemetry_writer.add_log("WARNING", "test error 1", "Traceback:\nValueError", {"a": "b"})

        _assert_logs(test_agent_session, expected_payload, seq_id=2)


def test_send_multiple_log_metric_no_duplicates(telemetry_writer, test_agent_session, mock_time):
    with override_global_config(dict(_telemetry_dependency_collection=False)):
        for _ in range(10):
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

        _assert_logs(test_agent_session, expected_payload)


def test_send_multiple_log_metric_no_duplicates_for_each_interval(telemetry_writer, test_agent_session, mock_time):
    with override_global_config(dict(_telemetry_dependency_collection=False)):
        for _ in range(10):
            telemetry_writer.add_log("WARNING", "test error 1")

        expected_payload = [
            {
                "level": "WARNING",
                "message": "test error 1",
                "tracer_time": 1642544540,
            },
        ]

        _assert_logs(test_agent_session, expected_payload)

        for _ in range(10):
            telemetry_writer.add_log("WARNING", "test error 1")

        _assert_logs(test_agent_session, expected_payload, seq_id=2)


def test_send_multiple_log_metric_no_duplicates_for_each_interval_check_time(telemetry_writer, test_agent_session):
    with override_global_config(dict(_telemetry_dependency_collection=False)):
        for _ in range(3):
            sleep(0.1)
            telemetry_writer.add_log("WARNING", "test error 1")

        expected_payload = [
            {
                "level": "WARNING",
                "message": "test error 1",
                "tracer_time": ANY,
            },
        ]

        _assert_logs(test_agent_session, expected_payload)

        for _ in range(3):
            sleep(0.1)
            telemetry_writer.add_log("WARNING", "test error 1")

        _assert_logs(test_agent_session, expected_payload, seq_id=2)
