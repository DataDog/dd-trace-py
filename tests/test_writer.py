from unittest import TestCase

import pytest

from ddtrace.span import Span
from ddtrace.writer import AsyncWorker, Q, Empty


class RemoveAllFilter():
    def __init__(self):
        self.filtered_traces = 0

    def process_trace(self, trace):
        self.filtered_traces += 1
        return None


class KeepAllFilter():
    def __init__(self):
        self.filtered_traces = 0

    def process_trace(self, trace):
        self.filtered_traces += 1
        return trace


class AddTagFilter():
    def __init__(self, tag_name):
        self.tag_name = tag_name
        self.filtered_traces = 0

    def process_trace(self, trace):
        self.filtered_traces += 1
        for span in trace:
            span.set_tag(self.tag_name, 'A value')
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
            self.traces.put([
                Span(tracer=None, name='name', trace_id=i, span_id=j, parent_id=j - 1 or None)
                for j in range(7)
            ])

    def test_filters_keep_all(self):
        filtr = KeepAllFilter()
        filters = [filtr]
        worker = AsyncWorker(self.api, self.traces, self.services, filters=filters)
        worker.stop()
        worker.join()
        self.assertEqual(len(self.api.traces), N_TRACES)
        self.assertEqual(filtr.filtered_traces, N_TRACES)

    def test_filters_remove_all(self):
        filtr = RemoveAllFilter()
        filters = [filtr]
        worker = AsyncWorker(self.api, self.traces, self.services, filters=filters)
        worker.stop()
        worker.join()
        self.assertEqual(len(self.api.traces), 0)
        self.assertEqual(filtr.filtered_traces, N_TRACES)

    def test_filters_add_tag(self):
        tag_name = 'Tag'
        filtr = AddTagFilter(tag_name)
        filters = [filtr]
        worker = AsyncWorker(self.api, self.traces, self.services, filters=filters)
        worker.stop()
        worker.join()
        self.assertEqual(len(self.api.traces), N_TRACES)
        self.assertEqual(filtr.filtered_traces, N_TRACES)
        for trace in self.api.traces:
            for span in trace:
                self.assertIsNotNone(span.get_tag(tag_name))

    def test_filters_short_circuit(self):
        filtr = KeepAllFilter()
        filters = [RemoveAllFilter(), filtr]
        worker = AsyncWorker(self.api, self.traces, self.services, filters=filters)
        worker.stop()
        worker.join()
        self.assertEqual(len(self.api.traces), 0)
        self.assertEqual(filtr.filtered_traces, 0)


def test_queue_full():
    q = Q(maxsize=3)
    q.put(1)
    q.put(2)
    q.put(3)
    q.put(4)
    assert (list(q.queue) == [1, 2, 4] or
            list(q.queue) == [1, 4, 3] or
            list(q.queue) == [4, 2, 3])


def test_queue_get():
    q = Q(maxsize=3)
    q.put(1)
    q.put(2)
    assert list(q.get()) == [1, 2]
    with pytest.raises(Empty):
        q.get(block=False)
