import time

from ddtrace.internal.telemetry.data.host import get_hostname
from ddtrace.internal.telemetry.data.metrics import Series


def test_series_to_dict():
    series = Series("test.metric")
    series.add_tag("foo", "bar")

    assert series.to_dict() == {
        "metric": "test.metric",
        "points": [],
        "tags": {"foo": "bar"},
        "type": Series.COUNT,
        "common": False,
        "interval": None,
        "host": get_hostname(),
    }


def test_series_add_tag():
    series = Series("test.metric")

    series.add_tag("foo", "bar")
    assert series.tags == {"foo": "bar"}


def test_series_add_point():
    series = Series("test.metric")

    series.add_point(111111)
    series.add_point(222222)

    curr_time_in_secs = int(time.time())

    assert len(series.points) == 2
    assert len(series.points[0]) == 2
    assert len(series.points[1]) == 2

    assert curr_time_in_secs - series.points[0][0] < 10
    assert series.points[0][1] == 111111

    assert curr_time_in_secs - series.points[1][0] < 10
    assert series.points[1][1] == 222222
