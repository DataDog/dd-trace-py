
import time
from ddtrace import Tracer
from test_tracer import DummyWriter


def trace(tracer):
    # explicit vars
    with tracer.trace("a", service="s", resource="r", span_type="t") as s:
        s.set_tag("a", "b")
        s.set_tag("b", 1)
        with tracer.trace("another.thing"): pass
        with tracer.trace("another.thing"): pass
        try:
            with tracer.trace("another.thing"):
                1/0
        except ZeroDivisionError:
            pass
            


def trace_error(tracer):
    # explicit vars
    with tracer.trace("a", service="s", resource="r", span_type="t") as s:
        1/0


def run():
    tracer = Tracer()
    tracer.writer = DummyWriter()

    loops = 10000
    start = time.time()
    for i in range(10000):
        trace(tracer)
    dur = time.time() - start
    print 'loops:%s duration:%.5fs' % (loops, dur)

if __name__ == '__main__':
    run()

