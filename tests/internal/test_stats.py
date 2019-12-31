import mock

import pytest

from ddtrace.internal.stats import Stats
from ddtrace.vendor.dogstatsd import DogStatsd


@pytest.fixture
def stats():
    return Stats()


@pytest.fixture
def dogstatsd():
    return mock.Mock(spec=DogStatsd)


def assert_values(stats_values, expected):
    assert len(stats_values) == len(expected)
    actual = set(v[0:3] + (tuple(v[3]),) for v in stats_values)
    expected = set(v[0:3] + (tuple(v[3]),) for v in expected)
    assert actual == expected


def test_stats_report_no_stats(stats, dogstatsd):
    stats.report(dogstatsd)

    dogstatsd.gauge.assert_not_called()
    dogstatsd.increment.assert_not_called()
    dogstatsd.histogram.assert_not_called()


def test_stats_report_stats(stats, dogstatsd):
    # Collect some stats
    stats.span_started()
    stats.span_finished()
    stats.error_log("ddtrace.tracer")

    # Report stats
    stats.report(dogstatsd)

    assert dogstatsd.gauge.mock_calls == [mock.call("datadog.tracer.spans.open", 0, tags=None)]
    assert dogstatsd.increment.mock_calls == [
        mock.call("datadog.tracer.log.errors", 1, tags=[("logger:ddtrace.tracer")])
    ]

    # Collect more stats
    stats.span_started()
    stats.error_log("ddtrace.tracer")
    stats.error_log("ddtrace.tracer")
    stats.error_log("ddtrace.tracer")

    # Report stats
    dogstatsd.reset_mock()
    stats.report(dogstatsd)

    assert dogstatsd.gauge.mock_calls == [mock.call("datadog.tracer.spans.open", 1, tags=None)]
    # Diff between last report and this report
    assert dogstatsd.increment.mock_calls == [
        mock.call("datadog.tracer.log.errors", 3, tags=[("logger:ddtrace.tracer")])
    ]

    # Add no more stats

    # Report stats
    dogstatsd.reset_mock()
    stats.report(dogstatsd)

    assert dogstatsd.gauge.mock_calls == [mock.call("datadog.tracer.spans.open", 1, tags=None)]
    # Diff between last report and this report
    assert dogstatsd.increment.mock_calls == [
        mock.call("datadog.tracer.log.errors", 0, tags=[("logger:ddtrace.tracer")])
    ]


def test_stats_reset_values(stats):
    # No stats started
    assert stats.reset_values() == []

    # Collect some stats
    stats.span_started()
    stats.span_finished()
    stats.error_log("ddtrace.tracer")

    # Validate stats
    assert_values(
        stats.reset_values(),
        [
            ("datadog.tracer.spans.open", "gauge", 0, None),
            ("datadog.tracer.log.errors", "increment", 1, ["logger:ddtrace.tracer"]),
        ],
    )

    # Collect more stats
    stats.span_started()
    stats.error_log("ddtrace.tracer")
    stats.error_log("ddtrace.tracer")

    # Validate stats
    assert_values(
        stats.reset_values(),
        [
            ("datadog.tracer.spans.open", "gauge", 1, None),
            ("datadog.tracer.log.errors", "increment", 2, ["logger:ddtrace.tracer"]),
        ],
    )

    # Add no new stats

    # Validate stats
    assert_values(
        stats.reset_values(),
        [
            ("datadog.tracer.spans.open", "gauge", 1, None),
            ("datadog.tracer.log.errors", "increment", 0, ["logger:ddtrace.tracer"]),
        ],
    )


def test_stats_span_started_finished(stats):
    # Open 5 spans
    for _ in range(5):
        stats.span_started()

    assert stats.reset_values() == [
        ("datadog.tracer.spans.open", "gauge", 5, None),
    ]

    # Close 2
    stats.span_finished()
    stats.span_finished()

    assert stats.reset_values() == [
        ("datadog.tracer.spans.open", "gauge", 3, None),
    ]

    # Open 5 more
    for _ in range(5):
        stats.span_started()

    assert stats.reset_values() == [
        ("datadog.tracer.spans.open", "gauge", 8, None),
    ]

    # Close all open spans
    for _ in range(8):
        stats.span_finished()

    assert stats.reset_values() == [
        ("datadog.tracer.spans.open", "gauge", 0, None),
    ]


def test_stats_error_log(stats):
    # Assert when error logs occur
    stats.error_log("ddtrace.tracer")
    for _ in range(2):
        stats.error_log("ddtrace.internal.writer")

    assert_values(
        stats.reset_values(),
        [
            ("datadog.tracer.log.errors", "increment", 1, ["logger:ddtrace.tracer"]),
            ("datadog.tracer.log.errors", "increment", 2, ["logger:ddtrace.internal.writer"]),
        ],
    )

    # Assert when no new error logs occur
    assert_values(
        stats.reset_values(),
        [
            ("datadog.tracer.log.errors", "increment", 0, ["logger:ddtrace.tracer"]),
            ("datadog.tracer.log.errors", "increment", 0, ["logger:ddtrace.internal.writer"]),
        ],
    )

    # Add more writer error logs
    stats.error_log("ddtrace.internal.writer")
    assert_values(
        stats.reset_values(),
        [
            ("datadog.tracer.log.errors", "increment", 0, ["logger:ddtrace.tracer"]),
            ("datadog.tracer.log.errors", "increment", 1, ["logger:ddtrace.internal.writer"]),
        ],
    )


def test_stats_patching(stats):
    for module in ("django", "redis", "requests"):
        stats.patch_success(module)

    for module in ("flask", "urllib3"):
        stats.patch_error(module)

    assert_values(
        stats.reset_values(),
        [
            ("datadog.tracer.patch.success", "gauge", 1, ["module:django"]),
            ("datadog.tracer.patch.success", "gauge", 1, ["module:redis"]),
            ("datadog.tracer.patch.success", "gauge", 1, ["module:requests"]),
            ("datadog.tracer.patch.error", "gauge", 1, ["module:flask"]),
            ("datadog.tracer.patch.error", "gauge", 1, ["module:urllib3"]),
        ],
    )

    # Report again and they are gone
    assert stats.reset_values() == []
