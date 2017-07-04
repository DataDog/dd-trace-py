from unittest import TestCase

from ddtrace.span import Span
from ddtrace.writer import AsyncWorker, Q

class RemoveAllProcessor():
    def process_trace(self, trace):
        return None

class KeepAllProcessor():
    def process_trace(self, trace):
        return trace

class AddTagProcessor():
    def __init__(self, tag_name):
        self.tag_name = tag_name
    def process_trace(self, trace):
        for span in trace:
            span.set_tag(self.tag_name, "A value")
        return trace

class DummmyAPI():
    def __init__(self):
        self.traces = []
    def send_traces(self, traces):
        for trace in traces:
            self.traces.append(trace)

N_TRACES = 11

class AsyncWorkerTests(TestCase):
    def setUp(self):
        self.api = DummmyAPI()
        self.traces = Q()
        self.services = Q()
        for i in range(N_TRACES):
            self.traces.add([Span(tracer=None, name="name", trace_id=i, span_id=j, parent_id=j-1 or None) for j in range(7)])

    def test_processing_pipeline_keep_all(self):
        processing_pipeline = [KeepAllProcessor()]
        worker = AsyncWorker(self.api, self.traces, self.services, processing_pipeline=processing_pipeline)
        worker.stop()
        worker.join()
        self.assertEqual(len(self.api.traces), N_TRACES)

    def test_processing_pipeline_remove_all(self):
        processing_pipeline = [RemoveAllProcessor()]
        worker = AsyncWorker(self.api, self.traces, self.services, processing_pipeline=processing_pipeline)
        worker.stop()
        worker.join()
        self.assertEqual(len(self.api.traces), 0)

    def test_processing_pipeline_add_tag(self):
        tag_name = "Tag"
        processing_pipeline = [AddTagProcessor(tag_name)]
        worker = AsyncWorker(self.api, self.traces, self.services, processing_pipeline=processing_pipeline)
        worker.stop()
        worker.join()
        self.assertEqual(len(self.api.traces), N_TRACES)
        for trace in self.api.traces:
            for span in trace:
                self.assertIsNotNone(span.get_tag(tag_name))
