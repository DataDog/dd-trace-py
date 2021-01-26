import mock

from ddtrace.span import Span
from ddtrace.internal.writer import AgentWriter, LogWriter, _human_size
from ddtrace._worker import BaseStrategy
from tests import BaseTestCase, AnyInt


class DummyOutput:
    def __init__(self):
        self.entries = []

    def write(self, message):
        self.entries.append(message)

    def flush(self):
        pass


class FailingAPI(object):
    @staticmethod
    def send_traces(traces):
        return [Exception("oops")]


class AgentWriterTests(BaseTestCase):
    N_TRACES = 11

    def test_basic_strategy(self):
        statsd = mock.Mock()
        writer = AgentWriter(
            dogstatsd=statsd, report_metrics=False, hostname="asdf", port=1234, strategy=BaseStrategy()
        )
        for i in range(10):
            writer.write(
                [Span(tracer=None, name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)]
            )

        assert 11 == len(writer._buffer)
        writer._strategy()
        assert 0 == len(writer._buffer)

        writer.stop()
        writer.join()

    def test_basic_strategy_stop_should_flush(self):
        statsd = mock.Mock()
        writer = AgentWriter(
            dogstatsd=statsd, report_metrics=False, hostname="asdf", port=1234, strategy=BaseStrategy()
        )
        for i in range(10):
            writer.write(
                [Span(tracer=None, name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)]
            )

        assert 11 == len(writer._buffer)
        writer.stop()
        writer.join()
        assert 0 == len(writer._buffer)

    def test_metrics_disabled(self):
        statsd = mock.Mock()
        writer = AgentWriter(dogstatsd=statsd, report_metrics=False, hostname="asdf", port=1234)
        for i in range(10):
            writer.write(
                [Span(tracer=None, name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)]
            )
        writer.stop()
        writer.join()

        statsd.increment.assert_not_called()
        statsd.distribution.assert_not_called()

    def test_metrics_bad_endpoint(self):
        statsd = mock.Mock()
        writer = AgentWriter(dogstatsd=statsd, report_metrics=True, hostname="asdf", port=1234)
        for i in range(10):
            writer.write(
                [Span(tracer=None, name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)]
            )
        writer.stop()
        writer.join()

        statsd.increment.assert_has_calls(
            [
                mock.call("datadog.tracer.http.requests"),
            ]
        )
        statsd.distribution.assert_has_calls(
            [
                mock.call("datadog.tracer.buffer.accepted.traces", 10, tags=[]),
                mock.call("datadog.tracer.buffer.accepted.spans", 50, tags=[]),
                mock.call("datadog.tracer.http.requests", 1, tags=[]),
                mock.call("datadog.tracer.http.errors", 1, tags=["type:err"]),
                mock.call("datadog.tracer.http.dropped.bytes", AnyInt(), tags=[]),
            ],
            any_order=True,
        )

    def test_metrics_trace_too_big(self):
        statsd = mock.Mock()
        writer = AgentWriter(dogstatsd=statsd, report_metrics=True, hostname="asdf", port=1234)
        for i in range(10):
            writer.write(
                [Span(tracer=None, name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)]
            )
        writer.write(
            [Span(tracer=None, name="a" * 5000, trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(2 ** 10)]
        )
        writer.stop()
        writer.join()

        statsd.increment.assert_has_calls(
            [
                mock.call("datadog.tracer.http.requests"),
            ]
        )
        statsd.distribution.assert_has_calls(
            [
                mock.call("datadog.tracer.buffer.accepted.traces", 10, tags=[]),
                mock.call("datadog.tracer.buffer.accepted.spans", 50, tags=[]),
                mock.call("datadog.tracer.buffer.dropped.traces", 1, tags=["reason:t_too_big"]),
                mock.call("datadog.tracer.buffer.dropped.bytes", AnyInt(), tags=["reason:t_too_big"]),
                mock.call("datadog.tracer.http.requests", 1, tags=[]),
                mock.call("datadog.tracer.http.errors", 1, tags=["type:err"]),
                mock.call("datadog.tracer.http.dropped.bytes", AnyInt(), tags=[]),
            ],
            any_order=True,
        )

    def test_metrics_multi(self):
        statsd = mock.Mock()
        writer = AgentWriter(dogstatsd=statsd, report_metrics=True, hostname="asdf", port=1234)
        for i in range(10):
            writer.write(
                [Span(tracer=None, name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)]
            )
        writer.flush_queue()
        statsd.increment.assert_has_calls(
            [
                mock.call("datadog.tracer.http.requests"),
            ]
        )
        statsd.distribution.assert_has_calls(
            [
                mock.call("datadog.tracer.buffer.accepted.traces", 10, tags=[]),
                mock.call("datadog.tracer.buffer.accepted.spans", 50, tags=[]),
                mock.call("datadog.tracer.http.requests", 1, tags=[]),
                mock.call("datadog.tracer.http.errors", 1, tags=["type:err"]),
                mock.call("datadog.tracer.http.dropped.bytes", AnyInt(), tags=[]),
            ],
            any_order=True,
        )

        statsd.reset_mock()

        for i in range(10):
            writer.write(
                [Span(tracer=None, name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)]
            )
        writer.stop()
        writer.join()

        statsd.increment.assert_has_calls(
            [
                mock.call("datadog.tracer.http.requests"),
            ]
        )
        statsd.distribution.assert_has_calls(
            [
                mock.call("datadog.tracer.buffer.accepted.traces", 10, tags=[]),
                mock.call("datadog.tracer.buffer.accepted.spans", 50, tags=[]),
                mock.call("datadog.tracer.http.requests", 1, tags=[]),
                mock.call("datadog.tracer.http.errors", 1, tags=["type:err"]),
                mock.call("datadog.tracer.http.dropped.bytes", AnyInt(), tags=[]),
            ],
            any_order=True,
        )


class LogWriterTests(BaseTestCase):
    N_TRACES = 11

    def create_writer(self):
        self.output = DummyOutput()
        writer = LogWriter(out=self.output)
        for i in range(self.N_TRACES):
            writer.write(
                [Span(tracer=None, name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(7)]
            )
        return writer


def test_humansize():
    assert _human_size(0) == "0B"
    assert _human_size(999) == "999B"
    assert _human_size(1000) == "1KB"
    assert _human_size(10000) == "10KB"
    assert _human_size(100000) == "100KB"
    assert _human_size(1000000) == "1MB"
    assert _human_size(10000000) == "10MB"
    assert _human_size(1000000000) == "1GB"
