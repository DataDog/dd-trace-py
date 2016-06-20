"""
tests for Tracer and utilities.
"""

import time
from nose.tools import eq_

from .tracer import Tracer


def test_tracer():
    # add some dummy tracing code.
    writer = DummyWriter()
    tracer = Tracer(writer=writer)
    sleep = 0.05

    def _mix():
        with tracer.trace("cake.mix"):
            time.sleep(sleep)

    def _bake():
        with tracer.trace("cake.bake"):
            time.sleep(sleep)

    def _make_cake():
        with tracer.trace("cake.make") as span:
            span.service = "baker"
            span.resource = "cake"
            _mix()
            _bake()

    # let's run it and make sure all is well.
    assert not writer.spans
    _make_cake()
    spans = writer.pop()
    assert spans, "%s" % spans
    eq_(len(spans), 3)
    spans_by_name = {s.name:s for s in spans}
    eq_(len(spans_by_name), 3)

    make = spans_by_name["cake.make"]
    assert make.span_id
    assert make.parent_id is None
    assert make.trace_id

    for other in ["cake.mix", "cake.bake"]:
        s = spans_by_name[other]
        eq_(s.parent_id, make.span_id)
        eq_(s.trace_id, make.trace_id)
        eq_(s.service, make.service) # ensure it inherits the service
        eq_(s.resource, s.name)      # ensure when we don't set a resource, it's there.


    # do it again and make sure it has new trace ids
    _make_cake()
    spans = writer.pop()
    for s in spans:
        assert s.trace_id != make.trace_id


class DummyWriter(object):
    """ DummyWriter is a small fake writer used for tests. not thread-safe. """

    def __init__(self):
        self.spans = []

    def write(self, spans):
        self.spans += spans

    def pop(self):
        s = self.spans
        self.spans = []
        return s
