from mock.mock import ANY
import pytest

from ddtrace.internal.constants import TELEMETRY_APPSEC
from ddtrace.internal.constants import TELEMETRY_TRACER
from ddtrace.internal.constants import TELEMETRY_TYPE_GENERATE_METRICS
from ddtrace.internal.telemetry.metrics_namespaces import TelemetryTypeError
from ddtrace.internal.utils.version import _get_version_agent_format
from tests.telemetry.test_writer import _get_request_body


def _assert_metric(test_agent_session, expected_series, namespace=TELEMETRY_TRACER, seq_id=1):
    test_agent_session.telemetry_writer.periodic()
    events = test_agent_session.get_events()
    assert len(events) == 1

    payload = {
        "namespace": namespace,
        "lib_language": "python",
        "lib_version": _get_version_agent_format(),
        "series": expected_series,
    }
    assert events[0]["request_type"] == TELEMETRY_TYPE_GENERATE_METRICS
    assert events[0] == _get_request_body(payload, TELEMETRY_TYPE_GENERATE_METRICS, seq_id)


def test_send_metric_flush_and_series_is_restarted(mock_time, test_agent_session):
    """A datapoint is at least: a metric name, a metric value, and the time at which the value was collected.
    But in Datadog, a datapoint also includes tags, which declare all the various scopes the datapoint belongs to
    https://www.datadoghq.com/blog/the-power-of-tagged-metrics/#whats-a-metric-tag
    """
    telemetry_writer = test_agent_session.telemetry_writer
    telemetry_writer.add_count_metric(TELEMETRY_TRACER, "test-metric2", 1, {"a": "b"})
    expected_series = [
        {
            "common": True,
            "host": ANY,
            "interval": 60,
            "name": "dd.app_telemetry.tracers.test-metric2",
            "points": [[1642544540, 1.0]],
            "tags": {"a": "b"},
            "type": "count",
        },
    ]

    _assert_metric(test_agent_session, expected_series)
    test_agent_session.clear()

    telemetry_writer.add_count_metric(TELEMETRY_TRACER, "test-metric2", 1, {"a": "b"})

    _assert_metric(test_agent_session, expected_series, seq_id=2)


def test_send_metric_datapoint_equal_type_and_tags_expected_1_serie(mock_time, test_agent_session):
    """A datapoint is at least: a metric name, a metric value, and the time at which the value was collected.
    But in Datadog, a datapoint also includes tags, which declare all the various scopes the datapoint belongs to
    https://www.datadoghq.com/blog/the-power-of-tagged-metrics/#whats-a-metric-tag
    """
    # test_agent_session.telemetry_writer._flush_namespace_metrics()
    telemetry_writer = test_agent_session.telemetry_writer
    telemetry_writer.add_count_metric(TELEMETRY_TRACER, "test-metric", 2, {"a": "b"})
    telemetry_writer.add_count_metric(TELEMETRY_TRACER, "test-metric", 3, {"a": "b"})

    expected_series = [
        {
            "common": True,
            "host": ANY,
            "interval": 60,
            "name": "dd.app_telemetry.tracers.test-metric",
            "points": [[1642544540, 2.0], [1642544540, 3.0]],
            "tags": {"a": "b"},
            "type": "count",
        },
    ]

    _assert_metric(test_agent_session, expected_series)


def test_send_metric_datapoint_equal_type_different_tags_expected_3_serie(mock_time, test_agent_session):
    """A datapoint is at least: a metric name, a metric value, and the time at which the value was collected.
    But in Datadog, a datapoint also includes tags, which declare all the various scopes the datapoint belongs to
    https://www.datadoghq.com/blog/the-power-of-tagged-metrics/#whats-a-metric-tag
    """
    telemetry_writer = test_agent_session.telemetry_writer
    telemetry_writer.add_count_metric(TELEMETRY_TRACER, "test-metric", 4, {"a": "b"})
    telemetry_writer.add_count_metric(TELEMETRY_TRACER, "test-metric", 5, {"a": "b", "c": "d"})
    telemetry_writer.add_count_metric(TELEMETRY_TRACER, "test-metric", 6, {})

    expected_series = [
        {
            "common": True,
            "host": ANY,
            "interval": 60,
            "name": "dd.app_telemetry.tracers.test-metric",
            "points": [[1642544540, 4.0]],
            "tags": {"a": "b"},
            "type": "count",
        },
        {
            "common": True,
            "host": ANY,
            "interval": 60,
            "name": "dd.app_telemetry.tracers.test-metric",
            "points": [[1642544540, 5.0]],
            "tags": {"a": "b", "c": "d"},
            "type": "count",
        },
        {
            "common": True,
            "host": ANY,
            "interval": 60,
            "name": "dd.app_telemetry.tracers.test-metric",
            "points": [[1642544540, 6.0]],
            "tags": {},
            "type": "count",
        },
    ]

    _assert_metric(test_agent_session, expected_series)


