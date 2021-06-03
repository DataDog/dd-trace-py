"""
Variety of test cases ensuring that ddtrace does not leak memory.
"""
import gc
from threading import Thread
from typing import TYPE_CHECKING
from weakref import WeakValueDictionary

import pytest

from ddtrace import Tracer


if TYPE_CHECKING:
    from ddtrace.span import Span


@pytest.fixture
def tracer():
    # type: (...) -> Tracer
    return Tracer()


def trace(weakdict, tracer, *args, **kwargs):
    # type: (WeakValueDictionary, Tracer, ...) -> Span
    """Return a reference to a span created from ``tracer.trace(*args, **kwargs)``
    and adds it to the given weak dictionary.

    Note: ensure to delete the returned reference from this function to ensure
    no additional references are kept to the span.
    """  # noqa: D402
    s = tracer.trace(*args, **kwargs)
    weakdict[s.span_id] = s
    return s


def test_single_thread_single_trace(tracer):
    """
    Ensure a simple trace doesn't leak span objects.
    """
    wd = WeakValueDictionary()
    with trace(wd, tracer, "span1"):
        assert len(wd) == 1
        with trace(wd, tracer, "span2"):
            assert len(wd) == 2

    # Spans are serialized and unreferenced when traces are finished
    # so gc-ing right away should delete all span objects.
    gc.collect()
    assert len(wd) == 0


def test_single_thread_multi_trace(tracer):
    """
    Ensure a trace in a thread is properly garbage collected.
    """
    wd = WeakValueDictionary()
    for _ in range(1000):
        with trace(wd, tracer, "span1"):
            with trace(wd, tracer, "span2"):
                pass
            with trace(wd, tracer, "span3"):
                pass

    gc.collect()
    assert len(wd) == 0


def test_multithread_trace(tracer):
    """
    Ensure a trace that crosses thread boundaries is properly garbage collected.
    """
    wd = WeakValueDictionary()
    state = []

    def _target(ctx):
        tracer.context_provider.activate(ctx)
        with trace(wd, tracer, "thread"):
            assert len(wd) == 2
        state.append(1)

    span = trace(wd, tracer, "")
    t = Thread(target=_target, args=(span.context,))
    t.start()
    t.join()
    # Ensure thread finished successfully
    assert state == [1]

    span.finish()
    del span
    gc.collect()
    assert len(wd) == 0
