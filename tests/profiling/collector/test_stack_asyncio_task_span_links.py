"""Tests for the asyncio task keys of the stack profiler's span links.

The stack profiler keeps a native map from each execution unit (a thread, an asyncio task or a greenlet) to the span
it runs. A task's key is id(task). Three writers fill task keys: the activation listener (a span activated inside a
task), the creation hook on loop.create_task (a new task inherits its creator's span), and the first step of an eager
task (a task whose first step runs inside create_task). A weakref callback erases a task's key when the task dies.

_stack._span_link_entry returns None for a missing key, and otherwise (entry, birth). A tombstone is a key that is
kept with no span (entry is None), for example after the root span the task ran finished. The birth is a counter
value fixed when the key is created, so an unchanged birth shows that the same key was updated, not recreated.

The sampler does not use task keys yet, because the per-task attribution switch is still off. So these tests read the
keys with the test-only _stack._span_link_entry getter instead of decoding profiles.
"""

import asyncio
from collections.abc import Coroutine
from collections.abc import Iterator
import contextvars
import functools
import gc
from pathlib import Path
import sys
import threading
from typing import Any
from typing import Callable
from typing import Optional
from typing import TypeVar

import pytest

from ddtrace._trace.provider import DefaultContextProvider
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.datadog.profiling import stack as stack_module
from ddtrace.internal.service import ServiceStatus
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap
from ddtrace.profiling import _asyncio
from ddtrace.profiling.collector.stack import StackCollector
from ddtrace.trace import Span
from ddtrace.trace import Tracer
from tests.profiling.collector.test_utils import uvloop_available


pytestmark = pytest.mark.skipif(not stack_module.is_available, reason="stack profiler not available")

if stack_module.is_available:
    from ddtrace.internal.datadog.profiling.stack import _stack

    TASK = _stack.SPAN_LINK_DOMAIN_ASYNCIO_TASK
    THREAD = _stack.SPAN_LINK_DOMAIN_THREAD
    SPAN = _stack.SPAN_LINK_KIND_SPAN
    CONTEXT = _stack.SPAN_LINK_KIND_CONTEXT

T = TypeVar("T")

# Every test runs its loop for well under a second. The bound turns a hang into a failure.
LOOP_TIMEOUT_SECONDS = 10

requires_eager_tasks = pytest.mark.skipif(sys.version_info < (3, 12), reason="eager tasks were added in Python 3.12")
requires_task_context = pytest.mark.skipif(
    sys.version_info < (3, 11), reason="create_task(context=...) was added in Python 3.11"
)


class _OwnActiveRuleProvider(DefaultContextProvider):
    """Overrides active(), which turns inheritance mode off: the profiler cannot predict what tasks inherit."""

    def active(self) -> Any:
        return DefaultContextProvider.active(self)


@pytest.fixture
def exporter(tmp_path: Path) -> None:
    # The sampler hands its samples to the exporter, which must be started first.
    assert ddup.is_available
    ddup.config(
        env="test", service="test_stack_asyncio_task_span_links", version="1", output_filename=str(tmp_path / "prof")
    )
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


@pytest.fixture(params=["integration off", "integration on"])
def asyncio_integration(request: pytest.FixtureRequest) -> Iterator[bool]:
    """Run the test with the asyncio tracing integration unpatched, then patched."""
    from ddtrace.contrib.internal.asyncio import patch as asyncio_patch

    assert not getattr(asyncio, "_datadog_patch", False), "the asyncio integration was left patched"
    if request.param == "integration off":
        yield False
        return
    asyncio_patch.patch()
    try:
        yield True
    finally:
        asyncio_patch.unpatch()


