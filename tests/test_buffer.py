import random
import threading

from nose.tools import eq_

from ddtrace.buffer import ThreadLocalSpanBuffer


def _get_test_span():
    return random.randint(0, 10000) # FIXME[matt] make this real

def test_thread_local_buffer():

    tb = ThreadLocalSpanBuffer()

    def _set_get():
        eq_(tb.get(), None)
        span = _get_test_span()
        tb.set(span)
        eq_(span, tb.get())

    threads = [threading.Thread(target=_set_get) for _ in range(20)]
    for t in threads:
        t.daemon = True
        t.start()

    for t in threads:
        t.join()
