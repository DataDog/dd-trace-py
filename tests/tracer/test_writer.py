import mock
import msgpack

from ddtrace.internal.writer import AgentWriter
from ddtrace.internal.writer import LogWriter
from ddtrace.internal.writer import _human_size
from ddtrace.api import Response
from ddtrace.constants import KEEP_SPANS_RATE_KEY
from ddtrace.span import Span
from tests import AnyInt
from tests import BaseTestCase


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

    def test_drop_reason_bad_endpoint(self):
        statsd = mock.Mock()
        writer_metrics_reset = mock.Mock()
        writer = AgentWriter(dogstatsd=statsd, report_metrics=False, hostname="asdf", port=1234)
        writer._metrics_reset = writer_metrics_reset
        for i in range(10):
            writer.write(
                [Span(tracer=None, name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)]
            )
        writer.stop()
        writer.join()

        writer_metrics_reset.assert_called_once()

        assert 1 == writer._metrics["http.errors"]["count"]
        assert 10 == writer._metrics["http.dropped.traces"]["count"]

    def test_drop_reason_trace_too_big(self):
        statsd = mock.Mock()
        writer_metrics_reset = mock.Mock()
        writer = AgentWriter(dogstatsd=statsd, report_metrics=False, hostname="asdf", port=1234)
        writer._metrics_reset = writer_metrics_reset
        for i in range(10):
            writer.write(
                [Span(tracer=None, name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)]
            )
        writer.write(
            [Span(tracer=None, name="a" * 5000, trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(2 ** 10)]
        )
        writer.stop()
        writer.join()

        writer_metrics_reset.assert_called_once()

        assert 1 == writer._metrics["buffer.dropped.traces"]["count"]
        assert ["reason:t_too_big"] == writer._metrics["buffer.dropped.traces"]["tags"]

    def test_drop_reason_buffer_full(self):
        statsd = mock.Mock()
        writer_metrics_reset = mock.Mock()
        writer = AgentWriter(buffer_size=5300, dogstatsd=statsd, report_metrics=False, hostname="asdf", port=1234)
        writer._metrics_reset = writer_metrics_reset
        for i in range(10):
            writer.write(
                [Span(tracer=None, name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)]
            )
        writer.write([Span(tracer=None, name="a", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)])
        writer.stop()
        writer.join()

        writer_metrics_reset.assert_called_once()

        assert 1 == writer._metrics["buffer.dropped.traces"]["count"]
        assert ["reason:full"] == writer._metrics["buffer.dropped.traces"]["tags"]

    def test_drop_reason_encoding_error(self):
        statsd = mock.Mock()
        writer_encoder = mock.Mock()
        writer_metrics_reset = mock.Mock()
        writer_encoder.encode_trace.side_effect = Exception
        writer = AgentWriter(dogstatsd=statsd, report_metrics=False, hostname="asdf", port=1234)
        writer._encoder = writer_encoder
        writer._metrics_reset = writer_metrics_reset
        for i in range(10):
            writer.write(
                [Span(tracer=None, name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)]
            )

        writer.stop()
        writer.join()

        writer_metrics_reset.assert_called_once()

        assert 10 == writer._metrics["encoder.dropped.traces"]["count"]

    def test_keep_rate(self):
        statsd = mock.Mock()
        writer_run_periodic = mock.Mock()
        writer_put = mock.Mock()
        writer_put.return_value = Response(status=200)
        writer = AgentWriter(dogstatsd=statsd, report_metrics=False, hostname="asdf", port=1234)
        writer.run_periodic = writer_run_periodic
        writer._put = writer_put

        traces = [
            [Span(tracer=None, name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(5)]
            for i in range(4)
        ]

        traces_too_big = [
            [Span(tracer=None, name="a" * 5000, trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(2 ** 10)]
            for i in range(4)
        ]

        # 1. We write 4 traces successfully.
        for trace in traces:
            writer.write(trace)
        writer.flush_queue()

        payload = msgpack.unpackb(writer_put.call_args.args[0])
        # No previous drops.
        assert 0.0 == writer._drop_sma.get()
        # 4 traces written.
        assert 4 == len(payload)
        # 100% of traces kept (refers to the past).
        # No traces sent before now so 100% kept.
        for trace in payload:
            assert 1.0 == trace[0]["metrics"].get(KEEP_SPANS_RATE_KEY, -1)

        # 2. We fail to write 4 traces because of size limitation.
        for trace in traces_too_big:
            writer.write(trace)
        writer.flush_queue()

        # 50% of traces were dropped historically.
        # 4 successfully written before and 4 dropped now.
        assert 0.5 == writer._drop_sma.get()
        # put not called since no new traces are available.
        writer_put.assert_called_once()

        # 3. We write 2 traces successfully.
        for trace in traces[:2]:
            writer.write(trace)
        writer.flush_queue()

        payload = msgpack.unpackb(writer_put.call_args.args[0])
        # 40% of traces were dropped historically.
        assert 0.4 == writer._drop_sma.get()
        # 2 traces written.
        assert 2 == len(payload)
        # 50% of traces kept (refers to the past).
        # We had 4 successfully written and 4 dropped.
        for trace in payload:
            assert 0.5 == trace[0]["metrics"].get(KEEP_SPANS_RATE_KEY, -1)

        # 4. We write 1 trace successfully and fail to write 3.
        writer.write(traces[0])
        for trace in traces_too_big[:3]:
            writer.write(trace)
        writer.flush_queue()

        payload = msgpack.unpackb(writer_put.call_args.args[0])
        # 50% of traces were dropped historically.
        assert 0.5 == writer._drop_sma.get()
        # 1 trace written.
        assert 1 == len(payload)
        # 60% of traces kept (refers to the past).
        # We had 4 successfully written, then 4 dropped, then 2 written.
        for trace in payload:
            assert 0.6 == trace[0]["metrics"].get(KEEP_SPANS_RATE_KEY, -1)


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
