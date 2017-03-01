import time

from nose.tools import eq_
from unittest.case import SkipTest

from ddtrace.context import Context
from ddtrace.span import Span
from ddtrace.ext import errors


def test_ids():
    s = Span(tracer=None, name="span.test")
    assert s.trace_id
    assert s.span_id
    assert not s.parent_id

    s2 = Span(tracer=None, name="t", trace_id=1, span_id=2, parent_id=1)
    eq_(s2.trace_id, 1)
    eq_(s2.span_id, 2)
    eq_(s2.parent_id, 1)


def test_tags():
    s = Span(tracer=None, name="test.span")
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

def test_set_valid_metrics():
    s = Span(tracer=None, name="test.span")
    s.set_metric("a", 0)
    s.set_metric("b", -12)
    s.set_metric("c", 12.134)
    s.set_metric("d", 1231543543265475686787869123)
    s.set_metric("e", "12.34")
    d = s.to_dict()
    expected = {
        "a": 0,
        "b": -12,
        "c": 12.134,
        "d": 1231543543265475686787869123,
        "e": 12.34,
    }
    eq_(d["metrics"], expected)

def test_set_invalid_metric():
    s = Span(tracer=None, name="test.span")

    invalid_metrics = [
        None,
        {},
        [],
        s,
        "quarante-douze",
        float("nan"),
        float("inf"),
        1j
    ]

    for i, m in enumerate(invalid_metrics):
        k = str(i)
        s.set_metric(k, m)
        eq_(s.get_metric(k), None)

def test_set_numpy_metric():
    try:
        import numpy as np
    except ImportError:
        raise SkipTest("numpy not installed")
    s = Span(tracer=None, name="test.span")
    s.set_metric("a", np.int64(1))
    eq_(s.get_metric("a"), 1)
    eq_(type(s.get_metric("a")), float)

def test_tags_not_string():
    # ensure we can cast as strings
    class Foo(object):
        def __repr__(self):
            1/0

    s = Span(tracer=None, name="test.span")
    s.set_tag("a", Foo())

def test_finish():
    # ensure finish will record a span
    dt = DummyTracer()
    ctx = Context()
    s = Span(dt, "test.span", context=ctx)
    ctx.add_span(s)
    assert s.duration is None

    sleep = 0.05
    with s as s1:
        assert s is s1
        time.sleep(sleep)
    assert s.duration >= sleep, "%s < %s" % (s.duration, sleep)
    eq_(1, dt.spans_recorded)


def test_finish_no_tracer():
    # ensure finish works with no tracer without raising exceptions
    s = Span(tracer=None, name="test.span")
    s.finish()

def test_finish_called_multiple_times():
    # we should only record a span the first time finish is called on it
    dt = DummyTracer()
    ctx = Context()
    s = Span(dt, 'bar', context=ctx)
    ctx.add_span(s)
    s.finish()
    s.finish()
    assert dt.spans_recorded == 1


def test_finish_set_span_duration():
    # If set the duration on a span, the span should be recorded with this
    # duration
    s = Span(tracer=None, name='test.span')
    s.duration = 1337.0
    s.finish()
    assert s.duration == 1337.0

def test_traceback_with_error():
    s = Span(None, "test.span")
    try:
        1/0
    except ZeroDivisionError:
        s.set_traceback()
    else:
        assert 0, "should have failed"

    assert s.error
    assert 'by zero' in s.get_tag(errors.ERROR_MSG)
    assert "ZeroDivisionError" in s.get_tag(errors.ERROR_TYPE)
    assert s.get_tag(errors.ERROR_STACK)

def test_traceback_without_error():
    s = Span(None, "test.span")
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
    s = Span(tracer=None, name="test.span", service="s",  resource="r")
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
    eq_(d["error"], 0)
    eq_(type(d["error"]), int)

def test_span_boolean_err():
    s = Span(tracer=None, name="foo.bar", service="s",  resource="r")
    s.error = True
    s.finish()

    d = s.to_dict()
    assert d
    eq_(d["error"], 1)
    eq_(type(d["error"]), int)



class DummyTracer(object):
    def __init__(self):
        self.last_span = None
        self.spans_recorded = 0

    def record(self, span):
        self.last_span = span
        self.spans_recorded += 1
