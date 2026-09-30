from ddtrace.internal.telemetry.constants import TELEMETRY_EVENT_TYPE
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from tests.telemetry.test_telemetry_metrics import _decode_sketch_count


def _assert_metric(
    test_agent,
    expected_metrics,
    namespace=TELEMETRY_NAMESPACE.TRACERS,
    type_payload=TELEMETRY_EVENT_TYPE.METRICS,
):
    """Assert APPSEC metrics against the native worker payloads.

    The native generate-metrics payload carries ``namespace`` per-series (not at the event level)
    and adds ``interval``; metric ``points`` timestamps are stamped by the worker (Rust) and are
    not mockable. Distributions are emitted with request_type "sketches" as a base64 DDSketch, so
    only the recorded point count is recoverable.
    """
    assert len(expected_metrics) > 0, "expected_metrics should not be empty"
    test_agent.telemetry_writer.periodic(force_flush=True)
    event_type = "sketches" if type_payload == TELEMETRY_EVENT_TYPE.DISTRIBUTIONS else type_payload.value
    metrics_events = test_agent.get_events(event_type)
    assert len(metrics_events) > 0, "captured metrics events should not be empty"

    if type_payload == TELEMETRY_EVENT_TYPE.DISTRIBUTIONS:
        series = []
        for event in metrics_events:
            for s in event["payload"]["series"]:
                if s.get("namespace") == namespace.value:
                    series.append((s["metric"], sorted(s.get("tags", [])), _decode_sketch_count(s["sketch_b64"])))
        for expected_metric in expected_metrics:
            key = (
                expected_metric["metric"],
                sorted(expected_metric["tags"]),
                float(len(expected_metric["points"])),
            )
            assert key in series, "%r not in %r" % (key, series)
        return

    def _normalize(s: dict) -> dict:
        n = {k: v for k, v in s.items() if k not in ("namespace", "interval")}
        n["tags"] = sorted(n.get("tags", []))
        n["points"] = [[0, value] for _, value in n.get("points", [])]
        return n

    metrics = []
    for event in metrics_events:
        for metric in event["payload"]["series"]:
            if metric.get("namespace") == namespace.value:
                metrics.append(_normalize(metric))

    for expected_metric in expected_metrics:
        expected = {k: v for k, v in expected_metric.items() if k != "interval"}
        expected["tags"] = sorted(expected["tags"])
        expected["points"] = [[0, value] for _, value in expected["points"]]
        assert expected in metrics, "%r not in %r" % (expected, metrics)


def test_send_appsec_rate_metric(telemetry_writer, test_agent_session, mock_time):
    telemetry_writer.add_rate_metric(
        TELEMETRY_NAMESPACE.APPSEC,
        "test-metric",
        6,
        (("hi", "HELLO"), ("NAME", "CANDY")),
    )
    telemetry_writer.add_rate_metric(TELEMETRY_NAMESPACE.APPSEC, "test-metric", 6, tuple())
    telemetry_writer.add_rate_metric(TELEMETRY_NAMESPACE.APPSEC, "test-metric", 6, tuple())

    # Rate metrics carry ``type: "rate"``. The worker aggregates the points as a summed count
    # (it does not divide client-side); the backend computes the rate using ``interval``.
    expected_series = [
        {
            "common": True,
            "metric": "test-metric",
            "points": [[1642544540, 6.0]],
            "tags": ["hi:hello", "name:candy"],
            "type": "rate",
        },
        {
            "common": True,
            "metric": "test-metric",
            "points": [[1642544540, 12.0]],
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
