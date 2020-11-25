import time

import mock

from ddtrace.span import Span
from ddtrace.api import API, APIExtendedException
from ddtrace.constants import KEEP_SPANS_RATE_KEY
from ddtrace.internal.writer import AgentWriter, LogWriter
from ddtrace.payload import PayloadFull, PayloadFullExtended
from tests import BaseTestCase

MAX_NUM_SPANS = 7


class DummyAPI(API):
    def __init__(self):
        # Call API.__init__ to setup required properties
        super(DummyAPI, self).__init__(hostname="localhost", port=8126)

        self.traces = []

    def send_traces(self, traces):
        responses = []
        for trace in traces:
            self.traces.append(trace)
            if len(trace) > MAX_NUM_SPANS:
                response = PayloadFullExtended()

                response.spans = len(trace)
                response.traces = 1
            else:
                response = mock.Mock()
                response.status = 200
            responses.append(response)
        return responses


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
        response = APIExtendedException(exc=Exception("oops"))
        response.spans = sum(map(len, traces))
        response.traces = len(traces)
        return [response]


class AgentWriterTests(BaseTestCase):
    N_TRACES = 11

    def create_worker(self, api_class=DummyAPI, enable_stats=False, num_traces=N_TRACES, num_spans=MAX_NUM_SPANS):
        with self.override_global_config(dict(health_metrics_enabled=enable_stats)):
            self.dogstatsd = mock.Mock()
            worker = AgentWriter(dogstatsd=self.dogstatsd)
            worker._STATS_EVERY_INTERVAL = 1
            self.api = api_class()
            worker.api = self.api
            AgentWriterTests.write_traces(worker, num_traces=num_traces, num_spans=num_spans)
            worker.stop()
            worker.join()
            return worker

    def create_lazy_worker(self, api_class=DummyAPI, enable_stats=False):
        with self.override_global_config(dict(health_metrics_enabled=enable_stats)):
            self.dogstatsd = mock.Mock()
            worker = AgentWriter(dogstatsd=self.dogstatsd)
            worker._STATS_EVERY_INTERVAL = 1
            self.api = api_class()
            worker.api = self.api
            return worker

    @staticmethod
    def write_traces(worker, num_traces=N_TRACES, num_spans=MAX_NUM_SPANS):
        for i in range(num_traces):
            worker.write(
                [
                    Span(tracer=None, name="name", trace_id=i, span_id=j, parent_id=j - 1 or None)
                    for j in range(num_spans)
                ]
            )

    def test_send_stats(self):
        dogstatsd = mock.Mock()
        worker = AgentWriter(dogstatsd=dogstatsd)
        assert worker._send_stats is False
        with self.override_global_config(dict(health_metrics_enabled=True)):
            assert worker._send_stats is True

        worker = AgentWriter(dogstatsd=None)
        assert worker._send_stats is False
        with self.override_global_config(dict(health_metrics_enabled=True)):
            assert worker._send_stats is False

    def test_no_dogstats(self):
        worker = self.create_worker()
        assert worker._send_stats is False
        assert [] == self.dogstatsd.gauge.mock_calls

    def test_dogstatsd(self):
        self.create_worker(enable_stats=True)
        assert [
            mock.call("datadog.tracer.heartbeat", 1),
            mock.call("datadog.tracer.queue.max_length", 1000),
        ] == self.dogstatsd.gauge.mock_calls

        assert [
            mock.call("datadog.tracer.flushes"),
            mock.call("datadog.tracer.flush.traces.total", 11, tags=None),
            mock.call("datadog.tracer.flush.spans.total", 77, tags=None),
            mock.call("datadog.tracer.api.requests.total", 11, tags=None),
            mock.call("datadog.tracer.api.errors.total", 0, tags=None),
            mock.call("datadog.tracer.api.traces_payloadfull.total", 0, tags=None),
            mock.call("datadog.tracer.api.responses.total", 11, tags=["status:200"]),
            mock.call("datadog.tracer.queue.dropped.traces", 0),
            mock.call("datadog.tracer.queue.enqueued.traces", 11),
            mock.call("datadog.tracer.queue.enqueued.spans", 77),
            mock.call("datadog.tracer.shutdown"),
        ] == self.dogstatsd.increment.mock_calls

        histogram_calls = [
            mock.call("datadog.tracer.flush.traces", 11, tags=None),
            mock.call("datadog.tracer.flush.spans", 77, tags=None),
            mock.call("datadog.tracer.api.requests", 11, tags=None),
            mock.call("datadog.tracer.api.errors", 0, tags=None),
            mock.call("datadog.tracer.api.traces_payloadfull", 0, tags=None),
            mock.call("datadog.tracer.api.responses", 11, tags=["status:200"]),
        ]
        if hasattr(time, "thread_time"):
            histogram_calls.append(mock.call("datadog.tracer.writer.cpu_time", mock.ANY))

        assert histogram_calls == self.dogstatsd.histogram.mock_calls

    def test_dogstatsd_traces_payloadfull(self):
        num_spans = MAX_NUM_SPANS + 1
        self.create_worker(enable_stats=True, num_traces=1, num_spans=num_spans)
        assert [
            mock.call("datadog.tracer.heartbeat", 1),
            mock.call("datadog.tracer.queue.max_length", 1000),
        ] == self.dogstatsd.gauge.mock_calls

        assert [
            mock.call("datadog.tracer.flushes"),
            mock.call("datadog.tracer.flush.traces.total", 1, tags=None),
            mock.call("datadog.tracer.flush.spans.total", num_spans, tags=None),
            mock.call("datadog.tracer.api.requests.total", 1, tags=None),
            mock.call("datadog.tracer.api.errors.total", 0, tags=None),
            mock.call("datadog.tracer.api.traces_payloadfull.total", 1, tags=None),
            mock.call("datadog.tracer.queue.dropped.traces", 0),
            mock.call("datadog.tracer.queue.enqueued.traces", 1),
            mock.call("datadog.tracer.queue.enqueued.spans", 8),
            mock.call("datadog.tracer.shutdown"),
        ] == self.dogstatsd.increment.mock_calls

        histogram_calls = [
            mock.call("datadog.tracer.flush.traces", 1, tags=None),
            mock.call("datadog.tracer.flush.spans", num_spans, tags=None),
            mock.call("datadog.tracer.api.requests", 1, tags=None),
            mock.call("datadog.tracer.api.errors", 0, tags=None),
            mock.call("datadog.tracer.api.traces_payloadfull", 1, tags=None),
        ]
        if hasattr(time, "thread_time"):
            histogram_calls.append(mock.call("datadog.tracer.writer.cpu_time", mock.ANY))

        assert histogram_calls == self.dogstatsd.histogram.mock_calls

    def test_dogstatsd_failing_api(self):
        self.create_worker(api_class=FailingAPI, enable_stats=True)
        assert [
            mock.call("datadog.tracer.heartbeat", 1),
            mock.call("datadog.tracer.queue.max_length", 1000),
        ] == self.dogstatsd.gauge.mock_calls

        assert [
            mock.call("datadog.tracer.flushes"),
            mock.call("datadog.tracer.flush.traces.total", 11, tags=None),
            mock.call("datadog.tracer.flush.spans.total", 77, tags=None),
            mock.call("datadog.tracer.api.requests.total", 1, tags=None),
            mock.call("datadog.tracer.api.errors.total", 1, tags=None),
            mock.call("datadog.tracer.api.traces_payloadfull.total", 0, tags=None),
            mock.call("datadog.tracer.queue.dropped.traces", 0),
            mock.call("datadog.tracer.queue.enqueued.traces", 11),
            mock.call("datadog.tracer.queue.enqueued.spans", 77),
            mock.call("datadog.tracer.shutdown"),
        ] == self.dogstatsd.increment.mock_calls

        histogram_calls = [
            mock.call("datadog.tracer.flush.traces", 11, tags=None),
            mock.call("datadog.tracer.flush.spans", 77, tags=None),
            mock.call("datadog.tracer.api.requests", 1, tags=None),
            mock.call("datadog.tracer.api.errors", 1, tags=None),
            mock.call("datadog.tracer.api.traces_payloadfull", 0, tags=None),
        ]
        if hasattr(time, "thread_time"):
            histogram_calls.append(mock.call("datadog.tracer.writer.cpu_time", mock.ANY))

        assert histogram_calls == self.dogstatsd.histogram.mock_calls

    def test_drop_reason_payloadfull(self):
        worker = self.create_lazy_worker()
        worker.start = mock.MagicMock(name="start")

        AgentWriterTests.write_traces(worker, num_traces=3, num_spans=MAX_NUM_SPANS + 1)

        assert 3 == worker.flush_queue()

    def test_drop_reason_failing_api(self):
        worker = self.create_lazy_worker(api_class=FailingAPI)
        worker.start = mock.MagicMock(name="start")

        AgentWriterTests.write_traces(worker, num_traces=3)

        assert 3 == worker.flush_queue()

    def test_keep_rate(self):
        worker = self.create_lazy_worker()
        worker.start = mock.MagicMock(name="start")

        AgentWriterTests.write_traces(worker, num_traces=4)
        worker.run_periodic()

        assert 0.0 == worker._drop_sma.get()
        for trace in self.api.traces:
            assert 1.0 == trace[0].metrics.get(KEEP_SPANS_RATE_KEY, -1)

        AgentWriterTests.write_traces(worker, num_traces=4, num_spans=MAX_NUM_SPANS + 1)
        worker.run_periodic()

        assert 0.5 == worker._drop_sma.get()
        for trace in self.api.traces[4:]:
            assert 1.0 == trace[0].metrics.get(KEEP_SPANS_RATE_KEY, -1)

        AgentWriterTests.write_traces(worker, num_traces=2)
        worker.run_periodic()

        assert 0.4 == worker._drop_sma.get()
        for trace in self.api.traces[8:]:
            assert 0.5 == trace[0].metrics.get(KEEP_SPANS_RATE_KEY, -1)

        worker._trace_queue.pop_stats = mock.MagicMock(name="pop_stats", return_value=(2, 2, 1))

        AgentWriterTests.write_traces(worker, num_traces=2)
        worker.run_periodic()

        assert 0.5 == worker._drop_sma.get()
        for trace in self.api.traces[10:]:
            assert 0.6 == trace[0].metrics.get(KEEP_SPANS_RATE_KEY, -1)


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
