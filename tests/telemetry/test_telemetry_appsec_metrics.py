from ddtrace.internal.telemetry.constants import TELEMETRY_EVENT_TYPE
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


def _assert_metric(
    test_agent,
    expected_metrics,
    namespace=TELEMETRY_NAMESPACE.TRACERS,
    type_payload=TELEMETRY_EVENT_TYPE.METRICS,
):
    assert len(expected_metrics) > 0, "expected_metrics should not be empty"
    test_agent.telemetry_writer.periodic(force_flush=True)
    metrics_events = test_agent.get_events(type_payload.value)
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
        type_payload=TELEMETRY_EVENT_TYPE.DISTRIBUTIONS,
    )
