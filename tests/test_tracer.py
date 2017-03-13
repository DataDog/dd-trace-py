"""
tests for Tracer and utilities.
"""

import time

from nose.tools import assert_raises, eq_, ok_
from unittest.case import SkipTest

from ddtrace.encoding import JSONEncoder, MsgpackEncoder
from ddtrace.tracer import Tracer
from ddtrace.writer import AgentWriter
from ddtrace.context import Context


def test_tracer_vars():
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer

    # explicit vars
    s = tracer.trace("a", service="s", resource="r", span_type="t")
    eq_(s.service, "s")
    eq_(s.resource, "r")
    eq_(s.span_type, "t")
    s.finish()

    # defaults
    s = tracer.trace("a")
    eq_(s.service, None)
    eq_(s.resource, "a") # inherits
    eq_(s.span_type, None)

def test_tracer():
    # add some dummy tracing code.
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer
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

def test_tracer_wrap():
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer

    @tracer.wrap('decorated_function', service='s', resource='r',
            span_type='t')
    def f(tag_name, tag_value):
        # make sure we can still set tags
        span = tracer.current_span()
        span.set_tag(tag_name, tag_value)
    f('a', 'b')

    spans = writer.pop()
    eq_(len(spans), 1)
    s = spans[0]
    eq_(s.name, 'decorated_function')
    eq_(s.service, 's')
    eq_(s.resource, 'r')
    eq_(s.span_type, 't')
    eq_(s.to_dict()['meta']['a'], 'b')

def test_tracer_wrap_default_name():
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer

    @tracer.wrap()
    def f():
        pass
    f()

    eq_(writer.spans[0].name, 'tests.test_tracer.f')

def test_tracer_wrap_exception():
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer

    @tracer.wrap()
    def f():
        raise Exception('bim')

    assert_raises(Exception, f)

    eq_(len(writer.spans), 1)
    eq_(writer.spans[0].error, 1)

def test_tracer_wrap_multiple_calls():
    # Make sure that we create a new span each time the function is called
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer

    @tracer.wrap()
    def f():
        pass
    f()
    f()

    spans = writer.pop()
    eq_(len(spans), 2)
    assert spans[0].span_id != spans[1].span_id

def test_tracer_wrap_span_nesting():
    # Make sure that nested spans have the correct parents
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer

    @tracer.wrap('inner')
    def inner():
        pass
    @tracer.wrap('outer')
    def outer():
        with tracer.trace('mid'):
            inner()
    outer()

    spans = writer.pop()
    eq_(len(spans), 3)

    # sift through the list so we're not dependent on span ordering within the
    # writer
    for span in spans:
        if span.name == 'outer':
            outer_span = span
        elif span.name == 'mid':
            mid_span = span
        elif span.name == 'inner':
            inner_span = span
        else:
            assert False, 'unknown span found'  # should never get here

    assert outer_span
    assert mid_span
    assert inner_span

    eq_(outer_span.parent_id, None)
    eq_(mid_span.parent_id, outer_span.span_id)
    eq_(inner_span.parent_id, mid_span.span_id)

def test_tracer_wrap_class():
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer

    class Foo(object):

        @staticmethod
        @tracer.wrap()
        def s():
            return 1

        @classmethod
        @tracer.wrap()
        def c(cls):
            return 2

        @tracer.wrap()
        def i(cls):
            return 3

    f = Foo()
    eq_(f.s(), 1)
    eq_(f.c(), 2)
    eq_(f.i(), 3)

    spans = writer.pop()
    eq_(len(spans), 3)
    names = [s.name for s in spans]
    # FIXME[matt] include the class name here.
    eq_(sorted(names), sorted(["tests.test_tracer.%s" % n for n in ["s", "c", "i"]]))


def test_tracer_wrap_factory():
    # it should use a wrap_factory if defined
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer

    def wrap_executor(tracer, fn, args, kwargs, span_name, service, resource, span_type):
        with tracer.trace('wrap.overwrite') as span:
            span.set_tag('args', args)
            span.set_tag('kwargs', kwargs)
            return fn(*args, **kwargs)

    @tracer.wrap()
    def wrapped_function(param, kw_param=None):
        eq_(42, param)
        eq_(42, kw_param)

    # set the custom wrap factory after the wrapper has been called
    tracer.configure(wrap_executor=wrap_executor)

    # call the function expecting that the custom tracing wrapper is used
    wrapped_function(42, kw_param=42)
    eq_(writer.spans[0].name, 'wrap.overwrite')
    eq_(writer.spans[0].get_tag('args'), '(42,)')
    eq_(writer.spans[0].get_tag('kwargs'), '{\'kw_param\': 42}')


def test_tracer_disabled():
    # add some dummy tracing code.
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer

    tracer.enabled = True
    with tracer.trace("foo") as s:
        s.set_tag("a", "b")
    assert writer.pop()

    tracer.enabled = False
    with tracer.trace("foo") as s:
        s.set_tag("a", "b")
    assert not writer.pop()

