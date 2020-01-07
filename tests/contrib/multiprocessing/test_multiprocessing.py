import multiprocessing
from multiprocessing import Process, Pool

try:
    # for PY3 use the SimpleQueue provided by the default context
    from multiprocessing import SimpleQueue
except ImportError:
    from multiprocessing.queues import SimpleQueue
import pytest
import os
from ddtrace.compat import PY2


def hello(q, name=None):
    msg = "hello {}".format(name) if name else "hello"
    q.put(msg)


def square(x):
    return x * x


@pytest.fixture
def tracer(monkeypatch):
    from ddtrace.internal import writer

    # create trace queue to be shared by writer across processes
    trace_queue = SimpleQueue()

    class MockWriter(writer.AgentWriter):
        def __init__(self, *args, **kwargs):
            super(MockWriter, self).__init__(*args, **kwargs)
            # stop the writer thread to avoid flushing queue
            self.stop()
            # replace writers trace queue with multiprocessing queue
            self._trace_queue = trace_queue

        def write(self, spans=None, services=None):
            if spans:
                self._trace_queue.put(list(s.to_dict() for s in spans))

        def flush_queue(self, *args, **kwargs):
            # no-op flush in case writer thread is not stopped before spans are
            # written to queue
            pass

        def pop(self):
            traces = []
            while not self._trace_queue.empty():
                traces.extend(self._trace_queue.get())
            return list(reversed(traces))

    from ddtrace.contrib.multiprocessing import patch, unpatch

    patch()

    from ddtrace import Pin, Tracer

    tracer = Tracer()
    monkeypatch.setattr(tracer, "writer", MockWriter())
    Pin.override(multiprocessing, tracer=tracer)

    yield tracer
    unpatch()


def test_process(tracer):
    q = SimpleQueue()
    p = Process(target=hello, args=(q,))
    p.start()
    p.join()

    assert q.get() == "hello"

    spans = tracer.writer.pop()
    assert len(spans) == 1
    assert spans[0]["name"] == "multiprocessing.run"
    assert spans[0]["parent_id"] is None
    assert spans[0]["span_id"] is not None
    assert spans[0]["service"] == "multiprocessing"
    assert spans[0]["type"] == "worker"
    assert spans[0]["error"] == 0
    assert spans[0]["metrics"]["system.pid"] != os.getpid()


def test_process_pos_args(tracer):
    def check():
        spans = tracer.writer.pop()
        assert len(spans) == 1
        assert spans[0]["name"] == "multiprocessing.run"
        assert spans[0]["parent_id"] is None
        assert spans[0]["span_id"] is not None
        assert spans[0]["service"] == "multiprocessing"
        assert spans[0]["type"] == "worker"
        assert spans[0]["error"] == 0
        assert spans[0]["metrics"]["system.pid"] != os.getpid()

    # use all positional arguments
    q = SimpleQueue()
    p = Process(None, hello, "my-process", (q,), dict(name="xyz"))
    p.start()
    p.join()
    assert q.get() == "hello xyz"
    check()

    # use some positional arguments
    q = SimpleQueue()
    p = Process(None, hello, "my-process", args=(q,), kwargs=dict(name="xyz"))
    p.start()
    p.join()
    assert q.get() == "hello xyz"
    check()


def test_process_with_parent(tracer):
    with tracer.trace("parent"):
        q = SimpleQueue()
        p = Process(target=hello, args=(q,))
        p.start()
        p.join()
        assert q.get() == "hello"

    spans = tracer.writer.pop()
    assert len(spans) == 2

    assert spans[0]["name"] == "parent"
    assert spans[0]["parent_id"] is None
    assert spans[0]["span_id"] is not None
    assert spans[0]["metrics"]["system.pid"] == os.getpid()

    assert spans[1]["name"] == "multiprocessing.run"
    assert spans[1]["parent_id"] == spans[0]["span_id"]
    assert spans[1]["span_id"] is not None
    assert spans[1]["service"] == "multiprocessing"
    assert spans[1]["type"] == "worker"
    assert spans[1]["error"] == 0
    assert "system.pid" not in spans[1]["metrics"]


@pytest.mark.skipif(PY2, reason="start methods added in Python 3.4")
def test_process_forked(tracer):
    mp_ctx = multiprocessing.get_context("fork")

    with tracer.trace("parent"):
        q = mp_ctx.SimpleQueue()
        p = mp_ctx.Process(target=hello, args=(q,))
        p.start()
        p.join()
        assert q.get() == "hello"

    spans = tracer.writer.pop()
    assert len(spans) == 2

    assert spans[0]["name"] == "parent"
    assert spans[0]["parent_id"] is None
    assert spans[0]["span_id"] is not None
    assert spans[0]["metrics"]["system.pid"] == os.getpid()

    assert spans[1]["name"] == "multiprocessing.run"
    assert spans[1]["parent_id"] == spans[0]["span_id"]
    assert spans[1]["span_id"] is not None
    assert spans[1]["service"] == "multiprocessing"
    assert spans[1]["type"] == "worker"
    assert spans[1]["error"] == 0
    assert "system.pid" not in spans[1]["metrics"]


def test_pool_map(tracer):
    p = Pool(processes=4)
    result = p.map(square, [1, 2, 3])

    assert result == [1, 4, 9]

    spans = tracer.writer.pop()
    # TODO: Add support for pool maps
    assert len(spans) == 0
