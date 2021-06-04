"""
Variety of test cases ensuring that ddtrace does not leak memory.
"""
import gc
import os
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


def test_leak(tracer):
    wd = WeakValueDictionary()
    span = trace(wd, tracer, "span1")
    span2 = trace(wd, tracer, "span2")
    assert len(wd) == 2
    gc.collect()
    assert len(wd) == 2
    span2.finish()
    span.finish()
    del span, span2
    gc.collect()
    assert len(wd) == 0


def test_fork_open_span(tracer):
    wd = WeakValueDictionary()
    span = trace(wd, tracer, "span")
    pid = os.fork()

    if pid == 0:
        assert len(wd) == 1
        del span
        gc.collect()
        # span is still open an in the context
        assert len(wd) == 1
        span2 = trace(wd, tracer, "span2")
        assert span2._parent is None
        assert len(wd) == 2
        span2.finish()

        del span2
        gc.collect()
        assert len(wd) == 0
        os._exit(12)

    assert len(wd) == 1
    span.finish()
    del span
    gc.collect()
    assert len(wd) == 0

    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12