def test_unserializable_span_with_finish():
    try:
        import numpy as np
    except ImportError:
        raise SkipTest("numpy not installed")

    # a weird case where manually calling finish with an unserializable
    # span was causing an loop of serialization.
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer

    with tracer.trace("parent") as span:
        span.metrics['as'] = np.int64(1) # circumvent the data checks
        span.finish()

def test_tracer_disabled_mem_leak():
    # ensure that if the tracer is disabled, we still remove things from the
    # span buffer upon finishing.
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer

    tracer.enabled = False
    s1 = tracer.trace("foo")
    s1.finish()
    p1 = tracer.current_span()
    s2 = tracer.trace("bar")
    assert not s2._parent, s2._parent
    s2.finish()
    assert not p1, p1

def test_tracer_global_tags():
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer

    s1 = tracer.trace('brie')
    s1.finish()
    assert not s1.meta

    tracer.set_tags({'env': 'prod'})
    s2 = tracer.trace('camembert')
    s2.finish()
    assert s2.meta == {'env': 'prod'}

    tracer.set_tags({'env': 'staging', 'other': 'tag'})
    s3 = tracer.trace('gruyere')
    s3.finish()
    assert s3.meta == {'env': 'staging', 'other': 'tag'}


def test_global_context():
    # the tracer uses a global thread-local Context
    tracer = get_dummy_tracer()
    span = tracer.trace('fake_span')
    ctx = tracer.get_call_context()
    eq_(1, len(ctx._trace))
    eq_(span, ctx._trace[0])


def test_tracer_current_span():
    # the current span is in the local Context()
    tracer = get_dummy_tracer()
    span = tracer.trace('fake_span')
    eq_(span, tracer.current_span())


def test_start_span():
    # it should create a root Span
    tracer = get_dummy_tracer()
    span = tracer.start_span('web.request')
    eq_('web.request', span.name)
    eq_(tracer, span._tracer)
    ok_(span._parent is None)
    ok_(span.parent_id is None)
    ok_(span._context is not None)
    eq_(span, span._context._current_span)


def test_start_span_optional():
    # it should create a root Span with arguments
    tracer = get_dummy_tracer()
    span = tracer.start_span('web.request', service='web', resource='/', span_type='http')
    eq_('web.request', span.name)
    eq_('web', span.service)
    eq_('/', span.resource)
    eq_('http', span.span_type)


def test_start_child_span():
    # it should create a child Span for the given parent
    tracer = get_dummy_tracer()
    parent = tracer.start_span('web.request')
    child = tracer.start_span('web.worker', child_of=parent)
    eq_('web.worker', child.name)
    eq_(tracer, child._tracer)
    eq_(parent, child._parent)
    eq_(parent.span_id, child.parent_id)
    eq_(parent.trace_id, child.trace_id)
    eq_(parent._context, child._context)
    eq_(child, child._context._current_span)


def test_start_child_span_attributes():
    # it should create a child Span with parent's attributes
    tracer = get_dummy_tracer()
    parent = tracer.start_span('web.request', service='web', resource='/', span_type='http')
    child = tracer.start_span('web.worker', child_of=parent)
    eq_('web.worker', child.name)
    eq_('web', child.service)


def test_start_child_from_context():
    # it should create a child span with a populated Context
    tracer = get_dummy_tracer()
    root = tracer.start_span('web.request')
    context = root.context
    child = tracer.start_span('web.worker', child_of=context)
    eq_('web.worker', child.name)
    eq_(tracer, child._tracer)
    eq_(root, child._parent)
    eq_(root.span_id, child.parent_id)
    eq_(root.trace_id, child.trace_id)
    eq_(root._context, child._context)
    eq_(child, child._context._current_span)


class DummyWriter(AgentWriter):
    """ DummyWriter is a small fake writer used for tests. not thread-safe. """

    def __init__(self):
        # original call
        super(DummyWriter, self).__init__()
        # dummy components
        self.spans = []
        self.traces = []
        self.services = {}
        self.json_encoder = JSONEncoder()
        self.msgpack_encoder = MsgpackEncoder()

    def write(self, spans=None, services=None):
        if spans:
            # the traces encoding expect a list of traces so we
            # put spans in a list like we do in the real execution path
            # with both encoders
            trace = [spans]
            self.json_encoder.encode_traces(trace)
            self.msgpack_encoder.encode_traces(trace)
            self.spans += spans
            self.traces += trace

        if services:
            self.json_encoder.encode_services(services)
            self.msgpack_encoder.encode_services(services)
            self.services.update(services)

    def pop(self):
        # dummy method
        s = self.spans
        self.spans = []
        return s

    def pop_traces(self):
        # dummy method
        traces = self.traces
        self.traces = []
        return traces

    def pop_services(self):
        # dummy method
        s = self.services
        self.services = {}
        return s


def get_dummy_tracer():
    tracer = Tracer()
    tracer.writer = DummyWriter()
    return tracer
