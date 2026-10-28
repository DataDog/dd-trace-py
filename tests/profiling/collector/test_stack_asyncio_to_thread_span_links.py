"""Tests for the span link of asyncio.to_thread workers, which run the function inside a copy of the caller's Context.

The "thread key" is the span link entry the sampler uses for a plain thread. These tests read it with the test-only
_span_link_entry getter while the worker function runs, and after it returns.
"""

import asyncio
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from typing import Any
from typing import Callable
from typing import Optional

import pytest

from ddtrace._trace.provider import DefaultContextProvider
from ddtrace.contrib.internal.futures.patch import patch as futures_patch
from ddtrace.contrib.internal.futures.patch import unpatch as futures_unpatch
from ddtrace.internal.datadog.profiling import context_meta
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.datadog.profiling import stack as stack_module
from ddtrace.internal.service import ServiceStatus
from ddtrace.profiling import _span_links
from ddtrace.profiling.collector.stack import StackCollector
from ddtrace.trace import Span
from ddtrace.trace import Tracer


pytestmark = pytest.mark.skipif(not stack_module.is_available, reason="stack profiler not available")

if stack_module.is_available:
    from ddtrace.internal.datadog.profiling.stack import _stack

    THREAD = _stack.SPAN_LINK_DOMAIN_THREAD
    SPAN = _stack.SPAN_LINK_KIND_SPAN
    CONTEXT = _stack.SPAN_LINK_KIND_CONTEXT


class _OwnActiveRuleProvider(DefaultContextProvider):
    """Overrides active(), which turns inheritance mode off: the profiler only follows activation events."""

    def active(self) -> Any:
        return DefaultContextProvider.active(self)


class InjectedError(Exception):
    pass


@pytest.fixture
def exporter(tmp_path: Path) -> None:
    # The sampler hands its samples to the exporter, which must be started first.
    assert ddup.is_available
    ddup.config(env="test", service="test_asyncio_to_thread", version="1", output_filename=str(tmp_path / "profile"))
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


@pytest.fixture(params=["futures_off", "futures_on"])
def futures(request: pytest.FixtureRequest) -> Iterator[str]:
    # The futures integration activates a Context copy on the worker thread, outside the copied Context.
    if request.param == "futures_on":
        futures_patch()
    try:
        yield request.param
    finally:
        if request.param == "futures_on":
            futures_unpatch()


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


