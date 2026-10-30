"""Tests for the span link of anyio.to_thread.run_sync workers, which run the function inside a copy of the caller's
Context.

The "thread key" is the span link entry the sampler uses for a plain thread. These tests read it with the test-only
_span_link_entry getter while the worker function runs, and after it returns. Each test runs on the asyncio and the
trio backend of AnyIO.
"""

from collections.abc import Iterator
from pathlib import Path
import threading
from typing import Any
from typing import Optional

import pytest

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.datadog.profiling import stack as stack_module
from ddtrace.internal.service import ServiceStatus
from ddtrace.profiling.collector.stack import StackCollector
from ddtrace.trace import Span
from ddtrace.trace import Tracer


anyio = pytest.importorskip("anyio")
pytest.importorskip("trio")

pytestmark = pytest.mark.skipif(not stack_module.is_available, reason="stack profiler not available")

if stack_module.is_available:
    from ddtrace.internal.datadog.profiling.stack import _stack

    THREAD = _stack.SPAN_LINK_DOMAIN_THREAD
    SPAN = _stack.SPAN_LINK_KIND_SPAN


@pytest.fixture
def exporter(tmp_path: Path) -> None:
    # The sampler hands its samples to the exporter, which must be started first.
    assert ddup.is_available
    ddup.config(env="test", service="test_anyio_to_thread", version="1", output_filename=str(tmp_path / "profile"))
    ddup.start()


@pytest.fixture
def collector(exporter: None, tracer: Tracer) -> Iterator[StackCollector]:
    tracer.context_provider.activate(None)
    c = StackCollector(tracer=tracer)
    c.start()
    try:
        yield c
    finally:
        if c.status == ServiceStatus.RUNNING:
            c.stop()
        tracer.context_provider.activate(None)


@pytest.fixture(params=["asyncio", "trio"])
def backend(request: pytest.FixtureRequest) -> str:
    name: str = request.param
    return name


def thread_entry(thread_id: Optional[int] = None) -> "_stack.SpanLinkEntry":
    found = _stack._span_link_entry(THREAD, threading.get_ident() if thread_id is None else thread_id)
    return None if found is None else found[0]


def span_entry(span: Span) -> "_stack.SpanLinkEntry":
    # The native entry stores a missing span type as an empty string.
    return (span.span_id, span._local_root.span_id, span._local_root.span_type or "", SPAN)


class Worker:
    """A worker function that records its thread and its thread key, and counts its calls."""

    def __init__(self, result: object = None, error: Optional[BaseException] = None) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.entries: list[_stack.SpanLinkEntry] = []
        self.thread_id = 0
        self._result = result
        self._error = error

    def __call__(self, *args: Any, **kwargs: Any) -> object:
        self.calls.append((args, kwargs))
        self.thread_id = threading.get_ident()
        self.entries.append(thread_entry())
        if self._error is not None:
            raise self._error
        return self._result


def test_run_sync_worker_carries_the_span_and_clears_it_on_return(
    collector: StackCollector, tracer: Tracer, backend: str
) -> None:
    traced = Worker()
    untraced = Worker()

    async def main() -> None:
        with tracer.trace("request") as span:
            await anyio.to_thread.run_sync(traced)
        await anyio.to_thread.run_sync(untraced)
        assert traced.entries == [span_entry(span)]

    anyio.run(main, backend=backend)
    if backend == "asyncio":
        # AnyIO's asyncio backend keeps idle workers per event loop and marks a worker idle on the loop before it
        # delivers the result, so the second call runs on the first call's thread. Trio's thread cache is shared by
        # the whole process, so a worker that an earlier test abandoned could take the second call there.
        assert untraced.thread_id == traced.thread_id
    # The worker must not keep the request's span for its next call, on any backend.
    assert untraced.entries == [None]
    assert thread_entry(traced.thread_id) is None


def test_abandoned_run_sync_worker_label_moves_to_the_parent_when_the_span_finishes(
    collector: StackCollector, tracer: Tracer, backend: str
) -> None:
    started = threading.Event()
    span_finished = threading.Event()
    done = threading.Event()
    entries: list[_stack.SpanLinkEntry] = []

    def worker() -> None:
        entries.append(thread_entry())
        started.set()
        try:
            assert span_finished.wait(5)
            entries.append(thread_entry())
        finally:
            done.set()

    async def cancel_when_started(scope: Any) -> None:
        while not started.is_set():
            await anyio.sleep(0.001)
        scope.cancel()

    async def main() -> None:
        with tracer.trace("parent") as parent:
            with tracer.trace("request") as span:
                with anyio.CancelScope() as scope:
                    async with anyio.create_task_group() as tg:
                        tg.start_soon(cancel_when_started, scope)
                        # Cancelling abandons the worker, and the request span finishes while the worker runs.
                        await anyio.to_thread.run_sync(worker, abandon_on_cancel=True)
            span_finished.set()
            while not done.is_set():
                await anyio.sleep(0.001)
            assert entries == [span_entry(span), span_entry(parent)]

    anyio.run(main, backend=backend)


