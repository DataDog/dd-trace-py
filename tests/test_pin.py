
from ddtrace import Pin
from nose.tools import eq_


def test_pin():
    class A(object):
        pass

    a = A()
    pin = Pin.new(service="abc")
    pin.onto(a)

    got = Pin.get_from(a)
    assert pin.service == got.service
    assert pin is got

def test_cant_pin():

    class Thing(object):
        __slots__ = ['t']

    t = Thing()
    t.t = 1

    Pin.new(service="a").onto(t)

def test_cant_modify():
    p = Pin.new(service="abc")
    try:
        p.service = "other"
    except AttributeError:
        pass

def test_copy():
    p1 = Pin.new(service="a", app="app_type", tags={"a":"b"})
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
    p = Pin.new(service="abc")
    assert p.service == "abc"
    assert 'abc' in str(p)
