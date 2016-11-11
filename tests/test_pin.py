
from ddtrace import Pin

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

def test_none():
    assert None is Pin.get_from(None)

def test_repr():
    p = Pin(service="abc")
    assert p.service == "abc"
    assert 'abc' in str(p)