def test_run_sync_passes_the_user_arguments_once(collector: StackCollector, tracer: Tracer, backend: str) -> None:
    worker = Worker(result="result")

    async def main() -> None:
        with tracer.trace("request") as span:
            assert await anyio.to_thread.run_sync(worker, 1, 2) == "result"
            # func= binds the same parameter, and the wrapper must still find it.
            assert await anyio.to_thread.run_sync(func=worker) == "result"
        assert worker.entries == [span_entry(span), span_entry(span)]

    anyio.run(main, backend=backend)
    assert worker.calls == [((1, 2), {}), ((), {})]


def test_run_sync_worker_exception_propagates_and_the_thread_key_is_cleared(
    collector: StackCollector, tracer: Tracer, backend: str
) -> None:
    error = ValueError("user error")
    worker = Worker(error=error)

    async def main() -> None:
        with tracer.trace("request") as span:
            with pytest.raises(ValueError) as excinfo:
                await anyio.to_thread.run_sync(worker)
        assert excinfo.value is error
        assert worker.entries == [span_entry(span)]

    anyio.run(main, backend=backend)
    assert len(worker.calls) == 1
    assert thread_entry(worker.thread_id) is None


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_anyio_to_thread_labels",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
        # Trio asserts that its internal lock is a plain _thread lock, which the lock profiler replaces.
        "DD_PROFILING_LOCK_ENABLED": "false",
    },
    # anyio_first: anyio.to_thread is imported, and run_sync bound to a local name, before the profiler starts.
    parametrize={"BACKEND": ["asyncio", "trio"], "IMPORT_ORDER": ["anyio_first", "profiler_first"]},
    err=None,
    timeout=60,
)
def test_stack_labels_anyio_worker_samples_with_the_request_span() -> None:
    import os
    import sys

    if os.environ["IMPORT_ORDER"] == "anyio_first":
        from anyio.to_thread import run_sync

    import threading
    import time

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    tracer._endpoint_call_counter_span_processor.enable()
    if os.environ["IMPORT_ORDER"] == "profiler_first":
        # Otherwise this variant would test the anyio_first path again.
        assert "anyio.to_thread" not in sys.modules
    p = profiler.Profiler(tracer=tracer)
    p.start()

    import anyio

    if os.environ["IMPORT_ORDER"] == "profiler_first":
        from anyio.to_thread import run_sync

    endpoint = "anyio-worker-endpoint"
    worker_thread_ids = []

    def traced_worker_work() -> None:
        worker_thread_ids.append(threading.get_ident())
        time.sleep(0.4)

    def untraced_worker_work() -> None:
        worker_thread_ids.append(threading.get_ident())
        time.sleep(0.4)

    async def main() -> int:
        with tracer.trace("anyio.request", resource=endpoint, span_type=ext.SpanTypes.WEB) as span:
            await run_sync(traced_worker_work)
        await run_sync(untraced_worker_work)
        return span.span_id

    span_id = anyio.run(main, backend=os.environ["BACKEND"])
    p.stop()

    # The second call runs on the same pooled worker. In this process no other worker can be idle on either backend.
    assert len(worker_thread_ids) == 2
    assert worker_thread_ids[0] == worker_thread_ids[1]

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    traced_samples = pprof_utils.get_samples_with_function(profile, wall_samples, "traced_worker_work")
    untraced_samples = pprof_utils.get_samples_with_function(profile, wall_samples, "untraced_worker_work")
    span_id = pprof_utils.reinterpret_int_as_int64(span_id)

    assert traced_samples
    assert untraced_samples
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") == endpoint for sample in traced_samples)
    assert all(pprof_utils.get_num_label(profile, sample, "span id") == span_id for sample in traced_samples)
    assert all(pprof_utils.get_num_label(profile, sample, "local root span id") == span_id for sample in traced_samples)
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") is None for sample in untraced_samples)
    assert all(pprof_utils.get_num_label(profile, sample, "span id") is None for sample in untraced_samples)
