import mock

from ddtrace.span import Span
from ddtrace.api import API
from ddtrace.internal.writer import AgentWriter, LogWriter
from ddtrace.payload import PayloadFull
from tests import BaseTestCase

MAX_NUM_SPANS = 7


class RemoveAllFilter:
    def __init__(self):
        self.filtered_traces = 0

    def process_trace(self, trace):
        self.filtered_traces += 1
        return None


class KeepAllFilter:
    def __init__(self):
        self.filtered_traces = 0

    def process_trace(self, trace):
        self.filtered_traces += 1
        return trace


class AddTagFilter:
    def __init__(self, tag_name):
        self.tag_name = tag_name
        self.filtered_traces = 0

    def process_trace(self, trace):
        self.filtered_traces += 1
        for span in trace:
            span.set_tag(self.tag_name, "A value")
        return trace


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

    def create_worker(
        self, filters=None, api_class=DummyAPI, num_traces=N_TRACES, num_spans=MAX_NUM_SPANS
    ):
        worker = AgentWriter(filters=filters)
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

    def test_filters_keep_all(self):
        filtr = KeepAllFilter()
        self.create_worker([filtr])
        self.assertEqual(len(self.api.traces), self.N_TRACES)
        self.assertEqual(filtr.filtered_traces, self.N_TRACES)

    def test_filters_remove_all(self):
        filtr = RemoveAllFilter()
        self.create_worker([filtr])
        self.assertEqual(len(self.api.traces), 0)
        self.assertEqual(filtr.filtered_traces, self.N_TRACES)

    def test_filters_add_tag(self):
        tag_name = "Tag"
        filtr = AddTagFilter(tag_name)
        self.create_worker([filtr])
        self.assertEqual(len(self.api.traces), self.N_TRACES)
        self.assertEqual(filtr.filtered_traces, self.N_TRACES)
        for trace in self.api.traces:
            for span in trace:
                self.assertIsNotNone(span.get_tag(tag_name))

    def test_filters_short_circuit(self):
        filtr = KeepAllFilter()
        filters = [RemoveAllFilter(), filtr]
        self.create_worker(filters)
        self.assertEqual(len(self.api.traces), 0)
        self.assertEqual(filtr.filtered_traces, 0)


class LogWriterTests(BaseTestCase):
    N_TRACES = 11

    def create_writer(self, filters=None):
        self.output = DummyOutput()
        writer = LogWriter(out=self.output, filters=filters)
        for i in range(self.N_TRACES):
            writer.write(
                [Span(tracer=None, name="name", trace_id=i, span_id=j, parent_id=j - 1 or None) for j in range(7)]
            )
        return writer

    def test_filters_keep_all(self):
        filtr = KeepAllFilter()
        self.create_writer([filtr])
        self.assertEqual(len(self.output.entries), self.N_TRACES)
        self.assertEqual(filtr.filtered_traces, self.N_TRACES)

    def test_filters_remove_all(self):
        filtr = RemoveAllFilter()
        self.create_writer([filtr])
        self.assertEqual(len(self.output.entries), 0)
        self.assertEqual(filtr.filtered_traces, self.N_TRACES)
