import mock

from ddtrace.span import Span
from ddtrace.api import API
from ddtrace.internal.writer import AgentWriter, LogWriter
from ddtrace.payload import PayloadFull
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
                response = PayloadFull()
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
        return [Exception("oops")]


class AgentWriterTests(BaseTestCase):
    N_TRACES = 11

    def create_worker(self, api_class=DummyAPI, enable_stats=False, num_traces=N_TRACES, num_spans=MAX_NUM_SPANS):
        with self.override_global_config(dict(health_metrics_enabled=enable_stats)):
            self.dogstatsd = mock.Mock()
            worker = AgentWriter()
            worker._STATS_EVERY_INTERVAL = 1
            self.api = api_class()
            worker.api = self.api
            for i in range(num_traces):
                worker.write(
                    [
                        Span(tracer=None, name="name", trace_id=i, span_id=j, parent_id=j - 1 or None)
                        for j in range(num_spans)
                    ]
                )
            worker.stop()
            worker.join()
            return worker


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
