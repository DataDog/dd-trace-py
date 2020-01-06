import multiprocessing
from multiprocessing import Process, SimpleQueue, Pool
import pytest
import os


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
            return traces

    from ddtrace.contrib.multiprocessing import patch, unpatch
    patch()

    from ddtrace import Pin, Tracer
    tracer = Tracer()
    monkeypatch.setattr(tracer, 'writer', MockWriter())
    Pin.override(multiprocessing, tracer=tracer)

    yield tracer
    unpatch()


def test_single_process_no_context(tracer):
    def f(name):
        print('hello', name)

    p = Process(target=f, args=('bob',))
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


def test_single_process_with_context(tracer):
    def f(name):
        print('hello', name)

    with tracer.trace("parent"):
        p = Process(target=f, args=('bob',))
        p.start()
        p.join()

    spans = tracer.writer.pop()
    assert len(spans) == 2

    assert spans[1]["name"] == "parent"
    assert spans[1]["parent_id"] is None
    assert spans[1]["span_id"] is not None
    assert spans[1]["metrics"]["system.pid"] == os.getpid()

    assert spans[0]["name"] == "multiprocessing.run"
    assert spans[0]["parent_id"] == spans[1]["span_id"]
    assert spans[0]["span_id"] is not None
    assert spans[0]["service"] == "multiprocessing"
    assert spans[0]["type"] == "worker"
    assert spans[0]["error"] == 0
    assert "system.pid" not in spans[0]["metrics"]


def test_pool_map(tracer):
    def f(x):
        return x * x

    p = Pool(processes=4)
    result = p.map(f, [1, 2, 3])

    assert result == [1, 4, 9]
