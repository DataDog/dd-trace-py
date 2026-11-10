"""Tests for the stack collector's span link writers: the activation listener, the finish listener and start/stop."""

from collections.abc import Iterator
from pathlib import Path
import threading
from typing import Any
from typing import Callable
from typing import Optional

import pytest

from ddtrace._trace.context import Context
from ddtrace._trace.provider import DefaultContextProvider
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.datadog.profiling import stack as stack_module
from ddtrace.internal.service import ServiceStatus
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
    """Overrides active(), which turns inheritance mode off: finished spans are cleared, not retargeted."""

    def active(self) -> Any:
        return DefaultContextProvider.active(self)


@pytest.fixture(params=["inheritance", "clear"])
def mode(request: pytest.FixtureRequest, tracer: Tracer, monkeypatch: pytest.MonkeyPatch) -> str:
    if request.param == "clear":
        monkeypatch.setattr(tracer, "context_provider", _OwnActiveRuleProvider())
    mode: str = request.param
    return mode


@pytest.fixture
def exporter(tmp_path: Path) -> None:
    # The sampler hands its samples to the exporter, which must be started first.
    assert ddup.is_available
    ddup.config(env="test", service="test_stack_span_links", version="1", output_filename=str(tmp_path / "profile"))
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


def thread_entry(thread_id: Optional[int] = None) -> "_stack.SpanLinkEntry":
    found = _stack._span_link_entry(THREAD, threading.get_ident() if thread_id is None else thread_id)
    return None if found is None else found[0]


def span_entry(span: Span) -> "_stack.SpanLinkEntry":
    # The native entry stores a missing span type as an empty string.
    return (span.span_id, span._local_root.span_id, span._local_root.span_type or "", SPAN)


def on_another_thread(fn: Callable[[], None]) -> None:
    thread = threading.Thread(target=fn)
    thread.start()
    thread.join(10)
    assert not thread.is_alive()


class ActiveOnWorker:
    """A worker thread that activates a span, then waits, so another thread can finish the span."""

    def __init__(self, tracer: Tracer, span: Optional[Span]) -> None:
        self._tracer = tracer
        self._span = span
        self._activated = threading.Event()
        self._release = threading.Event()
        self._thread = threading.Thread(target=self._run)
        self.thread_id = 0

    def _run(self) -> None:
        self.thread_id = threading.get_ident()
        self._tracer.context_provider.activate(self._span)
        self._activated.set()
        self._release.wait(10)

    def __enter__(self) -> "ActiveOnWorker":
        self._thread.start()
        assert self._activated.wait(5)
        return self

    def __exit__(self, *_: object) -> None:
        self._release.set()
        self._thread.join(10)
        assert not self._thread.is_alive()


def test_activating_no_span_erases_the_thread_key(collector: StackCollector, tracer: Tracer) -> None:
    with tracer.trace("request") as span:
        assert thread_entry() == span_entry(span)
        tracer.context_provider.activate(None)
        assert thread_entry() is None


def test_activating_a_span_id_0_context_erases_the_thread_key(collector: StackCollector, tracer: Tracer) -> None:
    # DDRuntimeContext activates NonRecordingSpan(INVALID_SPAN_CONTEXT) as a Context with span id 0.
    with tracer.trace("request") as span:
        assert thread_entry() == span_entry(span)
        tracer.context_provider.activate(Context(trace_id=0, span_id=0))
        assert thread_entry() is None


def test_finish_on_another_thread_moves_the_thread_key_to_the_parent(
    collector: StackCollector, tracer: Tracer, mode: str
) -> None:
    root = tracer.start_span("root", activate=False)
    child = tracer.start_span("child", child_of=root, activate=False)
    with ActiveOnWorker(tracer, child) as worker:
        assert thread_entry(worker.thread_id) == span_entry(child)
        child.finish()
        assert thread_entry(worker.thread_id) == (span_entry(root) if mode == "inheritance" else None)
        root.finish()
        assert thread_entry(worker.thread_id) is None


def test_activating_a_finished_span_publishes_what_active_returns(
    collector: StackCollector, tracer: Tracer, mode: str
) -> None:
    root = tracer.start_span("root", activate=False)
    child = tracer.start_span("child", child_of=root, activate=False)
    child.finish()
    tracer.context_provider.activate(child)
    assert thread_entry() == (span_entry(root) if mode == "inheritance" else None)
    root.finish()


def test_context_entry_survives_the_finish_of_the_span_with_the_same_id(
    collector: StackCollector, tracer: Tracer, mode: str
) -> None:
    root = tracer.start_span("root", activate=False)
    child = tracer.start_span("child", child_of=root, activate=False)
    with ActiveOnWorker(tracer, child) as worker:
        # This thread runs a copy of the child's Context, as the futures integration does for submitted work.
        tracer.context_provider.activate(Context(trace_id=child.trace_id, span_id=child.span_id))
        context_entry = (child.span_id, child.span_id, "", CONTEXT)
        assert thread_entry() == context_entry

        child.finish()
        assert thread_entry() == context_entry
        assert thread_entry(worker.thread_id) == (span_entry(root) if mode == "inheritance" else None)
    root.finish()
    assert thread_entry() == context_entry


