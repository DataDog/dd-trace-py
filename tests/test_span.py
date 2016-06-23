import time

from nose.tools import eq_

from ddtrace.span import Span
from ddtrace.ext import errors


def test_ids():
    s = Span(tracer=None, name="test_ids")
    assert s.trace_id
    assert s.span_id
    assert not s.parent_id

    s2 = Span(tracer=None, name="t", trace_id=1, span_id=2, parent_id=1)
    eq_(s2.trace_id, 1)
    eq_(s2.span_id, 2)
    eq_(s2.parent_id, 1)


def test_tags():
    s = Span(tracer=None, name="foo")
    s.set_tag("a", "a")
    s.set_tag("b", 1)
    s.set_tag("c", "1")
    d = s.to_dict()
    expected = {
        "a" : "a",
        "b" : "1",
        "c" : "1",
    }
    eq_(d["meta"], expected)

def test_tags_not_string():
    # ensure we can cast as strings
    class Foo(object):
        def __repr__(self):
            1/0

    s = Span(tracer=None, name="foo")
    s.set_tag("a", Foo())

def test_finish():
    # ensure finish will record a span.
    dt = DummyTracer()
    assert dt.last_span is None
    s = Span(dt, "foo")
    assert s.duration is None
    sleep = 0.05
    with s as s1:
        assert s is s1
        time.sleep(sleep)
    assert s.duration >= sleep, "%s < %s" % (s.duration, sleep)
    eq_(s, dt.last_span)

    # ensure finish works with no tracer
    s2 = Span(tracer=None, name="foo")
    s2.finish()

def test_traceback_with_error():
    s = Span(None, "foo")
    try:
        1/0
    except ZeroDivisionError:
        s.set_traceback()
    else:
        assert 0, "should have failed"

    assert s.error
    assert 'by zero' in s.get_tag(errors.ERROR_MSG)
    eq_("exceptions.ZeroDivisionError", s.get_tag(errors.ERROR_TYPE))
    assert s.get_tag(errors.ERROR_STACK)

def test_traceback_without_error():
    s = Span(None, "foo")
    s.set_traceback()
    assert not s.error
    assert not s.get_tag(errors.ERROR_MSG)
    assert not s.get_tag(errors.ERROR_TYPE)
    assert not s.get_tag(errors.ERROR_STACK)

def test_ctx_mgr():
    dt = DummyTracer()
    s = Span(dt, "bar")
    assert not s.duration
    assert not s.error

    e = Exception("boo")
    try:
        with s:
            time.sleep(0.01)
            raise e
    except Exception as out:
        eq_(out, e)
        assert s.duration > 0, s.duration
        assert s.error
        eq_(s.get_tag(errors.ERROR_MSG), "boo")
        assert "Exception" in s.get_tag(errors.ERROR_TYPE)
        assert s.get_tag(errors.ERROR_STACK)

    else:
        assert 0, "should have failed"

def test_span_to_dict():
    s = Span(tracer=None, name="foo.bar", service="s",  resource="r")
    s.span_type = "foo"
    s.set_tag("a", "1")
    s.set_meta("b", "2")
    s.finish()

    d = s.to_dict()
    assert d
    eq_(d["span_id"], s.span_id)
    eq_(d["trace_id"], s.trace_id)
    eq_(d["parent_id"], s.parent_id)
    eq_(d["meta"], {"a": "1", "b": "2"})
    eq_(d["type"], "foo")


class DummyTracer(object):

    def __init__(self):
        self.last_span = None

    def record(self, span):
        self.last_span = span

