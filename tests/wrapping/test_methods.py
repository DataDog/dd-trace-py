"""Methods and other bound callables: instance / class / static / property / __call__.

Each mechanism installs its wrapper onto the class attribute in place, preserving
descriptor binding (``self`` / ``cls``).
"""

import asyncio
import inspect

from tests.wrapping._harness import aiterate
from tests.wrapping._harness import run
from tests.wrapping.mechanisms import wrap_property


def test_instance_method(mech):
    class C:
        def m(self, x):
            return ("instance", x)

    mech.install_method(C, "m")
    assert C().m(5) == ("instance", 5)


def test_wrapping_method_preserves_class_hierarchy(mech):
    # Wrapping a method must not disturb the class itself: isinstance/issubclass
    # checks, the MRO, method-vs-function detection, and override dispatch all hold.
    class Base:
        def method(self, x):
            return ("base", x)

    class Child(Base):
        def method(self, x):
            return ("child", x)

    mech.install_method(Child, "method")
    obj = Child()
    assert isinstance(obj, Base)
    assert issubclass(Child, Base)
    assert Child.__mro__ == (Child, Base, object)
    assert inspect.ismethod(obj.method)
    assert obj.method(7) == ("child", 7)


def test_classmethod(mech):
    class C:
        @classmethod
        def m(cls, x):
            return (cls.__name__, x)

    mech.install_method(C, "m")
    assert C.m(5) == ("C", 5)
    assert C().m(5) == ("C", 5)


def test_staticmethod(mech):
    class C:
        @staticmethod
        def m(x):
            return ("static", x)

    mech.install_method(C, "m")
    assert C.m(5) == ("static", 5)
    assert C().m(5) == ("static", 5)


def test_property_getter(mech):
    class C:
        def __init__(self):
            self._x = 10

        def _get(self):
            return self._x * 2

        prop = property(_get)

    wrap_property(mech, C, "prop")
    assert C().prop == 20


def test_callable_instance(mech):
    class C:
        def __call__(self, x):
            return ("called", x)

    mech.install_method(C, "__call__")
    assert C()(5) == ("called", 5)


def test_instance_method_generator(mech):
    class C:
        def gen(self, n):
            yield from range(n)

    mech.install_method(C, "gen")
    assert list(C().gen(3)) == [0, 1, 2]


def test_instance_method_coroutine(mech):
    class C:
        async def coro(self, x):
            await asyncio.sleep(0)
            return x + 1

    mech.install_method(C, "coro")
    assert run(C().coro(5)) == 6


def test_classmethod_coroutine(mech):
    class C:
        @classmethod
        async def coro(cls, x):
            await asyncio.sleep(0)
            return (cls.__name__, x)

    mech.install_method(C, "coro")
    assert run(C.coro(5)) == ("C", 5)


def test_instance_method_async_generator(mech):
    class C:
        async def agen(self, n):
            for i in range(n):
                await asyncio.sleep(0)
                yield i

    mech.install_method(C, "agen")
    assert run(aiterate(C().agen(3))) == [0, 1, 2]
