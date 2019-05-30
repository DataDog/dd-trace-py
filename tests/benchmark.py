from ddtrace import Tracer
import pytest

from .test_tracer import DummyWriter


@pytest.fixture
def tracer():
    tracer = Tracer()
    tracer.writer = DummyWriter()
    return tracer


def test_tracer_context(benchmark, tracer):
    def func(tracer):
        with tracer.trace('a', service='s', resource='r', span_type='t') as s:
            s.set_tag('a', 'b')
            s.set_tag('b', 1)
            with tracer.trace('another.thing'):
                pass
            with tracer.trace('another.thing'):
                pass

    benchmark(func, tracer)


def test_tracer_wrap_staticmethod(benchmark, tracer):
    class Foo(object):
        @staticmethod
        @tracer.wrap()
        def func():
            return 0

    f = Foo()
    benchmark(f.func)


def test_tracer_wrap_classmethod(benchmark, tracer):
    class Foo(object):
        @classmethod
        @tracer.wrap()
        def func(cls):
            return 0

    f = Foo()
    benchmark(f.func)


def test_tracer_wrap_instancemethod(benchmark, tracer):
    class Foo(object):
        @tracer.wrap()
        def func(self):
            return 0

    f = Foo()
    benchmark(f.func)