def test_start_publishes_the_span_active_before_start(exporter: None, tracer: Tracer) -> None:
    with tracer.trace("request") as span:
        c = StackCollector(tracer=tracer)
        c.start()
        try:
            assert thread_entry() == span_entry(span)
        finally:
            c.stop()


def test_restart_drops_stale_keys_and_republishes_the_active_span(collector: StackCollector, tracer: Tracer) -> None:
    other = tracer.start_span("other thread", activate=False)
    with ActiveOnWorker(tracer, other) as worker:
        with tracer.trace("parent") as parent:
            with tracer.trace("child") as child:
                assert thread_entry() == span_entry(child)
                assert thread_entry(worker.thread_id) == span_entry(other)
                collector.stop()
                assert thread_entry() is None
                assert thread_entry(worker.thread_id) is None
            # The child finished, and the parent became active again, while the profiler was stopped.
            collector.start()
            assert thread_entry() == span_entry(parent)
            # Other threads are published on their next activation.
            assert thread_entry(worker.thread_id) is None
    other.finish()


def test_listener_running_after_stop_leaves_no_key(collector: StackCollector, tracer: Tracer) -> None:
    # The activation was dispatched before stop, and the listener runs after it.
    with tracer.trace("request") as span:
        collector.stop()
        collector._link_span(tracer.context_provider, span)
        assert thread_entry() is None


def test_stop_between_lifecycle_and_link_leaves_no_key(
    collector: StackCollector, tracer: Tracer, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_link = stack_module.link

    def stop_then_link(*args: Any) -> None:
        collector.stop()
        real_link(*args)

    with tracer.trace("request") as span:
        monkeypatch.setattr(stack_module, "link", stop_then_link)
        collector._link_span(tracer.context_provider, span)
        monkeypatch.undo()
        assert thread_entry() is None


def test_stop_and_start_between_lifecycle_and_link_keeps_the_new_lifecycle_key(
    collector: StackCollector, tracer: Tracer, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_link = stack_module.link
    restarted: list[bool] = []

    def restart_then_link(*args: Any) -> None:
        if not restarted:
            restarted.append(True)
            collector.stop()
            # start() republishes the active span with the new token.
            collector.start()
        real_link(*args)

    stale = tracer.start_span("stale", activate=False)
    with tracer.trace("request") as span:
        monkeypatch.setattr(stack_module, "link", restart_then_link)
        # A listener for an activation that the active span has since replaced.
        collector._link_span(tracer.context_provider, stale)
        monkeypatch.undo()
        assert restarted
        assert thread_entry() == span_entry(span)
    stale.finish()


def test_span_finishing_before_the_activation_write_lands(
    collector: StackCollector, tracer: Tracer, mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tracer.start_span("root", activate=False)
    child = tracer.start_span("child", child_of=root, activate=False)
    real_link = stack_module.link

    def finish_then_link(*args: Any) -> None:
        if not child.finished:
            on_another_thread(child.finish)
            # The finish listener ran before the write, so it had nothing to move.
            assert not stack_module.holds_span(child.span_id)
        real_link(*args)

    monkeypatch.setattr(stack_module, "link", finish_then_link)
    tracer.context_provider.activate(child)
    monkeypatch.undo()

    assert child.finished
    # The write landed with the finished child, and the re-check after it moved the key off the child.
    assert thread_entry() == (span_entry(root) if mode == "inheritance" else None)
    assert not stack_module.holds_span(child.span_id)
    root.finish()


@pytest.mark.parametrize("has_grandparent", [True, False])
def test_parent_finishing_while_retargeting_to_it(
    collector: StackCollector, tracer: Tracer, mode: str, has_grandparent: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    grandparent = tracer.start_span("grandparent", activate=False) if has_grandparent else None
    parent = tracer.start_span("parent", child_of=grandparent, activate=False)
    child = tracer.start_span("child", child_of=parent, activate=False)
    real_retarget = stack_module.retarget

    def finish_parent_then_retarget(token: int, span_id: int, entry: "_stack.SpanLinkEntry") -> None:
        if span_id == child.span_id and not parent.finished:
            on_another_thread(parent.finish)
        real_retarget(token, span_id, entry)

    with ActiveOnWorker(tracer, child) as worker:
        monkeypatch.setattr(stack_module, "retarget", finish_parent_then_retarget)
        child.finish()
        monkeypatch.undo()

        assert parent.finished
        if mode == "inheritance" and grandparent is not None:
            assert thread_entry(worker.thread_id) == span_entry(grandparent)
        else:
            assert thread_entry(worker.thread_id) is None
        assert not stack_module.holds_span(child.span_id)
        assert not stack_module.holds_span(parent.span_id)
    if grandparent is not None:
        grandparent.finish()
