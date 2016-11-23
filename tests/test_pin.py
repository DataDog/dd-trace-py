
from ddtrace import Pin
from nose.tools import eq_


def test_pin():
    class A(object):
        pass

    a = A()
    pin = Pin(service="abc")
    pin.onto(a)

    got = Pin.get_from(a)
    assert pin.service == got.service
    assert pin is got

def test_cant_pin():

    class Thing(object):
        __slots__ = ['t']

    t = Thing()
    t.t = 1

    Pin(service="a").onto(t)

def test_cant_modify():
    p = Pin(service="abc")
    try:
        p.service = "other"
    except AttributeError:
        pass

def test_copy():
    p1 = Pin(service="a", app="app_type", tags={"a":"b"})
    p2 = p1.clone(service="b")
    assert p1.service == "a"
    assert p2.service == "b"
    assert p1.app == "app_type"
    assert p2.app == "app_type"
    eq_(p1.tags, p2.tags)
    assert not (p1.tags is p2.tags)
    assert p1.tracer is p2.tracer

def test_none():
    assert None is Pin.get_from(None)

def test_repr():
    p = Pin(service="abc")
    assert p.service == "abc"
    assert 'abc' in str(p)

def test_override():
    class A(object):
        pass

    Pin(service="foo", app="blah").onto(A)
    a = A()
    Pin.override(a, app="bar")
    eq_(Pin.get_from(a).app, "bar")
    eq_(Pin.get_from(a).service, "foo")

    b = A()
    eq_(Pin.get_from(b).service, "foo")
    eq_(Pin.get_from(b).app, "blah")


def test_overide_missing():
    class A():
        pass

    a = A()
    assert not Pin.get_from(a)
    Pin.override(a, service="foo")
    assert Pin.get_from(a).service == "foo"