def on_worker_threads(name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
    """Return a replacement for the stack binding name that calls fn(real, *args) on worker threads only.

    The main thread runs the event loop and the profiler's activation listener, which keep the real binding.
    """
    real = getattr(stack_module, name)

    def dispatch(*args: Any) -> Any:
        if threading.current_thread() is threading.main_thread():
            return real(*args)
        return fn(real, *args)

    return dispatch


def test_to_thread_worker_carries_the_span_and_clears_it_on_return(
    collector: StackCollector, tracer: Tracer, futures: str
) -> None:
    traced = Worker()
    untraced = Worker()

    async def main() -> None:
        # With one worker the second call always runs on the first call's thread. A larger pool can start a new
        # thread, because the worker reports its result before it tells the pool it is idle.
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
        with tracer.trace("request") as span:
            await asyncio.to_thread(traced)
        await asyncio.to_thread(untraced)
        assert traced.entries == [span_entry(span)]

    asyncio.run(main())
    # The pooled worker must not keep the request's span for its next call.
    assert untraced.thread_id == traced.thread_id
    assert untraced.entries == [None]
    assert thread_entry(traced.thread_id) is None


def test_to_thread_worker_label_moves_to_the_parent_when_the_span_finishes(
    collector: StackCollector, tracer: Tracer, futures: str
) -> None:
    started = threading.Event()
    span_finished = threading.Event()
    entries: list[_stack.SpanLinkEntry] = []

    def worker() -> None:
        entries.append(thread_entry())
        started.set()
        assert span_finished.wait(5)
        entries.append(thread_entry())

    async def main() -> None:
        with tracer.trace("parent") as parent:
            span = tracer.trace("request")
            # The task copies the Context where span is active, and to_thread copies the task's Context.
            future = asyncio.ensure_future(asyncio.to_thread(worker))
            while not started.is_set():
                await asyncio.sleep(0.001)
            span.finish()
            span_finished.set()
            await future
            # Without the wrapper, the futures integration leaves a CONTEXT entry, which a finish never moves.
            assert entries == [span_entry(span), span_entry(parent)]

    asyncio.run(asyncio.wait_for(main(), 10))


def test_to_thread_worker_keeps_the_activation_label_with_a_custom_provider(
    collector: StackCollector, tracer: Tracer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tracer, "context_provider", _OwnActiveRuleProvider())
    futures_patch()
    try:
        worker = Worker()

        async def main() -> None:
            with tracer.trace("request") as span:
                await asyncio.to_thread(worker)
            # The futures integration activates a Context copy that carries the local root.
            assert worker.entries == [(span.span_id, span.span_id, "", CONTEXT)]

        asyncio.run(main())
    finally:
        futures_unpatch()
        tracer.context_provider.activate(None)


def test_to_thread_worker_writes_nothing_with_a_custom_provider_and_no_activation(
    collector: StackCollector, tracer: Tracer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tracer, "context_provider", _OwnActiveRuleProvider())
    link_calls: list[tuple[Any, ...]] = []
    worker = Worker()

    async def main() -> None:
        with tracer.trace("request"):
            monkeypatch.setattr(stack_module, "link", on_worker_threads("link", lambda real, *a: link_calls.append(a)))
            await asyncio.to_thread(worker)

    try:
        asyncio.run(main())
    finally:
        tracer.context_provider.activate(None)
    assert worker.entries == [None]
    assert link_calls == []


def test_to_thread_worker_writes_nothing_while_the_profiler_is_stopped(
    collector: StackCollector, tracer: Tracer
) -> None:
    collector.stop()
    worker = Worker()

    async def main() -> None:
        with tracer.trace("request"):
            await asyncio.to_thread(worker)

    asyncio.run(main())
    assert len(worker.calls) == 1
    assert worker.entries == [None]


def test_to_thread_passes_the_user_arguments_once(collector: StackCollector, tracer: Tracer, futures: str) -> None:
    worker = Worker(result="result")

    async def main() -> None:
        with tracer.trace("request") as span:
            # func= belongs to the user function, not to to_thread or the profiler wrapper.
            assert await asyncio.to_thread(worker, func=42) == "result"
            assert await asyncio.to_thread(worker, 1, 2, key="value") == "result"
        assert worker.entries == [span_entry(span), span_entry(span)]

    asyncio.run(main())
    assert worker.calls == [((), {"func": 42}), ((1, 2), {"key": "value"})]


def test_to_thread_worker_exception_propagates_and_the_thread_key_is_cleared(
    collector: StackCollector, tracer: Tracer
) -> None:
    error = ValueError("user error")
    worker = Worker(error=error)

    async def main() -> None:
        with tracer.trace("request") as span:
            with pytest.raises(ValueError) as excinfo:
                await asyncio.to_thread(worker)
        assert excinfo.value is error
        assert worker.entries == [span_entry(span)]

    asyncio.run(main())
    assert len(worker.calls) == 1
    assert thread_entry(worker.thread_id) is None


def _raise_in_inheritance_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    real = context_meta.inheritance_mode

    def inheritance_mode(provider: object) -> bool:
        if threading.current_thread() is threading.main_thread():
            return real(provider)
        raise InjectedError()

    monkeypatch.setattr(context_meta, "inheritance_mode", inheritance_mode)


def _raise_in_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    def lifecycle(real: Callable[[], int]) -> int:
        raise InjectedError()

    monkeypatch.setattr(stack_module, "lifecycle", on_worker_threads("lifecycle", lifecycle))


@pytest.mark.parametrize("inject", [_raise_in_lifecycle, _raise_in_inheritance_mode])
def test_to_thread_worker_runs_once_when_a_capability_check_fails(
    collector: StackCollector,
    tracer: Tracer,
    monkeypatch: pytest.MonkeyPatch,
    inject: Callable[[pytest.MonkeyPatch], None],
) -> None:
    worker = Worker(result="result")

    async def main() -> None:
        with tracer.trace("request"):
            inject(monkeypatch)
            assert await asyncio.to_thread(worker) == "result"

    asyncio.run(main())
    assert len(worker.calls) == 1
    # Nothing was written, so there is nothing to clean up.
    assert worker.entries == [None]


def test_to_thread_worker_runs_once_and_cleans_up_when_resolving_the_span_fails(
    collector: StackCollector, tracer: Tracer, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_resolve = context_meta.resolve
    link_entries: list[_stack.SpanLinkEntry] = []

    def resolve(item: Any) -> Any:
        if threading.current_thread() is threading.main_thread():
            return real_resolve(item)
        raise InjectedError()

    def link(real: Callable[..., None], token: int, thread: bool, task_id: int, greenlet_id: int, entry: Any) -> None:
        link_entries.append(entry)
        real(token, thread, task_id, greenlet_id, entry)

    worker = Worker(result="result")

    async def main() -> None:
        with tracer.trace("request"):
            monkeypatch.setattr(context_meta, "resolve", resolve)
            monkeypatch.setattr(stack_module, "link", on_worker_threads("link", link))
            assert await asyncio.to_thread(worker) == "result"

    asyncio.run(main())
    assert len(worker.calls) == 1
    assert worker.entries == [None]
    # The failure came before the write, and the cleanup still ran.
    assert link_entries == [None]


def test_to_thread_worker_runs_once_and_cleans_up_when_the_write_fails(
    collector: StackCollector, tracer: Tracer, monkeypatch: pytest.MonkeyPatch
) -> None:
    link_entries: list[_stack.SpanLinkEntry] = []

    def link(real: Callable[..., None], token: int, thread: bool, task_id: int, greenlet_id: int, entry: Any) -> None:
        link_entries.append(entry)
        if entry is not None:
            raise InjectedError()
        real(token, thread, task_id, greenlet_id, entry)

    worker = Worker(result="result")

    async def main() -> None:
        with tracer.trace("request") as span:
            monkeypatch.setattr(stack_module, "link", on_worker_threads("link", link))
            assert await asyncio.to_thread(worker) == "result"
        # The wrapper passes span_info's entry, before the native side stores a missing span type as "".
        assert link_entries == [context_meta.span_info(span), None]

    asyncio.run(main())
    assert len(worker.calls) == 1


def test_to_thread_worker_runs_once_and_cleans_up_when_revalidation_fails(
    collector: StackCollector, tracer: Tracer, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = _span_links.retarget_finished

    def retarget_finished(*args: Any) -> None:
        if threading.current_thread() is threading.main_thread():
            return real(*args)
        raise InjectedError()

    worker = Worker(result="result")

    async def main() -> None:
        with tracer.trace("request") as span:
            monkeypatch.setattr(_span_links, "retarget_finished", retarget_finished)
            assert await asyncio.to_thread(worker) == "result"
        # The write landed before the failure, and the cleanup removed it.
        assert worker.entries == [span_entry(span)]

    asyncio.run(main())
    assert len(worker.calls) == 1
    assert thread_entry(worker.thread_id) is None


@pytest.mark.parametrize("outcome", ["returns", "raises"])
def test_to_thread_worker_outcome_passes_through_when_cleanup_fails(
    collector: StackCollector, tracer: Tracer, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    def link(real: Callable[..., None], token: int, thread: bool, task_id: int, greenlet_id: int, entry: Any) -> None:
        if entry is None:
            raise InjectedError()
        real(token, thread, task_id, greenlet_id, entry)

    error = ValueError("user error")
    worker = Worker(result="result", error=error if outcome == "raises" else None)

    async def main() -> None:
        with tracer.trace("request"):
            monkeypatch.setattr(stack_module, "link", on_worker_threads("link", link))
            if outcome == "raises":
                with pytest.raises(ValueError) as excinfo:
                    await asyncio.to_thread(worker)
                assert excinfo.value is error
            else:
                assert await asyncio.to_thread(worker) == "result"

    asyncio.run(main())
    assert len(worker.calls) == 1


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_asyncio_to_thread_labels",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    parametrize={"FUTURES_INTEGRATION": ["off", "on"]},
    err=None,
    timeout=60,
)
def test_stack_labels_asyncio_to_thread_worker_samples_with_the_request_span() -> None:
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    import os
    import threading
    import time

    from ddtrace import ext
    from ddtrace.contrib.internal.futures.patch import patch as futures_patch
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    if os.environ["FUTURES_INTEGRATION"] == "on":
        futures_patch()

    endpoint = "to-thread-endpoint"
    worker_thread_ids = []

    def traced_worker_work() -> None:
        worker_thread_ids.append(threading.get_ident())
        time.sleep(0.4)

    def untraced_worker_work() -> None:
        worker_thread_ids.append(threading.get_ident())
        time.sleep(0.4)

    async def main() -> int:
        # With one worker the second call always runs on the first call's thread.
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
        with tracer.trace("to-thread.request", resource=endpoint, span_type=ext.SpanTypes.WEB) as span:
            await asyncio.to_thread(traced_worker_work)
        await asyncio.to_thread(untraced_worker_work)
        return span.span_id

    tracer._endpoint_call_counter_span_processor.enable()
    p = profiler.Profiler(tracer=tracer)
    p.start()
    span_id = asyncio.run(main())
    p.stop()

    # The pooled worker runs both calls.
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
