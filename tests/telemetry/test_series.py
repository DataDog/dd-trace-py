import mock

from ddtrace.internal.telemetry.data.metrics import MetricType
from ddtrace.internal.telemetry.data.metrics import Series


def test_default_series():
    """tests initializing a Series with default args"""
    series = Series("test.metric")

    assert series.metric == "test.metric"
    assert series.type == MetricType.COUNT
    assert series.common is False
    assert series.interval is None
    assert series.tags == {}
    assert series.points == []


def test_guage_series():
    """tests initializing a Series object with a gauge metric"""
    series = Series("test.guage_metric", MetricType.GAUGE, interval=20, common=False)

    assert series.metric == "test.guage_metric"
    assert series.type == MetricType.GAUGE
    assert series.common is False
    assert series.interval == 20


def test_rate_series():
    """tests initializing a Series object with a rate metric"""
    series = Series("test.common_rate_metric", MetricType.RATE, interval=30, common=True)

    assert series.metric == "test.common_rate_metric"
    assert series.type == MetricType.RATE
    assert series.common is True
    assert series.interval == 30


def test_series_set_tag():
    """tests adding tags to a metric"""
    series = Series("test.rate_metric", metric_type=MetricType.RATE)

    series.set_tag("foo", "bar")
    series.set_tag("foo", "moo")

    series.set_tag("gege", "meme")

    assert series.tags == {"foo": "moo", "gege": "meme"}


def test_series_add_point():
    """tests adding points to a metric"""
    series = Series("test.guage_metric", MetricType.GAUGE, interval=10)

    with mock.patch("time.time") as t:
        t.return_value = 6543210
        series.add_point(111111)
        series.add_point(222222)

        assert series.points == [(6543210, 111111), (6543210, 222222)]


def test_series_to_dict():
    """tests converting a series object to a dict and validates the set fields"""
    series = Series("test.metric", MetricType.GAUGE, True, interval=10)

    with mock.patch("time.time") as t:
        t.return_value = 6543210
        with mock.patch("ddtrace.internal.telemetry.data.metrics.get_hostname") as gh:
            gh.return_value = "docker-desktop"
            series.add_point(111111)
            series.add_point(222222)

            series.set_tag("foo", "bar")

            assert series.to_dict() == {
                "metric": "test.metric",
                "points": [(6543210, 111111), (6543210, 222222)],
                "tags": {"foo": "bar"},
                "type": "gauge",
                "common": True,
                "interval": 10,
                "host": "docker-desktop",
            }
