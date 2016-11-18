import mock
import json

from ddtrace.span import Span


class FakeTime(object):
    """"Allow to mock time.time for tests

    `time.time` returns a defined `current_time` instead.
    Any `time.time` call also increase the `current_time` of `delta` seconds.
    """

    def __init__(self):
        # Sane defaults
        self._current_time = 1e9
        self._delta = 0.001

    def __call__(self):
        self._current_time = self._current_time + self._delta
        return self._current_time

    def set_epoch(self, epoch):
        self._current_time = epoch

    def set_delta(self, delta):
        self._delta = delta

    def sleep(self, second):
        self._current_time += second


def patch_time():
    """Patch time.time with FakeTime"""
    return mock.patch('time.time', new_callable=FakeTime)


def _build_span(raw_span, tracer, name):
    """
    Returns a new finished span from the given span tree
    """
    span = Span(tracer, name)
    span.service = raw_span['service']
    span.resource = raw_span['resource']
    span.span_type = raw_span['type']
    span.meta = raw_span.get('meta')
    span.error = raw_span.get('error', 0)
    span.metrics = raw_span.get('metrics')
    span.start = raw_span['start']
    span.duration = raw_span['duration']
    span.trace_id = int(raw_span['trace_id'])
    span.span_id = int(raw_span['span_id'])
    span.parent_id = int(raw_span['parent_id'])
    span.finish()
    return span


def _build_span_tree(level, tracer):
    """
    Recursively build a list of spans that are part of the same trace
    """
    if not level:
        return []

    # build a list for this level that includes the root element
    spans = [_build_span(level, tracer, level['name'])]
    for sublevel in level.get('children', []):
        # for each children (if any) recursively build the sublevel
        spans.extend(_build_span_tree(sublevel, tracer))

    return spans


def load_trace_file(filename, tracer):
    """
    Utility function that you can use to create a real trace from a given JSON file. You
    can collect the trace from the Datadog JSON API.
    """
    with open(filename, 'r') as f:
        trace_tree = json.load(f)['trace']

    trace = _build_span_tree(trace_tree, tracer)
    return trace