def run(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(asyncio.wait_for(coro, LOOP_TIMEOUT_SECONDS))


def task_key(task: object) -> Optional[tuple["_stack.SpanLinkEntry", int]]:
    """Return None if the task has no key, otherwise (entry, birth). The entry is None for a tombstone."""
    return _stack._span_link_entry(TASK, id(task))


def task_entry(task: object) -> "_stack.SpanLinkEntry":
    found = task_key(task)
    return None if found is None else found[0]


def span_entry(span: Span) -> "_stack.SpanLinkEntry":
    # The native entry stores a missing span type as an empty string.
    return (span.span_id, span._local_root.span_id, span._local_root.span_type or "", SPAN)


def on_another_thread(fn: Callable[[], None]) -> None:
    thread = threading.Thread(target=fn)
    thread.start()
    thread.join(10)
    assert not thread.is_alive()


async def own_key() -> "_stack.SpanLinkEntry":
    """Return the running task's own entry, without touching the tracer."""
    return task_entry(asyncio.current_task())


async def own_key_after_suspending() -> "_stack.SpanLinkEntry":
    """Return the running task's own entry from its first step, after checking it is unchanged in the next one."""
    first = task_entry(asyncio.current_task())
    await asyncio.sleep(0)
    assert task_entry(asyncio.current_task()) == first
    return first


async def wait_forever(future: "asyncio.Future[None]") -> None:
    await future


def create_with_loop_create_task(coro: Coroutine[Any, Any, Any]) -> "asyncio.Future[Any]":
    return asyncio.get_running_loop().create_task(coro)


def create_with_asyncio_create_task(coro: Coroutine[Any, Any, Any]) -> "asyncio.Future[Any]":
    return asyncio.create_task(coro)


def create_with_ensure_future(coro: Coroutine[Any, Any, Any]) -> "asyncio.Future[Any]":
    return asyncio.ensure_future(coro)


def create_with_gather(coro: Coroutine[Any, Any, Any]) -> "asyncio.Future[Any]":
    gathering = asyncio.gather(coro)
    (child,) = gathering._children  # type: ignore[attr-defined]
    return child


@pytest.mark.parametrize(
    "create",
    [create_with_loop_create_task, create_with_asyncio_create_task, create_with_ensure_future, create_with_gather],
)
def test_child_task_inherits_the_creators_span_without_activating_anything(
    collector: StackCollector,
    tracer: Tracer,
    asyncio_integration: bool,
    create: Callable[[Coroutine[Any, Any, Any]], "asyncio.Future[Any]"],
) -> None:
    async def main() -> None:
        with tracer.trace("request") as span:
            child = create(own_key())
            # The creation hook wrote the key before the child ran a single step.
            assert task_entry(child) == span_entry(span)
            assert await child == span_entry(span)

    run(main())


@pytest.mark.skipif(sys.version_info < (3, 11), reason="asyncio.TaskGroup was added in Python 3.11")
def test_task_group_child_inherits_the_creators_span(
    collector: StackCollector, tracer: Tracer, asyncio_integration: bool
) -> None:
    async def main() -> None:
        with tracer.trace("request") as span:
            async with asyncio.TaskGroup() as group:  # type: ignore[attr-defined]
                child = group.create_task(own_key())
                assert task_entry(child) == span_entry(span)
            assert child.result() == span_entry(span)

    run(main())


def test_untraced_task_gets_no_key(collector: StackCollector, tracer: Tracer) -> None:
    async def main() -> None:
        child = asyncio.create_task(own_key())
        assert task_key(child) is None
        assert await child is None
        assert task_key(child) is None

    run(main())


@requires_task_context
def test_task_with_an_explicit_empty_context(
    collector: StackCollector, tracer: Tracer, asyncio_integration: bool
) -> None:
    async def main() -> None:
        with tracer.trace("request") as span:
            child = asyncio.get_running_loop().create_task(own_key(), context=contextvars.Context())
            # The empty Context has no span, so creation writes nothing.
            assert task_key(child) is None
            entry = await child
        if asyncio_integration:
            # The integration activates the creator's trace context in the child, which publishes it as a Context.
            assert entry is not None
            assert (entry[0], entry[3]) == (span.span_id, CONTEXT)
        else:
            assert entry is None

    run(main())


@requires_eager_tasks
def test_eager_task_with_an_explicit_empty_context(
    collector: StackCollector, tracer: Tracer, asyncio_integration: bool
) -> None:
    async def main() -> None:
        loop = asyncio.get_running_loop()
        loop.set_task_factory(asyncio.eager_task_factory)  # type: ignore[attr-defined]
        with tracer.trace("request") as span:
            child = loop.create_task(own_key(), context=contextvars.Context())
            # The first step ran, and completed the task, inside create_task.
            assert child.done()
            entry = child.result()
        if asyncio_integration:
            assert entry is not None
            assert (entry[0], entry[3]) == (span.span_id, CONTEXT)
        else:
            assert entry is None

    run(main())


@pytest.mark.skipif(sys.version_info >= (3, 12), reason="from Python 3.12 the Context of a task can be read")
def test_custom_task_factory_before_312_seeds_no_key(collector: StackCollector, tracer: Tracer) -> None:
    # Before Python 3.12 there is no way to read which Context a custom factory gave the task.
    def factory(loop: asyncio.AbstractEventLoop, coro: Coroutine[Any, Any, Any], **kwargs: Any) -> "asyncio.Task[Any]":
        return asyncio.Task(coro, loop=loop, **kwargs)

    async def main() -> None:
        asyncio.get_running_loop().set_task_factory(factory)
        with tracer.trace("request"):
            child = asyncio.create_task(own_key())
            assert task_key(child) is None
            assert await child is None

    run(main())


@pytest.mark.skipif(
    sys.version_info < (3, 11) or sys.version_info >= (3, 12),
    reason="create_task(context=...) was added in Python 3.11, and from 3.12 the Context of a task can be read",
)
def test_custom_task_factory_before_312_with_an_explicit_empty_context(
    collector: StackCollector, tracer: Tracer, asyncio_integration: bool
) -> None:
    def factory(loop: asyncio.AbstractEventLoop, coro: Coroutine[Any, Any, Any], **kwargs: Any) -> "asyncio.Task[Any]":
        return asyncio.Task(coro, loop=loop, **kwargs)

    async def main() -> None:
        loop = asyncio.get_running_loop()
        loop.set_task_factory(factory)
        with tracer.trace("request") as span:
            child = loop.create_task(own_key(), context=contextvars.Context())
            # The creation hook skips tasks from a custom factory before 3.12, and the empty Context has no span.
            assert task_key(child) is None
            entry = await child
        if asyncio_integration:
            # The integration activates the creator's trace context in the child, which publishes it as a Context.
            assert entry is not None
            assert (entry[0], entry[3]) == (span.span_id, CONTEXT)
        else:
            assert entry is None

    run(main())


@pytest.mark.skipif(sys.version_info < (3, 12), reason="from Python 3.12 the Context of a task can be read")
def test_custom_task_factory_seeds_the_tasks_own_context(collector: StackCollector, tracer: Tracer) -> None:
    def empty_context_factory(
        loop: asyncio.AbstractEventLoop, coro: Coroutine[Any, Any, Any], **kwargs: Any
    ) -> "asyncio.Task[Any]":
        # The factory ignores the caller's Context and runs every task in an empty one.
        kwargs["context"] = contextvars.Context()
        return asyncio.Task(coro, loop=loop, **kwargs)

    def keeping_factory(
        loop: asyncio.AbstractEventLoop, coro: Coroutine[Any, Any, Any], **kwargs: Any
    ) -> "asyncio.Task[Any]":
        return asyncio.Task(coro, loop=loop, **kwargs)

    async def main() -> None:
        loop = asyncio.get_running_loop()
        with tracer.trace("request") as span:
            loop.set_task_factory(keeping_factory)
            inherits = asyncio.create_task(own_key())
            assert task_entry(inherits) == span_entry(span)
            loop.set_task_factory(empty_context_factory)
            empty = asyncio.create_task(own_key())
            assert task_key(empty) is None
            assert await asyncio.gather(inherits, empty) == [span_entry(span), None]

    run(main())


def test_inherited_span_finished_at_creation_seeds_its_unfinished_parent(
    collector: StackCollector, tracer: Tracer, asyncio_integration: bool
) -> None:
    async def main() -> None:
        with tracer.trace("parent") as parent:
            child_span = tracer.start_span("child", child_of=parent, activate=True)
            # Finishing on another thread leaves the finished child span in this task's ContextVar.
            on_another_thread(child_span.finish)
            child = asyncio.create_task(own_key())
            assert task_entry(child) == span_entry(parent)
            assert await child == span_entry(parent)

    run(main())


def test_inherited_root_span_finished_before_the_first_step(
    collector: StackCollector, tracer: Tracer, asyncio_integration: bool
) -> None:
    async def main() -> None:
        root = tracer.trace("fire and forget")
        child = asyncio.create_task(own_key())
        assert task_entry(child) == span_entry(root)
        root.finish()
        entry = await child
        if asyncio_integration:
            # The integration activates the trace context it captured at creation, as a Context, which a finish does
            # not affect.
            assert entry is not None
            assert (entry[0], entry[3]) == (root.span_id, CONTEXT)
        else:
            # The root span had no parent to move to, so the key became a tombstone, which means no span.
            assert entry is None
            assert task_key(child) is not None

    run(main())


def test_activation_inside_a_task_writes_its_key(collector: StackCollector, tracer: Tracer) -> None:
    async def child() -> None:
        assert task_key(asyncio.current_task()) is None
        with tracer.trace("own") as span:
            assert task_entry(asyncio.current_task()) == span_entry(span)
        # The root span finished with no parent, so the key keeps a tombstone.
        found = task_key(asyncio.current_task())
        assert found is not None and found[0] is None

    run(child())


def test_activation_outside_a_task_writes_only_the_thread_key(collector: StackCollector, tracer: Tracer) -> None:
    task_ids: list[int] = []
    spans: list[Span] = []

    def callback() -> None:
        # A plain loop callback runs with no current task.
        task_ids.append(_asyncio.current_task_link_id())
        spans.append(tracer.start_span("callback", activate=True))

    async def main() -> None:
        loop = asyncio.get_running_loop()
        main_task = asyncio.current_task()
        assert task_key(main_task) is None
        handle_done = loop.create_future()
        loop.call_soon(lambda: (callback(), handle_done.set_result(None)))
        await handle_done
        (span,) = spans
        found = _stack._span_link_entry(THREAD, threading.get_ident())
        assert found is not None and found[0] == span_entry(span)
        # The task that ran before the callback, and runs after it, did not get the callback's span.
        assert task_key(main_task) is None
        span.finish()

    run(main())
    assert task_ids == [0]
    assert _asyncio.current_task_link_id() == 0


def test_long_lived_task_keeps_one_key_across_many_root_spans(collector: StackCollector, tracer: Tracer) -> None:
    async def main() -> None:
        task = asyncio.current_task()
        with tracer.trace("first"):
            pass
        found = task_key(task)
        assert found is not None
        birth = found[1]
        refs_before = set(_asyncio._task_refs)
        for _ in range(1000):
            with tracer.trace("request") as span:
                assert task_key(task) == (span_entry(span), birth)
            # Each root span leaves a tombstone, which keeps the key and its birth.
            assert task_key(task) == (None, birth)
        assert id(task) in _asyncio._task_refs
        # Other tasks may be collected meanwhile, but no task got a second cleanup hook.
        assert set(_asyncio._task_refs) <= refs_before

    run(main())


@pytest.mark.parametrize("writer", ["creation hook", "activation"])
def test_pending_task_key_is_erased_when_the_task_is_collected(
    collector: StackCollector, tracer: Tracer, writer: str
) -> None:
    spans: list[Span] = []

    async def traced_wait_forever(future: "asyncio.Future[None]") -> None:
        # A plain activation, with no with block, so that closing the dying coroutine does not run tracer code.
        spans.append(tracer.start_span("own", activate=True))
        await future

    async def main() -> None:
        loop = asyncio.get_running_loop()
        # Destroying a pending task reports "Task was destroyed but it is pending!" to the handler.
        loop.set_exception_handler(lambda _loop, _context: None)
        future = loop.create_future()
        if writer == "creation hook":
            with tracer.trace("request"):
                task = asyncio.create_task(wait_forever(future))
        else:
            task = asyncio.create_task(traced_wait_forever(future))
        await asyncio.sleep(0)
        task_id = id(task)
        assert _stack._span_link_entry(TASK, task_id) is not None
        assert task_id in _asyncio._task_refs
        # The task and its future now only reference each other, so only the garbage collector frees them.
        del task, future
        gc.collect()
        assert _stack._span_link_entry(TASK, task_id) is None
        assert task_id not in _asyncio._task_refs

    run(main())
    for span in spans:
        span.finish()


def test_no_task_key_while_profiling_is_stopped(collector: StackCollector, tracer: Tracer) -> None:
    async def main() -> None:
        collector.stop()
        with tracer.trace("request"):
            child = asyncio.create_task(own_key())
            assert task_key(child) is None
            assert await child is None
        assert id(child) not in _asyncio._task_refs

    run(main())


def test_no_inherited_key_when_the_provider_has_its_own_active_rule(
    collector: StackCollector, tracer: Tracer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tracer, "context_provider", _OwnActiveRuleProvider())

    async def child() -> "_stack.SpanLinkEntry":
        with tracer.trace("own") as span:
            # Activations inside the task are still published.
            assert task_entry(asyncio.current_task()) == span_entry(span)
        return None

    async def main() -> None:
        with tracer.trace("request"):
            inherits = asyncio.create_task(own_key())
            assert task_key(inherits) is None
            assert await inherits is None
            await asyncio.create_task(child())

    run(main())


def test_create_task_works_when_the_provider_check_raises(
    collector: StackCollector, tracer: Tracer, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_provider_check() -> object:
        raise RuntimeError("provider check failed")

    monkeypatch.setattr(_asyncio, "_inheriting_provider", broken_provider_check)

    async def main() -> None:
        with tracer.trace("request"):
            child = asyncio.create_task(own_key())
            assert task_key(child) is None
            assert await child is None

    run(main())


@pytest.mark.skipif(sys.version_info < (3, 12), reason="before 3.12 tasks from a custom factory are not seeded")
def test_provider_swapped_during_create_task_decides_the_seed(
    collector: StackCollector, tracer: Tracer, monkeypatch: pytest.MonkeyPatch
) -> None:
    def swapping_factory(
        loop: asyncio.AbstractEventLoop, coro: Coroutine[Any, Any, Any], **kwargs: Any
    ) -> "asyncio.Task[Any]":
        # The provider changes to one whose active() rule the profiler cannot follow while create_task runs.
        monkeypatch.setattr(tracer, "context_provider", _OwnActiveRuleProvider())
        return asyncio.Task(coro, loop=loop, **kwargs)

    async def main() -> None:
        loop = asyncio.get_running_loop()
        with tracer.trace("request"):
            loop.set_task_factory(swapping_factory)
            child = loop.create_task(own_key())
            loop.set_task_factory(None)
            # The seed checks the provider after create_task, so it does not write an inherited key.
            assert task_key(child) is None
            assert await child is None

    run(main())


def test_span_finishing_before_the_seed_write_lands_moves_the_key_to_the_parent(
    collector: StackCollector, tracer: Tracer, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_link = stack_module.link

    async def main() -> None:
        with tracer.trace("parent") as parent:
            child_span = tracer.start_span("child", child_of=parent, activate=True)
            # The creation hook swallows exceptions, so record what the race saw and check it afterwards.
            raced: list[tuple[int, bool]] = []

            def finish_then_link(token: int, thread: bool, task_id: int, greenlet_id: int, entry: Any) -> None:
                if not thread and not raced:
                    on_another_thread(child_span.finish)
                    raced.append((entry[0], stack_module.holds_span(child_span.span_id)))
                real_link(token, thread, task_id, greenlet_id, entry)

            monkeypatch.setattr(stack_module, "link", finish_then_link)
            task = asyncio.create_task(own_key())
            monkeypatch.undo()
            # The seed wrote the child span, which finished before the write. The finish listener had no task key to
            # move then, and it moved the other keys off the child span.
            assert raced == [(child_span.span_id, False)]
            # The seed re-checked the span after the write, and moved the key to the unfinished parent.
            assert task_entry(task) == span_entry(parent)
            assert not stack_module.holds_span(child_span.span_id)
            assert await task == span_entry(parent)

    run(main())


class _CallableFactory:
    def __call__(self, loop: asyncio.AbstractEventLoop, coro: Coroutine[Any, Any, Any]) -> "asyncio.Task[Any]":
        return asyncio.Task(coro, loop=loop)


def _passthrough(f: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    return f(*args, **kwargs)


def _other_passthrough(f: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    return f(*args, **kwargs)


def test_is_eager_task_factory_is_false_for_non_eager_factories() -> None:
    def plain_factory(loop: asyncio.AbstractEventLoop, coro: Coroutine[Any, Any, Any]) -> "asyncio.Task[Any]":
        return asyncio.Task(coro, loop=loop)

    for factory in (None, asyncio.Task, functools.partial(plain_factory), _CallableFactory(), plain_factory, 42):
        assert _asyncio.is_eager_task_factory(factory) is False


@requires_eager_tasks
def test_is_eager_task_factory_is_false_for_a_partial_of_the_eager_factory() -> None:
    # A partial is not a function, so it is not recognized, even though it starts tasks eagerly.
    assert _asyncio.is_eager_task_factory(functools.partial(asyncio.eager_task_factory)) is False  # type: ignore[attr-defined]


@requires_eager_tasks
def test_is_eager_task_factory_finds_every_eager_factory_through_in_place_wrappers() -> None:
    singleton = asyncio.eager_task_factory  # type: ignore[attr-defined]
    product = asyncio.create_eager_task_factory(asyncio.Task)  # type: ignore[attr-defined]
    assert _asyncio.is_eager_task_factory(singleton)
    assert _asyncio.is_eager_task_factory(product)

    wrap(product, _passthrough)
    assert _asyncio.is_eager_task_factory(product)
    wrap(product, _other_passthrough)
    assert _asyncio.is_eager_task_factory(product)

    wrap(singleton, _passthrough)
    try:
        assert _asyncio.is_eager_task_factory(singleton)
        assert _asyncio.is_eager_task_factory(product)
        wrap(singleton, _other_passthrough)
        try:
            assert _asyncio.is_eager_task_factory(singleton)
        finally:
            unwrap(singleton, _other_passthrough)
    finally:
        unwrap(singleton, _passthrough)


def _eager_factories() -> list[Any]:
    if sys.version_info < (3, 12):
        return []
    return [
        pytest.param(asyncio.eager_task_factory, id="eager_task_factory"),  # type: ignore[attr-defined]
        pytest.param(asyncio.create_eager_task_factory(asyncio.Task), id="create_eager_task_factory"),  # type: ignore[attr-defined]
    ]


def _code_name(task: "asyncio.Task[Any]") -> str:
    coro: Any = task.get_coro()
    return coro.cr_code.co_name


@requires_eager_tasks
@pytest.mark.parametrize("factory", _eager_factories())
def test_eager_task_that_completes_in_its_first_step_has_the_inherited_key(
    collector: StackCollector, tracer: Tracer, factory: Any
) -> None:
    async def main() -> None:
        asyncio.get_running_loop().set_task_factory(factory)
        with tracer.trace("request") as span:
            child = asyncio.create_task(own_key())
            assert child.done()
            # The first step already saw the key, so it was written before the task's own code ran.
            assert child.result() == span_entry(span)
            assert task_entry(child) == span_entry(span)

    run(main())


@requires_eager_tasks
def test_eager_task_that_suspends_has_the_inherited_key(collector: StackCollector, tracer: Tracer) -> None:
    async def main() -> None:
        asyncio.get_running_loop().set_task_factory(asyncio.eager_task_factory)  # type: ignore[attr-defined]
        with tracer.trace("request") as span:
            child = asyncio.create_task(own_key_after_suspending())
            assert not child.done()
            assert _code_name(child) == "_eager_task_span_link"
            # The wrapper keeps the original coroutine's names.
            assert child.get_coro().__qualname__ == own_key_after_suspending.__qualname__  # type: ignore[union-attr]
            assert await child == span_entry(span)

    run(main())


@requires_eager_tasks
def test_non_eager_tasks_are_not_wrapped(collector: StackCollector, tracer: Tracer) -> None:
    async def main() -> None:
        with tracer.trace("request"):
            child = asyncio.create_task(own_key())
            assert _code_name(child) == "own_key"
            await child

    run(main())


@pytest.mark.skipif(sys.version_info < (3, 14), reason="create_task(eager_start=...) was added in Python 3.14")
def test_eager_start_argument_decides_the_wrapping(collector: StackCollector, tracer: Tracer) -> None:
    async def main() -> None:
        loop = asyncio.get_running_loop()
        with tracer.trace("request") as span:
            eager = loop.create_task(own_key(), eager_start=True)
            # The first step ran inside create_task.
            assert eager.done()
            assert eager.result() == span_entry(span)
            suspending = loop.create_task(own_key_after_suspending(), eager_start=True)
            assert _code_name(suspending) == "_eager_task_span_link"
            assert await suspending == span_entry(span)

            loop.set_task_factory(asyncio.eager_task_factory)  # type: ignore[attr-defined]
            # An explicit eager_start=False wins over the eager factory: the task is neither eager nor wrapped.
            lazy = loop.create_task(own_key(), eager_start=False)
            assert not lazy.done()
            assert _code_name(lazy) == "own_key"
            assert await lazy == span_entry(span)

    run(main())


@requires_eager_tasks
def test_eager_task_cancelled_before_its_first_step_closes_the_original_coroutine(
    collector: StackCollector, tracer: Tracer
) -> None:
    # On a loop that is not running, an eager task is scheduled like any other task, so it can be cancelled before
    # its first step.
    loop = asyncio.new_event_loop()
    try:
        loop.set_task_factory(asyncio.eager_task_factory)  # type: ignore[attr-defined]
        original = own_key()
        with tracer.trace("request"):
            task = loop.create_task(original)
        assert _code_name(task) == "_eager_task_span_link"
        assert original.cr_frame is not None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            loop.run_until_complete(asyncio.wait_for(task, LOOP_TIMEOUT_SECONDS))
        # Run the done callbacks, which close the original coroutine. Without the wrapper, the cancellation would have
        # closed it.
        loop.run_until_complete(asyncio.sleep(0))
        assert original.cr_frame is None
    finally:
        loop.close()


@requires_eager_tasks
def test_create_task_failure_leaves_the_original_coroutine_to_the_caller(
    collector: StackCollector, tracer: Tracer
) -> None:
    loop = asyncio.new_event_loop()
    loop.set_task_factory(asyncio.eager_task_factory)  # type: ignore[attr-defined]
    loop.close()
    original = own_key()
    with tracer.trace("request"):
        with pytest.raises(RuntimeError, match="closed"):
            loop.create_task(original)
    # As without the profiler, the caller still owns the coroutine, unstarted and not closed.
    assert original.cr_frame is not None
    original.close()


@requires_eager_tasks
def test_create_task_failure_after_the_factory_closed_the_coroutine_closes_the_original(
    collector: StackCollector, tracer: Tracer
) -> None:
    def closing_constructor(coro: Any, **kwargs: Any) -> "asyncio.Task[Any]":
        # Like a task class that rejects the coroutine it is given, and closes it.
        coro.close()
        raise ValueError("rejected")

    async def main() -> None:
        loop = asyncio.get_running_loop()
        # An eager factory product, so create_task wraps the coroutine.
        loop.set_task_factory(asyncio.create_eager_task_factory(closing_constructor))  # type: ignore[attr-defined]
        original = own_key()
        try:
            with tracer.trace("request"):
                with pytest.raises(ValueError, match="rejected"):
                    loop.create_task(original)
        finally:
            # asyncio.run creates tasks to shut down the loop.
            loop.set_task_factory(None)
        # The factory closed the wrapper, so the original is closed too, as the factory would have done to it.
        assert original.cr_frame is None

    run(main())


@pytest.mark.skipif(not uvloop_available(), reason="uvloop is not installed")
def test_uvloop_child_task_inherits_the_creators_span(collector: StackCollector, tracer: Tracer) -> None:
    import uvloop

    async def main() -> None:
        with tracer.trace("request") as span:
            child = asyncio.get_running_loop().create_task(own_key())
            assert task_entry(child) == span_entry(span)
            gathered = asyncio.gather(own_key())
            assert await asyncio.gather(child, gathered) == [span_entry(span), [span_entry(span)]]

    loop = uvloop.new_event_loop()
    try:
        assert isinstance(loop, uvloop.Loop)
        loop.run_until_complete(asyncio.wait_for(main(), LOOP_TIMEOUT_SECONDS))
    finally:
        loop.close()


@requires_eager_tasks
@pytest.mark.skipif(not uvloop_available(), reason="uvloop is not installed")
def test_uvloop_eager_task_has_the_inherited_key(collector: StackCollector, tracer: Tracer) -> None:
    import uvloop

    async def main() -> None:
        asyncio.get_running_loop().set_task_factory(asyncio.eager_task_factory)  # type: ignore[attr-defined]
        with tracer.trace("request") as span:
            child = asyncio.get_running_loop().create_task(own_key())
            assert child.done()
            assert child.result() == span_entry(span)
            suspending = asyncio.get_running_loop().create_task(own_key_after_suspending())
            assert _code_name(suspending) == "_eager_task_span_link"
            assert await suspending == span_entry(span)

    loop = uvloop.new_event_loop()
    try:
        loop.run_until_complete(asyncio.wait_for(main(), LOOP_TIMEOUT_SECONDS))
    finally:
        loop.close()