def test_send_metric_datapoint_equal_tags_different_type_expected_1_serie(mock_time, test_agent_session):
    """A datapoint is at least: a metric name, a metric value, and the time at which the value was collected.
    But in Datadog, a datapoint also includes tags, which declare all the various scopes the datapoint belongs to
    https://www.datadoghq.com/blog/the-power-of-tagged-metrics/#whats-a-metric-tag
    """
    telemetry_writer = test_agent_session.telemetry_writer
    telemetry_writer.add_count_metric(TELEMETRY_TRACER, "test-metric", 1, {"a": "b"})
    with pytest.raises(TelemetryTypeError) as e:
        telemetry_writer.add_gauge_metric(TELEMETRY_TRACER, "test-metric", 1, {"a": "b"})

    assert e.value.args[0] == (
        'Error: metric with name "dd.app_telemetry.tracers.test-metric" and type "count" '
        'exists. You can\'t create a new metric with this name an type "gauge"'
    )


def test_send_tracers_count_metric(mock_time, test_agent_session):
    telemetry_writer = test_agent_session.telemetry_writer
    telemetry_writer.add_count_metric(TELEMETRY_TRACER, "test-metric", 1, {"a": "b"})
    telemetry_writer.add_count_metric(TELEMETRY_TRACER, "test-metric", 1, {"a": "b"})
    telemetry_writer.add_count_metric(TELEMETRY_TRACER, "test-metric", 1, {})
    telemetry_writer.add_count_metric(TELEMETRY_TRACER, "test-metric", 1, {"hi": "HELLO", "NAME": "CANDY"})

    expected_series = [
        {
            "common": True,
            "host": ANY,
            "interval": 60,
            "name": "dd.app_telemetry.tracers.test-metric",
            "points": [[1642544540, 1.0], [1642544540, 1.0]],
            "tags": {"a": "b"},
            "type": "count",
        },
        {
            "common": True,
            "host": ANY,
            "interval": 60,
            "name": "dd.app_telemetry.tracers.test-metric",
            "points": [[1642544540, 1.0]],
            "tags": {},
            "type": "count",
        },
        {
            "common": True,
            "host": ANY,
            "interval": 60,
            "name": "dd.app_telemetry.tracers.test-metric",
            "points": [[1642544540, 1.0]],
            "tags": {"NAME": "CANDY", "hi": "HELLO"},
            "type": "count",
        },
    ]
    _assert_metric(test_agent_session, expected_series)


def test_send_appsec_rate_metric(mock_time, test_agent_session):
    telemetry_writer = test_agent_session.telemetry_writer
    telemetry_writer.add_rate_metric(TELEMETRY_APPSEC, "test-metric", 1, {"hi": "HELLO", "NAME": "CANDY"})
    telemetry_writer.add_rate_metric(TELEMETRY_APPSEC, "test-metric", 1, {})
    telemetry_writer.add_rate_metric(TELEMETRY_APPSEC, "test-metric", 1, {})

    expected_series = [
        {
            "common": True,
            "host": ANY,
            "interval": 60,
            "name": "dd.app_telemetry.appsec.test-metric",
            "points": [[1642544540, 0.016666666666666666]],
            "tags": {"NAME": "CANDY", "hi": "HELLO"},
            "type": "rate",
        },
        {
            "common": True,
            "host": ANY,
            "interval": 60,
            "name": "dd.app_telemetry.appsec.test-metric",
            "points": [[1642544540, 0.03333333333333333]],
            "tags": {},
            "type": "rate",
        },
    ]

    _assert_metric(test_agent_session, expected_series, namespace=TELEMETRY_APPSEC)


def test_send_appsec_gauge_metric(mock_time, test_agent_session):
    telemetry_writer = test_agent_session.telemetry_writer
    telemetry_writer.add_gauge_metric(TELEMETRY_APPSEC, "test-metric", 5, {"hi": "HELLO", "NAME": "CANDY"})
    telemetry_writer.add_gauge_metric(TELEMETRY_APPSEC, "test-metric", 5, {"a": "b"})
    telemetry_writer.add_gauge_metric(TELEMETRY_APPSEC, "test-metric", 6, {})

    expected_series = [
        {
            "common": True,
            "host": ANY,
            "interval": 60,
            "name": "dd.app_telemetry.appsec.test-metric",
            "points": [[1642544540, 5.0]],
            "tags": {"NAME": "CANDY", "hi": "HELLO"},
            "type": "gauge",
        },
        {
            "common": True,
            "host": ANY,
            "interval": 60,
            "name": "dd.app_telemetry.appsec.test-metric",
            "points": [[1642544540, 5.0]],
            "tags": {"a": "b"},
            "type": "gauge",
        },
        {
            "common": True,
            "host": ANY,
            "interval": 60,
            "name": "dd.app_telemetry.appsec.test-metric",
            "points": [[1642544540, 6.0]],
            "tags": {},
            "type": "gauge",
        },
    ]
    _assert_metric(test_agent_session, expected_series, namespace=TELEMETRY_APPSEC)
