from unittest import TestCase

from ddtrace.span import Span
from ddtrace.writer import AsyncWorker, Q

class RemoveAllProcessor():
    def __init__(self):
        self.processed_traces = 0

    def process_trace(self, trace):
        self.processed_traces += 1
        return None

class KeepAllProcessor():
    def __init__(self):
        self.processed_traces = 0

    def process_trace(self, trace):
        self.processed_traces += 1
        return trace

class AddTagProcessor():
    def __init__(self, tag_name):
        self.tag_name = tag_name
        self.processed_traces = 0

    def process_trace(self, trace):
        self.processed_traces += 1
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
        processor = KeepAllProcessor()
        processing_pipeline = [processor]
        worker = AsyncWorker(self.api, self.traces, self.services, processing_pipeline=processing_pipeline)
        worker.stop()
        worker.join()
        self.assertEqual(len(self.api.traces), N_TRACES)
        self.assertEqual(processor.processed_traces, N_TRACES)

    def test_processing_pipeline_remove_all(self):
        processor = RemoveAllProcessor()
        processing_pipeline = [processor]
        worker = AsyncWorker(self.api, self.traces, self.services, processing_pipeline=processing_pipeline)
        worker.stop()
        worker.join()
        self.assertEqual(len(self.api.traces), 0)
        self.assertEqual(processor.processed_traces, N_TRACES)

    def test_processing_pipeline_add_tag(self):
        tag_name = "Tag"
        processor = AddTagProcessor(tag_name)
        processing_pipeline = [processor]
        worker = AsyncWorker(self.api, self.traces, self.services, processing_pipeline=processing_pipeline)
        worker.stop()
        worker.join()
        self.assertEqual(len(self.api.traces), N_TRACES)
        self.assertEqual(processor.processed_traces, N_TRACES)
        for trace in self.api.traces:
            for span in trace:
                self.assertIsNotNone(span.get_tag(tag_name))

    def test_processing_pipeline_short_circuit(self):
        processor = KeepAllProcessor()
        processing_pipeline = [RemoveAllProcessor(), processor]
        worker = AsyncWorker(self.api, self.traces, self.services, processing_pipeline=processing_pipeline)
        worker.stop()
        worker.join()
        self.assertEqual(len(self.api.traces), 0)
        self.assertEqual(processor.processed_traces, 0)
