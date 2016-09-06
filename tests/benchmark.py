import time
import timeit

from ddtrace import Tracer

from .test_tracer import DummyWriter


def trace(tracer):
    # explicit vars
    with tracer.trace("a", service="s", resource="r", span_type="t") as s:
        s.set_tag("a", "b")
        s.set_tag("b", 1)
        with tracer.trace("another.thing"):
            pass
        with tracer.trace("another.thing"):
            pass

def trace_error(tracer):
    # explicit vars
    with tracer.trace("a", service="s", resource="r", span_type="t"):
        1 / 0

def run():
    print("## tracer.trace() benchmark ##")
    tracer = Tracer()
    tracer.writer = DummyWriter()

    loops = 10000
    start = time.time()
    for _ in range(10000):
        trace(tracer)
    dur = time.time() - start
    print '- trace execution time -> loops:%s duration:%.5fs' % (loops, dur)


def benchmark_tracer_wrap_with_classname():
    repeat = 10
    number = 10000
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer

    # testcase
    class Foo(object):
        @staticmethod
        @tracer.wrap()
        def s():
            return 0

        @classmethod
        @tracer.wrap()
        def c(cls):
            return 0

        @tracer.wrap()
        def m(self):
            return 0

    f = Foo()

    # benchmark
    print("## @wrapt benchmark ##")
    timer = timeit.Timer(f.s)
    result = timer.repeat(repeat=repeat, number=number)
    print("- staticmethod execution time: {:8.6f}".format(min(result)))
    timer = timeit.Timer(f.c)
    result = timer.repeat(repeat=repeat, number=number)
    print("- classmethod execution time: {:8.6f}".format(min(result)))
    timer = timeit.Timer(f.m)
    result = timer.repeat(repeat=repeat, number=number)
    print("- method execution time: {:8.6f}".format(min(result)))


if __name__ == '__main__':
    benchmark_tracer_wrap_with_classname()
    run()
