from unittest import TestCase

import pytest

from ddtrace.span import Span
from ddtrace.internal.writer import AgentWriter, Q, Empty


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


class AgentWriterTests(TestCase):
    N_TRACES = 11

    def create_worker(self, filters):
        worker = AgentWriter(filters=filters)
        self.api = DummmyAPI()
        worker.api = self.api
        for i in range(self.N_TRACES):
            worker.write([
                Span(tracer=None, name='name', trace_id=i, span_id=j, parent_id=j - 1 or None)
                for j in range(7)
            ])
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
        tag_name = 'Tag'
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


def test_queue_full():
    q = Q(maxsize=3)
    q.put([1])
    q.put(2)
    q.put([3])
    q.put([4, 4])
    assert (list(q.queue) == [[1], 2, [4, 4]] or
            list(q.queue) == [[1], [4, 4], [3]] or
            list(q.queue) == [[4, 4], 2, [3]])
    assert q.dropped == 1
    assert q.enqueued == 4
    assert q.enqueued_lengths == 5
    dropped, enqueued, enqueued_lengths = q.reset_stats()
    assert dropped == 1
    assert enqueued == 4
    assert enqueued_lengths == 5


def test_queue_get():
    q = Q(maxsize=3)
    q.put(1)
    q.put(2)
    assert list(q.get()) == [1, 2]
    with pytest.raises(Empty):
        q.get(block=False)
