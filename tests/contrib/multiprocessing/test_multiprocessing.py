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
    def f(name):
        print("hello", name)

    p = Process(target=f, args=("bob",))
    p.start()
    p.join()

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

    def f(name, suffix=None):
        name = "{} {}".format(name, suffix) if suffix else name
        print("hello {}".format(name))

    # use all positional arguments
    p = Process(None, f, "my-process", ("bob",), dict(suffix="sr."))
    p.start()
    p.join()
    check()

    # use some positional arguments
    p = Process(None, f, "my-process", args=("bob",), kwargs=dict(suffix="sr."))
    p.start()
    p.join()
    check()


def test_process_with_parent(tracer):
    def f(name):
        print("hello", name)

    with tracer.trace("parent"):
        p = Process(target=f, args=("bob",))
        p.start()
        p.join()

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
    def f(name):
        print("hello", name)

    mp_ctx = multiprocessing.get_context("fork")

    with tracer.trace("parent"):
        p = mp_ctx.Process(target=f, args=("bob",))
        p.start()
        p.join()

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
    def f(x):
        return x * x

    p = Pool(processes=4)
    result = p.map(f, [1, 2, 3])

    assert result == [1, 4, 9]
