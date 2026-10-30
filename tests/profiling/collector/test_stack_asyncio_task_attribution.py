"""End-to-end tests for the span labels of asyncio task samples.

The stack profiler labels each sample with the span it ran: the span id, the local root span id, and the trace
endpoint (the resource of the local root span, for web spans). A sample of an asyncio task carries the span linked to
that task's own key, or no span. It never carries the span of the thread the task ran on, which is whichever span any
task on the thread activated last. A task's key is written when the task activates a span, and when the task is
created, from the span it inherits from its creator (the tracer's active span in the task's Context).

Each test runs asyncio code under the profiler in a subprocess and decodes the profile. Most tests keep another task's
span active on the thread (OtherSpanOnTheThread) while the task under test works, so that a sample that fell back to
the thread's span would carry that other span. Unless a case depends on the Python version, the tests run with the
asyncio tracing integration unpatched and patched (ASYNCIO_INTEGRATION), because the integration changes which span
the tracer considers active in some tasks.

test_stack_asyncio_task_span_links.py checks the task keys themselves.
"""

import asyncio
import os
import sys
import threading
import time
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import ContextManager
from typing import NamedTuple
from typing import Optional

import pytest

from ddtrace import ext
from ddtrace.internal.datadog.profiling import stack
from ddtrace.profiling.profiler import Profiler
from ddtrace.trace import Span
from ddtrace.trace import Tracer
from tests.profiling.collector import pprof_utils
from tests.profiling.collector.test_utils import uvloop_available


if TYPE_CHECKING:
    from tests.profiling.collector.pprof_pb2 import Profile  # pyright: ignore[reportMissingModuleSource]


pytestmark = pytest.mark.skipif(not stack.is_available, reason="stack profiler not available")

# Each test runs its loop for a few seconds at most. The bound turns a hang into a failure.
SUBPROCESS_TIMEOUT_SECONDS = 60

# Adaptive sampling can stretch the sampling interval. Without it, every busy phase below gets many samples.
NO_ADAPTIVE_SAMPLING = {"_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0"}
ASYNCIO_INTEGRATION = {"ASYNCIO_INTEGRATION": ["off", "on"]}

# The labels of a sample: (span id, local root span id, trace endpoint). None means the label is missing.
Labels = tuple[Optional[int], Optional[int], Optional[str]]
NO_SPAN: Labels = (None, None, None)

OTHER_ENDPOINT = "other-endpoint"
FIRST_ENDPOINT = "first-endpoint"
SECOND_ENDPOINT = "second-endpoint"


def busy(seconds: float) -> None:
    """Run on the CPU for seconds of this thread's CPU time."""
    deadline = time.thread_time_ns() + int(seconds * 1_000_000_000)
    while time.thread_time_ns() < deadline:
        pass


def patch_asyncio_integration() -> bool:
    """Patch the asyncio tracing integration if ASYNCIO_INTEGRATION is "on". Return whether it is patched."""
    if os.environ.get("ASYNCIO_INTEGRATION") != "on":
        return False
    from ddtrace.contrib.internal.asyncio import patch as asyncio_patch

    asyncio_patch.patch()
    return True


def start_profiler(tracer: Tracer) -> Profiler:
    # The endpoint counter records the endpoint of each finished web root span. It is off unless enabled.
    tracer._endpoint_call_counter_span_processor.enable()
    p = Profiler(tracer=tracer)
    p.start()
    return p


def stop_profiler(p: Profiler) -> "Profile":
    p.stop()
    return pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))


def span_labels(span: Span, endpoint: Optional[str]) -> Labels:
    """Return the labels of a sample that ran span. endpoint is the resource of span's local root, if it is web."""
    return (
        pprof_utils.reinterpret_int_as_int64(span.span_id),
        pprof_utils.reinterpret_int_as_int64(span._local_root.span_id),
        endpoint,
    )


def labels_of(profile: "Profile", samples: list[Any]) -> set[Labels]:
    return {
        (
            pprof_utils.get_num_label(profile, s, "span id"),
            pprof_utils.get_num_label(profile, s, "local root span id"),
            pprof_utils.get_str_label(profile, s, "trace endpoint"),
        )
        for s in samples
    }


def sample_labels(
    profile: "Profile", function_name: str, task_id: Optional[int] = None, value_type: str = "wall-time"
) -> set[Labels]:
    """Return the labels of the samples whose stack contains function_name. Fails if there are none.

    With task_id, only the samples of the task with that id count.
    """
    samples = pprof_utils.get_samples_with_function(
        profile, pprof_utils.get_samples_with_value_type(profile, value_type), function_name
    )
    if task_id is not None:
        samples = [s for s in samples if pprof_utils.get_num_label(profile, s, "task id") == task_id]
    assert samples, "no %s samples in %s" % (value_type, function_name)
    return labels_of(profile, samples)


def task_sample_labels(profile: "Profile", task_id: int, value_type: str = "wall-time") -> set[Labels]:
    """Return the labels of all the samples of the task with task_id, whatever their frames. Fails if there are none."""
    samples = [
        s
        for s in pprof_utils.get_samples_with_value_type(profile, value_type)
        if pprof_utils.get_num_label(profile, s, "task id") == task_id
    ]
    assert samples, "no %s samples of task %d" % (value_type, task_id)
    return labels_of(profile, samples)


def wait_for_in_flight_samples() -> None:
    """Wait until the sampler has finished the sample it is taking, if any, then let it continue.

    The sampler does not stop the thread while it takes a sample. It copies the frames first and reads the task keys
    afterwards, so a sample taken while tasks or keys change can mix the states before and after the change:

    - it ignores a task key created after the sample started (see the birth of a key in
      test_stack_asyncio_task_span_links.py), so it has no span;
    - it can pair frames copied before a key changed with the key read after the change;
    - when a task is created, exits, or starts or stops waiting during the sample, Echion can render the frames on the
      thread under another task, with that task's span.

    Tests call this right before and right after the work whose samples they check. Every sample that shows the work
    then starts after the changes that come before the work, and ends before the changes that come after it.
    pause_sampling() returns only once no sample is in flight, so this does not depend on timing.

    A function checked while it is suspended is different. Its frames are on the thread only as it starts and stops
    waiting, and a sample taken then can show them under another task. So tests check only its own task's samples of
    it.
    """
    assert stack.pause_sampling() is True
    stack.resume_sampling()


def finish_on_another_thread(span: Span) -> None:
    """Finish span on another thread, so the active span stored in the caller's Context stays the finished span."""
    thread = threading.Thread(target=span.finish)
    thread.start()
    thread.join(10)
    assert not thread.is_alive()


class OtherSpanOnTheThread:
    """Keep another task's root span active on the thread while the async with block runs.

    The other task activates its span and then waits, so the thread's span link holds that span until the block
    exits. A task sample in the block that fell back to the thread's span would carry labels().
    """

    def __init__(self, tracer: Tracer) -> None:
        self.tracer = tracer
        self.span: Optional[Span] = None

    async def _run(self, started: asyncio.Event, stop: asyncio.Event) -> None:
        # A root span, so its local root and endpoint differ from those of every span of the code under test.
        self.span = self.tracer.start_span(
            "other.request", resource=OTHER_ENDPOINT, span_type=ext.SpanTypes.WEB, activate=True
        )
        try:
            started.set()
            await stop.wait()
        finally:
            self.span.finish()

    async def __aenter__(self) -> "OtherSpanOnTheThread":
        started = asyncio.Event()
        self._stop = asyncio.Event()
        self._task = asyncio.get_running_loop().create_task(self._run(started, self._stop))
        await started.wait()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self._stop.set()
        await self._task

    def labels(self) -> Labels:
        assert self.span is not None
        return span_labels(self.span, OTHER_ENDPOINT)


def first_task_work() -> None:
    busy(0.4)


async def second_task_wait() -> None:
    await asyncio.sleep(0.5)


class TracedTask(NamedTuple):
    span: Span
    task_id: int


async def run_two_concurrent_traced_tasks(
    tracer: Tracer, start_root_span: Callable[[str, str], ContextManager[Any]]
) -> tuple[TracedTask, TracedTask]:
    """Run two tasks on the running loop, each in its own root span, and return the two tasks.

    start_root_span(name, endpoint) enters a web root span and yields it. The first task is created with
    loop.create_task and the second one with asyncio.gather. Once both spans are active, another task activates its
    span on the thread. Then the second task starts waiting in second_task_wait, and the first task works in
    first_task_work while the second one still waits, so the samples of each function must carry their own task's
    span.
    """
    traced: dict[str, TracedTask] = {}
    first_active = asyncio.Event()
    second_active = asyncio.Event()
    go = asyncio.Event()

    async def first() -> None:
        with start_root_span("first.request", FIRST_ENDPOINT) as span:
            traced["first"] = TracedTask(span, id(asyncio.current_task()))
            first_active.set()
            await go.wait()
            # Let the second task start waiting in second_task_wait before this one works.
            await asyncio.sleep(0)
            wait_for_in_flight_samples()
            first_task_work()
            wait_for_in_flight_samples()

    async def second() -> None:
        with start_root_span("second.request", SECOND_ENDPOINT) as span:
            traced["second"] = TracedTask(span, id(asyncio.current_task()))
            # This activation creates the key of the second task. Wait, so no sample that started before shows
            # second_task_wait.
            wait_for_in_flight_samples()
            second_active.set()
            await go.wait()
            await second_task_wait()
            wait_for_in_flight_samples()

    first_task = asyncio.get_running_loop().create_task(first())
    second_tasks = asyncio.gather(second())
    await first_active.wait()
    await second_active.wait()
    async with OtherSpanOnTheThread(tracer):
        go.set()
        await asyncio.gather(first_task, second_tasks)
    return traced["first"], traced["second"]


def assert_two_concurrent_tasks_kept_their_own_spans(
    profile: "Profile", first: TracedTask, second: TracedTask, first_task_work_only_in_its_own_samples: bool = False
) -> None:
    """Check that the samples of each task of run_two_concurrent_traced_tasks carry only that task's span.

    Every sample of a task carries that task's span, or no span before the span starts and after it finishes. It never
    carries the other task's span or the thread's span. This check reads only labels, so frame rendering cannot break
    it.

    first_task_work runs on the CPU between waits for in-flight samples, so all its samples must carry the first
    task's span. With first_task_work_only_in_its_own_samples, only the first task's samples of it count.

    second_task_wait is checked while it is suspended. Its frames are on the thread only at the moments it starts and
    stops waiting. A sample taken then can show them under another task, because Echion can pair the thread's frames
    with the wrong task (the TODO in unwind_tasks: the task known to be on CPU may not match the task that was on CPU
    when the thread stack was captured). So only the second task's own samples of it count.
    """
    for task, endpoint in ((first, FIRST_ENDPOINT), (second, SECOND_ENDPOINT)):
        labels = task_sample_labels(profile, task.task_id)
        assert labels <= {span_labels(task.span, endpoint), NO_SPAN}, (endpoint, labels)

    first_task_id = first.task_id if first_task_work_only_in_its_own_samples else None
    labels = sample_labels(profile, "first_task_work", task_id=first_task_id)
    assert labels == {span_labels(first.span, FIRST_ENDPOINT)}, ("first_task_work", labels)
    labels = sample_labels(profile, "second_task_wait", task_id=second.task_id)
    assert labels == {span_labels(second.span, SECOND_ENDPOINT)}, ("second_task_wait", labels)


@pytest.mark.subprocess(
    env={"DD_PROFILING_OUTPUT_PPROF": "/tmp/test_child_task_carries_the_creators_span", **NO_ADAPTIVE_SAMPLING},
    parametrize={**ASYNCIO_INTEGRATION, "CREATE": ["create_task", "gather"]},
    err=None,
    timeout=SUBPROCESS_TIMEOUT_SECONDS,
)
def test_child_task_carries_the_creators_span_without_activating_anything() -> None:
    import asyncio
    import os

    from ddtrace import ext
    from ddtrace.trace import Span
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_stack_asyncio_task_attribution import OtherSpanOnTheThread
    from tests.profiling.collector.test_stack_asyncio_task_attribution import busy
    from tests.profiling.collector.test_stack_asyncio_task_attribution import patch_asyncio_integration
    from tests.profiling.collector.test_stack_asyncio_task_attribution import sample_labels
    from tests.profiling.collector.test_stack_asyncio_task_attribution import span_labels
    from tests.profiling.collector.test_stack_asyncio_task_attribution import start_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import stop_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import wait_for_in_flight_samples

    patch_asyncio_integration()

    def child_work() -> None:
        busy(0.3)

    async def child() -> None:
        wait_for_in_flight_samples()
        child_work()
        wait_for_in_flight_samples()

    async def main() -> tuple[Span, OtherSpanOnTheThread]:
        with tracer.trace("parent.request", resource="parent-endpoint", span_type=ext.SpanTypes.WEB) as parent:
            async with OtherSpanOnTheThread(tracer) as other:
                if os.environ["CREATE"] == "gather":
                    await asyncio.gather(child())
                else:
                    await asyncio.create_task(child())
        return parent, other

    p = start_profiler(tracer)
    parent, other = asyncio.run(main())
    profile = stop_profiler(p)

    assert sample_labels(profile, "child_work") == {span_labels(parent, "parent-endpoint")}
    # The thread's span at that time was the other task's span.
    assert sample_labels(profile, "child_work") != {other.labels()}


@pytest.mark.skipif(sys.version_info < (3, 11), reason="create_task(context=...) was added in Python 3.11")
@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_explicit_empty_context_with_the_asyncio_integration",
        "ASYNCIO_INTEGRATION": "on",
        **NO_ADAPTIVE_SAMPLING,
    },
    # Before 3.12 a custom task factory receives the Context, and from 3.12 an eager task runs its first step in it.
    parametrize={"TASK": ["plain", "eager" if sys.version_info >= (3, 12) else "custom_factory"]},
    err=None,
    timeout=SUBPROCESS_TIMEOUT_SECONDS,
)
def test_task_with_an_explicit_empty_context_carries_the_creators_span_with_the_asyncio_integration() -> None:
    """The integration activates the creator's span in the task, so the task's own key gets it.

    test_stack_asyncio_endpoint_labels.py checks that without the integration the task gets no span.
    """
    import asyncio
    import contextvars
    import os
    from typing import Any

    from ddtrace import ext
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_stack_asyncio_task_attribution import OtherSpanOnTheThread
    from tests.profiling.collector.test_stack_asyncio_task_attribution import busy
    from tests.profiling.collector.test_stack_asyncio_task_attribution import patch_asyncio_integration
    from tests.profiling.collector.test_stack_asyncio_task_attribution import sample_labels
    from tests.profiling.collector.test_stack_asyncio_task_attribution import span_labels
    from tests.profiling.collector.test_stack_asyncio_task_attribution import start_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import stop_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import wait_for_in_flight_samples

    assert patch_asyncio_integration()
    kind = os.environ["TASK"]
    child_ids: list[int] = []

    def empty_context_work() -> None:
        busy(0.3)

    async def child() -> None:
        child_ids.append(id(asyncio.current_task()))
        wait_for_in_flight_samples()
        empty_context_work()
        wait_for_in_flight_samples()

    def custom_factory(loop: asyncio.AbstractEventLoop, coro: Any, **kwargs: Any) -> "asyncio.Task[Any]":
        return asyncio.Task(coro, loop=loop, **kwargs)

    async def main() -> Any:
        loop = asyncio.get_running_loop()
        with tracer.trace("parent.request", resource="parent-endpoint", span_type=ext.SpanTypes.WEB) as parent:
            async with OtherSpanOnTheThread(tracer):
                if kind == "eager":
                    loop.set_task_factory(asyncio.eager_task_factory)  # type: ignore[attr-defined]
                elif kind == "custom_factory":
                    loop.set_task_factory(custom_factory)
                try:
                    task = loop.create_task(child(), context=contextvars.Context())  # type: ignore[call-arg]
                finally:
                    loop.set_task_factory(None)
                await task
        return parent

    p = start_profiler(tracer)
    parent = asyncio.run(main())
    profile = stop_profiler(p)

    assert sample_labels(profile, "empty_context_work", child_ids[0]) == {span_labels(parent, "parent-endpoint")}


# eager_task_factory and create_eager_task_factory were added in 3.12, and create_task(eager_start=True) in 3.14.
EAGER_STARTS = ["eager_task_factory", "create_eager_task_factory"]
if sys.version_info >= (3, 14):
    EAGER_STARTS.append("eager_start")


@pytest.mark.skipif(sys.version_info < (3, 12), reason="eager tasks were added in Python 3.12")
@pytest.mark.subprocess(
    env={"DD_PROFILING_OUTPUT_PPROF": "/tmp/test_eager_task_carries_the_inherited_span", **NO_ADAPTIVE_SAMPLING},
    # finishes_eagerly: the child completes inside create_task and is never scheduled.
    # suspends: the child also runs a normal step after its eager first step.
    parametrize={**ASYNCIO_INTEGRATION, "EAGER_START": EAGER_STARTS, "CHILD": ["finishes_eagerly", "suspends"]},
    err=None,
    timeout=SUBPROCESS_TIMEOUT_SECONDS,
)
def test_eager_task_carries_the_inherited_span_from_its_first_step() -> None:
    """An eager task is a task whose first step runs inside create_task, before create_task returns.

    Its samples carry the span it inherited, in the first step and after it resumes. With the integration patched,
    the integration also wraps the eager factory in place to publish context switches (forced here, because on 3.14
    the native context watcher makes that unneeded).
    """
    import asyncio
    import os
    from typing import Any

    from ddtrace import ext
    from ddtrace.trace import Span
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_stack_asyncio_task_attribution import OtherSpanOnTheThread
    from tests.profiling.collector.test_stack_asyncio_task_attribution import busy
    from tests.profiling.collector.test_stack_asyncio_task_attribution import sample_labels
    from tests.profiling.collector.test_stack_asyncio_task_attribution import span_labels
    from tests.profiling.collector.test_stack_asyncio_task_attribution import start_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import stop_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import wait_for_in_flight_samples

    if os.environ["ASYNCIO_INTEGRATION"] == "on":
        from ddtrace.contrib.internal.asyncio import _context_switch
        from ddtrace.contrib.internal.asyncio import patch as asyncio_patch

        _context_switch.context_switches_require_fallback = lambda: True
        asyncio_patch.patch()
        assert _context_switch._installed

    eager_start = os.environ["EAGER_START"]
    suspend = os.environ["CHILD"] == "suspends"
    child_ids: list[int] = []

    # Separate names so each step is checked on its own samples.
    def eager_first_step_work() -> None:
        busy(0.3)

    def resumed_step_work() -> None:
        busy(0.3)

    async def child(resume: asyncio.Event) -> None:
        child_ids.append(id(asyncio.current_task()))
        wait_for_in_flight_samples()
        eager_first_step_work()
        wait_for_in_flight_samples()
        if suspend:
            await resume.wait()
            wait_for_in_flight_samples()
            resumed_step_work()
            wait_for_in_flight_samples()

    async def main() -> Span:
        loop = asyncio.get_running_loop()
        resume = asyncio.Event()
        with tracer.trace("eager.parent", resource="eager-endpoint", span_type=ext.SpanTypes.WEB) as parent:
            kwargs: dict[str, Any] = {}
            if eager_start == "eager_task_factory":
                loop.set_task_factory(asyncio.eager_task_factory)  # type: ignore[attr-defined]
            elif eager_start == "create_eager_task_factory":
                loop.set_task_factory(asyncio.create_eager_task_factory(asyncio.Task))  # type: ignore[attr-defined]
            else:
                kwargs["eager_start"] = True
            try:
                task = loop.create_task(child(resume), **kwargs)
            finally:
                loop.set_task_factory(None)
            assert task.done() is not suspend
            # While the child is suspended, another task activates its span on the thread. The child's resumed step
            # still carries the span it inherited.
            async with OtherSpanOnTheThread(tracer):
                resume.set()
                await task
        return parent

    p = start_profiler(tracer)
    parent = asyncio.run(main())
    profile = stop_profiler(p)

    expected = {span_labels(parent, "eager-endpoint")}
    assert sample_labels(profile, "eager_first_step_work", child_ids[0]) == expected
    if suspend:
        assert sample_labels(profile, "resumed_step_work", child_ids[0]) == expected


@pytest.mark.subprocess(
    env={"DD_PROFILING_OUTPUT_PPROF": "/tmp/test_inherited_finished_span", **NO_ADAPTIVE_SAMPLING},
    # The OTel thread context adds activation events on context switches. Labels must not depend on it.
    parametrize={**ASYNCIO_INTEGRATION, "DD_TRACE_OTEL_CTX_ENABLED": ["true", "false"]},
    err=None,
    timeout=SUBPROCESS_TIMEOUT_SECONDS,
)
def test_task_inheriting_a_finished_span_carries_its_unfinished_parent() -> None:
    """The creator's active span finished before the task was created, but its parent is still running.

    The tracer gives the task the unfinished parent as its active span, and so do its samples.
    """
    import asyncio

    from ddtrace import ext
    from ddtrace.trace import Span
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_stack_asyncio_task_attribution import OtherSpanOnTheThread
    from tests.profiling.collector.test_stack_asyncio_task_attribution import busy
    from tests.profiling.collector.test_stack_asyncio_task_attribution import finish_on_another_thread
    from tests.profiling.collector.test_stack_asyncio_task_attribution import patch_asyncio_integration
    from tests.profiling.collector.test_stack_asyncio_task_attribution import sample_labels
    from tests.profiling.collector.test_stack_asyncio_task_attribution import span_labels
    from tests.profiling.collector.test_stack_asyncio_task_attribution import start_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import stop_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import wait_for_in_flight_samples

    patch_asyncio_integration()

    def child_work() -> None:
        busy(0.3)

    async def child() -> None:
        wait_for_in_flight_samples()
        child_work()
        wait_for_in_flight_samples()

    async def main() -> Span:
        with tracer.trace("parent.request", resource="parent-endpoint", span_type=ext.SpanTypes.WEB) as parent:
            finished = tracer.start_span("finished.child", child_of=parent, activate=True)
            finish_on_another_thread(finished)
            async with OtherSpanOnTheThread(tracer):
                await asyncio.create_task(child())
        return parent

    p = start_profiler(tracer)
    parent = asyncio.run(main())
    profile = stop_profiler(p)

    assert sample_labels(profile, "child_work") == {span_labels(parent, "parent-endpoint")}


@pytest.mark.subprocess(
    env={"DD_PROFILING_OUTPUT_PPROF": "/tmp/test_inherited_root_finished_before_first_step", **NO_ADAPTIVE_SAMPLING},
    parametrize={**ASYNCIO_INTEGRATION, "DD_TRACE_OTEL_CTX_ENABLED": ["true", "false"]},
    err=None,
    timeout=SUBPROCESS_TIMEOUT_SECONDS,
)
def test_fire_and_forget_task_whose_root_span_finished_before_its_first_step() -> None:
    """A fire-and-forget task inherits a root span that finishes before the task runs its first step.

    Without the integration, the root span has no unfinished parent, so the task has no span. With the integration,
    the task activates its creator's trace context (the root span's ids) in its first step, so it carries the root
    span.
    """
    import asyncio

    from ddtrace import ext
    from ddtrace.trace import Span
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_stack_asyncio_task_attribution import NO_SPAN
    from tests.profiling.collector.test_stack_asyncio_task_attribution import OtherSpanOnTheThread
    from tests.profiling.collector.test_stack_asyncio_task_attribution import busy
    from tests.profiling.collector.test_stack_asyncio_task_attribution import patch_asyncio_integration
    from tests.profiling.collector.test_stack_asyncio_task_attribution import sample_labels
    from tests.profiling.collector.test_stack_asyncio_task_attribution import span_labels
    from tests.profiling.collector.test_stack_asyncio_task_attribution import start_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import stop_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import wait_for_in_flight_samples

    integration = patch_asyncio_integration()

    def fire_and_forget_work() -> None:
        busy(0.3)

    async def fire_and_forget(go: asyncio.Event) -> None:
        await go.wait()
        wait_for_in_flight_samples()
        fire_and_forget_work()
        wait_for_in_flight_samples()

    async def main() -> Span:
        go = asyncio.Event()
        root = tracer.trace("fire.and.forget", resource="fire-and-forget-endpoint", span_type=ext.SpanTypes.WEB)
        task = asyncio.create_task(fire_and_forget(go))
        root.finish()
        # The task works only once the other task's span is active on the thread.
        async with OtherSpanOnTheThread(tracer):
            go.set()
            await task
        return root

    p = start_profiler(tracer)
    root = asyncio.run(main())
    profile = stop_profiler(p)

    expected = span_labels(root, "fire-and-forget-endpoint") if integration else NO_SPAN
    assert sample_labels(profile, "fire_and_forget_work") == {expected}


@pytest.mark.subprocess(
    env={"DD_PROFILING_OUTPUT_PPROF": "/tmp/test_task_label_moves_to_the_parent", **NO_ADAPTIVE_SAMPLING},
    parametrize={**ASYNCIO_INTEGRATION, "DD_TRACE_OTEL_CTX_ENABLED": ["true", "false"]},
    err=None,
    timeout=SUBPROCESS_TIMEOUT_SECONDS,
)
def test_task_label_moves_to_the_parent_when_its_inherited_span_finishes() -> None:
    """The task never touches the tracer. Its inherited span, then that span's parent, finish while it keeps running.

    The tracer's active span in the task moves to the parent, then to no span, and so do the task's samples.
    """
    import asyncio

    from ddtrace import ext
    from ddtrace.trace import Span
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_stack_asyncio_task_attribution import NO_SPAN
    from tests.profiling.collector.test_stack_asyncio_task_attribution import OtherSpanOnTheThread
    from tests.profiling.collector.test_stack_asyncio_task_attribution import busy
    from tests.profiling.collector.test_stack_asyncio_task_attribution import finish_on_another_thread
    from tests.profiling.collector.test_stack_asyncio_task_attribution import patch_asyncio_integration
    from tests.profiling.collector.test_stack_asyncio_task_attribution import sample_labels
    from tests.profiling.collector.test_stack_asyncio_task_attribution import span_labels
    from tests.profiling.collector.test_stack_asyncio_task_attribution import start_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import stop_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import wait_for_in_flight_samples

    patch_asyncio_integration()

    def while_the_inherited_span_runs() -> None:
        busy(0.3)

    def after_the_inherited_span_finished() -> None:
        busy(0.3)

    def after_the_parent_finished() -> None:
        busy(0.3)

    phases = [while_the_inherited_span_runs, after_the_inherited_span_finished, after_the_parent_finished]

    async def child(go: "asyncio.Queue[None]", done: "asyncio.Queue[None]") -> None:
        for phase in phases:
            await go.get()
            wait_for_in_flight_samples()
            phase()
            wait_for_in_flight_samples()
            await done.put(None)

    async def main() -> tuple[Span, Span]:
        go: asyncio.Queue[None] = asyncio.Queue()
        done: asyncio.Queue[None] = asyncio.Queue()
        parent = tracer.trace("parent.request", resource="parent-endpoint", span_type=ext.SpanTypes.WEB)
        inherited = tracer.trace("inherited")
        async with OtherSpanOnTheThread(tracer):
            task = asyncio.create_task(child(go, done))
            await go.put(None)
            await done.get()
            # Finishing a span activates its parent in the finishing Context. On another thread, that activation
            # does not replace the other task's span on this thread.
            finish_on_another_thread(inherited)
            await go.put(None)
            await done.get()
            finish_on_another_thread(parent)
            await go.put(None)
            await task
        return parent, inherited

    p = start_profiler(tracer)
    parent, inherited = asyncio.run(main())
    profile = stop_profiler(p)

    assert sample_labels(profile, "while_the_inherited_span_runs") == {span_labels(inherited, "parent-endpoint")}
    assert sample_labels(profile, "after_the_inherited_span_finished") == {span_labels(parent, "parent-endpoint")}
    assert sample_labels(profile, "after_the_parent_finished") == {NO_SPAN}


@pytest.mark.subprocess(
    env={"DD_PROFILING_OUTPUT_PPROF": "/tmp/test_newer_activation_survives", **NO_ADAPTIVE_SAMPLING},
    parametrize=ASYNCIO_INTEGRATION,
    err=None,
    timeout=SUBPROCESS_TIMEOUT_SECONDS,
)
def test_newer_activation_in_a_task_survives_the_finish_of_its_earlier_span() -> None:
    """The task activates a span, then a newer one. The earlier span finishing must not change the task's label."""
    import asyncio

    from ddtrace import ext
    from ddtrace.trace import Span
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_stack_asyncio_task_attribution import OtherSpanOnTheThread
    from tests.profiling.collector.test_stack_asyncio_task_attribution import busy
    from tests.profiling.collector.test_stack_asyncio_task_attribution import finish_on_another_thread
    from tests.profiling.collector.test_stack_asyncio_task_attribution import patch_asyncio_integration
    from tests.profiling.collector.test_stack_asyncio_task_attribution import sample_labels
    from tests.profiling.collector.test_stack_asyncio_task_attribution import span_labels
    from tests.profiling.collector.test_stack_asyncio_task_attribution import start_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import stop_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import wait_for_in_flight_samples

    patch_asyncio_integration()

    def after_the_earlier_span_finished() -> None:
        busy(0.3)

    async def child() -> Span:
        earlier = tracer.start_span("earlier.request", activate=True)
        newer = tracer.start_span(
            "newer.request", resource="newer-endpoint", span_type=ext.SpanTypes.WEB, activate=True
        )
        finish_on_another_thread(earlier)
        wait_for_in_flight_samples()
        after_the_earlier_span_finished()
        wait_for_in_flight_samples()
        newer.finish()
        return newer

    async def main() -> Span:
        async with OtherSpanOnTheThread(tracer):
            return await asyncio.create_task(child())

    p = start_profiler(tracer)
    newer = asyncio.run(main())
    profile = stop_profiler(p)

    assert sample_labels(profile, "after_the_earlier_span_finished") == {span_labels(newer, "newer-endpoint")}


@pytest.mark.skipif(not uvloop_available(), reason="uvloop not available")
@pytest.mark.subprocess(
    env={"DD_PROFILING_OUTPUT_PPROF": "/tmp/test_uvloop_concurrent_traced_tasks", **NO_ADAPTIVE_SAMPLING},
    # uvloop.run() needs asyncio.Runner, which was added in 3.11.
    parametrize={"RUNNER": ["install", "uvloop.run"] if sys.version_info >= (3, 11) else ["install"]},
    err=None,
    timeout=SUBPROCESS_TIMEOUT_SECONDS,
)
def test_uvloop_concurrent_traced_tasks_keep_their_own_spans() -> None:
    import asyncio
    import os
    import sys

    import uvloop

    from ddtrace import ext
    from ddtrace.trace import Span
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_stack_asyncio_task_attribution import TracedTask
    from tests.profiling.collector.test_stack_asyncio_task_attribution import (
        assert_two_concurrent_tasks_kept_their_own_spans,
    )
    from tests.profiling.collector.test_stack_asyncio_task_attribution import run_two_concurrent_traced_tasks
    from tests.profiling.collector.test_stack_asyncio_task_attribution import start_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import stop_profiler

    def start_root_span(name: str, endpoint: str) -> Span:
        return tracer.trace(name, resource=endpoint, span_type=ext.SpanTypes.WEB)

    async def main() -> tuple[TracedTask, TracedTask]:
        assert isinstance(asyncio.get_running_loop(), uvloop.Loop)
        return await run_two_concurrent_traced_tasks(tracer, start_root_span)

    p = start_profiler(tracer)
    if os.environ["RUNNER"] == "uvloop.run":
        first, second = uvloop.run(main())
    else:
        uvloop.install()
        first, second = asyncio.run(main())
    profile = stop_profiler(p)

    # Before 3.11, Echion recognizes the boundary between the loop's frames and the task frames on a uvloop thread
    # only as uvloop.run() (find_upper_python_stack_size in threads.cc). Under asyncio.run() it does not find it, so
    # it appends the whole thread stack to every task, and first_task_work also shows in the other tasks' samples,
    # each with that task's own span. That is a rendering gap that predates per-task spans, so only each task's own
    # samples are checked there.
    rendering_appends_the_thread_stack_to_every_task = sys.version_info < (3, 11) and os.environ["RUNNER"] == "install"
    assert_two_concurrent_tasks_kept_their_own_spans(
        profile, first, second, first_task_work_only_in_its_own_samples=rendering_appends_the_thread_stack_to_every_task
    )


@pytest.mark.skipif(sys.version_info < (3, 11), reason="asyncio.Runner was added in Python 3.11")
@pytest.mark.subprocess(
    env={"DD_PROFILING_OUTPUT_PPROF": "/tmp/test_runner_loop_factory_traced_tasks", **NO_ADAPTIVE_SAMPLING},
    err=None,
    timeout=SUBPROCESS_TIMEOUT_SECONDS,
)
def test_runner_loop_factory_concurrent_traced_tasks_keep_their_own_spans() -> None:
    """With a loop_factory, asyncio.Runner never calls set_event_loop. Its loop is only in the running-loop slot."""
    import asyncio
    import threading

    from ddtrace import ext
    from ddtrace.trace import Span
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_asyncio_running_loop import asyncio_loop_slots
    from tests.profiling.collector.test_stack_asyncio_task_attribution import TracedTask
    from tests.profiling.collector.test_stack_asyncio_task_attribution import (
        assert_two_concurrent_tasks_kept_their_own_spans,
    )
    from tests.profiling.collector.test_stack_asyncio_task_attribution import run_two_concurrent_traced_tasks
    from tests.profiling.collector.test_stack_asyncio_task_attribution import start_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import stop_profiler

    def start_root_span(name: str, endpoint: str) -> Span:
        return tracer.trace(name, resource=endpoint, span_type=ext.SpanTypes.WEB)

    async def main() -> tuple[TracedTask, TracedTask]:
        slots = asyncio_loop_slots(threading.get_ident())
        assert slots["configured_loop"] == 0, slots
        assert slots["running_loop"] == id(asyncio.get_running_loop()), slots
        return await run_two_concurrent_traced_tasks(tracer, start_root_span)

    p = start_profiler(tracer)
    with asyncio.Runner(loop_factory=asyncio.new_event_loop) as runner:  # type: ignore[attr-defined]
        first, second = runner.run(main())
    profile = stop_profiler(p)

    assert_two_concurrent_tasks_kept_their_own_spans(profile, first, second)


@pytest.mark.subprocess(
    env={"DD_PROFILING_OUTPUT_PPROF": "/tmp/test_long_lived_task_many_root_spans", **NO_ADAPTIVE_SAMPLING},
    # A provider that overrides active() turns inheritance mode off: a finished span's links are cleared instead of
    # moved to its parent. A root span's finish leaves a tombstone in both modes.
    parametrize={"CONTEXT_PROVIDER": ["default", "ci"]},
    err=None,
    timeout=SUBPROCESS_TIMEOUT_SECONDS,
)
def test_long_lived_task_loses_no_label_across_many_root_spans() -> None:
    """A task that serves 1000 requests one after the other, each in its own root span, keeps one task key.

    When a root span finishes, the task's key is kept with no span (a tombstone), so its birth does not change. The
    birth is the counter value fixed when the key was created, and the sampler ignores a key born after the sample
    started. If each request recreated the key, samples taken while the key was recreated would lose their span.

    Each request waits for in-flight samples only after request_work, before its span finishes. So a sample that shows
    request_work can have started before the request's span started, and it reads the key before the span finishes.
    """
    import asyncio
    import os

    from ddtrace import ext
    from ddtrace.internal.ci_visibility.context import CIContextProvider
    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.trace import Span
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_stack_asyncio_task_attribution import busy
    from tests.profiling.collector.test_stack_asyncio_task_attribution import sample_labels
    from tests.profiling.collector.test_stack_asyncio_task_attribution import span_labels
    from tests.profiling.collector.test_stack_asyncio_task_attribution import start_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import stop_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import wait_for_in_flight_samples

    if os.environ["CONTEXT_PROVIDER"] == "ci":
        tracer.configure(context_provider=CIContextProvider())

    def request_work() -> None:
        busy(0.001)

    async def serve_requests() -> list[Span]:
        # The first span creates the task's key. The requests below only update it.
        with tracer.trace("warm-up"):
            pass
        wait_for_in_flight_samples()
        spans = []
        for _ in range(1000):
            with tracer.trace("request", resource="request-endpoint", span_type=ext.SpanTypes.WEB) as span:
                spans.append(span)
                request_work()
                wait_for_in_flight_samples()
            await asyncio.sleep(0)
        return spans

    async def main() -> list[Span]:
        return await asyncio.create_task(serve_requests())

    p = start_profiler(tracer)
    # A short interval keeps each wait short, and puts more samples in flight while the spans start.
    stack.set_interval(0.001)
    spans = asyncio.run(main())
    profile = stop_profiler(p)

    labels = sample_labels(profile, "request_work")
    assert labels <= {span_labels(span, "request-endpoint") for span in spans}, labels


@pytest.mark.subprocess(
    env={"DD_PROFILING_OUTPUT_PPROF": "/tmp/test_direct_asyncio_task", **NO_ADAPTIVE_SAMPLING},
    parametrize=ASYNCIO_INTEGRATION,
    err=None,
    timeout=SUBPROCESS_TIMEOUT_SECONDS,
)
def test_task_created_directly_with_asyncio_task_carries_only_its_own_activation() -> None:
    """A task created with asyncio.Task(...) skips loop.create_task, so no creation hook seeds its key.

    It inherits no span in its samples, even though the tracer sees its creator's span in it. A span it activates
    itself is still published to its key.
    """
    import asyncio

    from ddtrace import ext
    from ddtrace.trace import Span
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_stack_asyncio_task_attribution import NO_SPAN
    from tests.profiling.collector.test_stack_asyncio_task_attribution import OtherSpanOnTheThread
    from tests.profiling.collector.test_stack_asyncio_task_attribution import busy
    from tests.profiling.collector.test_stack_asyncio_task_attribution import patch_asyncio_integration
    from tests.profiling.collector.test_stack_asyncio_task_attribution import sample_labels
    from tests.profiling.collector.test_stack_asyncio_task_attribution import span_labels
    from tests.profiling.collector.test_stack_asyncio_task_attribution import start_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import stop_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import wait_for_in_flight_samples

    patch_asyncio_integration()

    def inherited_span_work() -> None:
        busy(0.3)

    def own_span_work() -> None:
        busy(0.3)

    async def inherits_only() -> None:
        wait_for_in_flight_samples()
        inherited_span_work()
        wait_for_in_flight_samples()

    async def activates_its_own_span() -> Span:
        with tracer.trace("own.work") as own:
            wait_for_in_flight_samples()
            own_span_work()
            wait_for_in_flight_samples()
        return own

    async def main() -> Span:
        with tracer.trace("parent.request", resource="parent-endpoint", span_type=ext.SpanTypes.WEB) as parent:
            async with OtherSpanOnTheThread(tracer):
                _, own = await asyncio.gather(asyncio.Task(inherits_only()), asyncio.Task(activates_its_own_span()))
        assert own.parent_id == parent.span_id
        return own

    p = start_profiler(tracer)
    own = asyncio.run(main())
    profile = stop_profiler(p)

    assert sample_labels(profile, "inherited_span_work") == {NO_SPAN}
    assert sample_labels(profile, "own_span_work") == {span_labels(own, "parent-endpoint")}


@pytest.mark.subprocess(
    env={"DD_PROFILING_OUTPUT_PPROF": "/tmp/test_ci_context_provider_tasks", **NO_ADAPTIVE_SAMPLING},
    err=None,
    timeout=SUBPROCESS_TIMEOUT_SECONDS,
)
def test_provider_with_its_own_active_rule_gives_tasks_only_their_own_activations() -> None:
    """CIContextProvider overrides active(), so the profiler cannot predict what a task inherits or what a finished
    span is replaced with.

    So a child task inherits no span in its samples, and when a task's own span finishes, its label is cleared instead
    of moving to the parent span.
    """
    import asyncio

    from ddtrace import ext
    from ddtrace.internal.ci_visibility.context import CIContextProvider
    from ddtrace.trace import Span
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_stack_asyncio_task_attribution import NO_SPAN
    from tests.profiling.collector.test_stack_asyncio_task_attribution import OtherSpanOnTheThread
    from tests.profiling.collector.test_stack_asyncio_task_attribution import busy
    from tests.profiling.collector.test_stack_asyncio_task_attribution import finish_on_another_thread
    from tests.profiling.collector.test_stack_asyncio_task_attribution import sample_labels
    from tests.profiling.collector.test_stack_asyncio_task_attribution import span_labels
    from tests.profiling.collector.test_stack_asyncio_task_attribution import start_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import stop_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import wait_for_in_flight_samples

    tracer.configure(context_provider=CIContextProvider())

    def child_work() -> None:
        busy(0.3)

    def while_the_own_span_runs() -> None:
        busy(0.3)

    def after_the_own_span_finished() -> None:
        busy(0.3)

    async def child() -> None:
        wait_for_in_flight_samples()
        child_work()
        wait_for_in_flight_samples()

    async def activates_its_own_span() -> Span:
        own = tracer.start_span("own.request", resource="own-endpoint", span_type=ext.SpanTypes.WEB, activate=True)
        own_child = tracer.start_span("own.child", child_of=own, activate=True)
        wait_for_in_flight_samples()
        while_the_own_span_runs()
        wait_for_in_flight_samples()
        finish_on_another_thread(own_child)
        wait_for_in_flight_samples()
        after_the_own_span_finished()
        wait_for_in_flight_samples()
        own.finish()
        return own_child

    async def main() -> Span:
        with tracer.trace("parent.request", resource="parent-endpoint", span_type=ext.SpanTypes.WEB):
            async with OtherSpanOnTheThread(tracer):
                await asyncio.create_task(child())
                return await asyncio.create_task(activates_its_own_span())

    p = start_profiler(tracer)
    own_child = asyncio.run(main())
    profile = stop_profiler(p)

    assert sample_labels(profile, "child_work") == {NO_SPAN}
    assert sample_labels(profile, "while_the_own_span_runs") == {span_labels(own_child, "own-endpoint")}
    assert sample_labels(profile, "after_the_own_span_finished") == {NO_SPAN}


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_otel_concurrent_tasks",
        "DD_TRACE_OTEL_ENABLED": "true",
        **NO_ADAPTIVE_SAMPLING,
    },
    ddtrace_run=True,
    err=None,
    timeout=SUBPROCESS_TIMEOUT_SECONDS,
)
def test_otel_concurrent_tasks_keep_their_own_spans() -> None:
    """Spans started with the OpenTelemetry API are published to the task's key like any other span."""
    import asyncio
    import contextlib
    from typing import Iterator

    import opentelemetry.trace

    from ddtrace import ext
    from ddtrace.trace import Span
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_stack_asyncio_task_attribution import (
        assert_two_concurrent_tasks_kept_their_own_spans,
    )
    from tests.profiling.collector.test_stack_asyncio_task_attribution import run_two_concurrent_traced_tasks
    from tests.profiling.collector.test_stack_asyncio_task_attribution import start_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import stop_profiler

    oteltracer = opentelemetry.trace.get_tracer(__name__)

    @contextlib.contextmanager
    def start_root_span(name: str, endpoint: str) -> Iterator[Span]:
        attributes = {"resource.name": endpoint, "span.type": ext.SpanTypes.WEB}
        with oteltracer.start_as_current_span(name, attributes=attributes) as otel_span:
            yield otel_span._ddspan

    p = start_profiler(tracer)
    first, second = asyncio.run(run_two_concurrent_traced_tasks(tracer, start_root_span))
    profile = stop_profiler(p)

    assert_two_concurrent_tasks_kept_their_own_spans(profile, first, second)


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_otel_child_task",
        "DD_TRACE_OTEL_ENABLED": "true",
        **NO_ADAPTIVE_SAMPLING,
    },
    ddtrace_run=True,
    err=None,
    timeout=SUBPROCESS_TIMEOUT_SECONDS,
)
def test_otel_child_task_carries_the_inherited_span_until_it_attaches_an_invalid_span() -> None:
    """A child task inherits the OpenTelemetry span its creator started.

    A NonRecordingSpan with the invalid span context (span id 0) that the task attaches is no span, so the task's
    samples then carry no span. They do not keep the inherited span, nor take the thread's span.
    """
    import asyncio

    import opentelemetry.context
    import opentelemetry.trace
    from opentelemetry.trace import INVALID_SPAN_CONTEXT
    from opentelemetry.trace import NonRecordingSpan

    from ddtrace import ext
    from ddtrace.trace import Span
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_stack_asyncio_task_attribution import NO_SPAN
    from tests.profiling.collector.test_stack_asyncio_task_attribution import OtherSpanOnTheThread
    from tests.profiling.collector.test_stack_asyncio_task_attribution import busy
    from tests.profiling.collector.test_stack_asyncio_task_attribution import sample_labels
    from tests.profiling.collector.test_stack_asyncio_task_attribution import span_labels
    from tests.profiling.collector.test_stack_asyncio_task_attribution import start_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import stop_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import wait_for_in_flight_samples

    oteltracer = opentelemetry.trace.get_tracer(__name__)

    def inherited_otel_span_work() -> None:
        busy(0.3)

    def invalid_span_work() -> None:
        busy(0.3)

    async def child() -> None:
        wait_for_in_flight_samples()
        inherited_otel_span_work()
        wait_for_in_flight_samples()
        opentelemetry.context.attach(opentelemetry.trace.set_span_in_context(NonRecordingSpan(INVALID_SPAN_CONTEXT)))
        wait_for_in_flight_samples()
        invalid_span_work()
        wait_for_in_flight_samples()

    async def main() -> Span:
        attributes = {"resource.name": "parent-endpoint", "span.type": ext.SpanTypes.WEB}
        with oteltracer.start_as_current_span("parent.request", attributes=attributes) as parent:
            async with OtherSpanOnTheThread(tracer):
                await asyncio.create_task(child())
        ddspan: Span = parent._ddspan
        return ddspan

    p = start_profiler(tracer)
    parent = asyncio.run(main())
    profile = stop_profiler(p)

    assert sample_labels(profile, "inherited_otel_span_work") == {span_labels(parent, "parent-endpoint")}
    assert sample_labels(profile, "invalid_span_work") == {NO_SPAN}


@pytest.mark.skipif(sys.platform != "linux", reason="the OTel thread context is only published on Linux")
@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_otel_thread_context_matches_task_samples",
        "DD_TRACE_OTEL_CTX_ENABLED": "true",
        **NO_ADAPTIVE_SAMPLING,
    },
    err=None,
    timeout=SUBPROCESS_TIMEOUT_SECONDS,
)
def test_otel_thread_context_and_task_samples_name_the_same_span() -> None:
    """The OTel thread context is a per-thread record of the active span that other tools read.

    It is updated on every context switch: natively from 3.14, and by the asyncio integration before. After the task
    resumes from a switch during which another task activated its span, both the record and the task's on-CPU
    samples name the task's own span.
    """
    import asyncio
    import sys

    from ddtrace import ext
    from ddtrace.trace import Span
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_stack_asyncio_task_attribution import OtherSpanOnTheThread
    from tests.profiling.collector.test_stack_asyncio_task_attribution import busy
    from tests.profiling.collector.test_stack_asyncio_task_attribution import sample_labels
    from tests.profiling.collector.test_stack_asyncio_task_attribution import span_labels
    from tests.profiling.collector.test_stack_asyncio_task_attribution import start_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import stop_profiler
    from tests.profiling.collector.test_stack_asyncio_task_attribution import wait_for_in_flight_samples
    from tests.tracer.test_otel_thread_context import _published_span_id

    if sys.version_info < (3, 14):
        from ddtrace.contrib.internal.asyncio import patch as asyncio_patch

        asyncio_patch.patch()

    published: list[object] = []

    def on_cpu_work() -> None:
        busy(0.3)

    async def traced_task(other_active: asyncio.Event) -> Span:
        with tracer.trace("task.request", resource="task-endpoint", span_type=ext.SpanTypes.WEB) as span:
            await other_active.wait()
            wait_for_in_flight_samples()
            published.append(_published_span_id())
            on_cpu_work()
            wait_for_in_flight_samples()
            published.append(_published_span_id())
        return span

    async def main() -> Span:
        other_active = asyncio.Event()
        task = asyncio.create_task(traced_task(other_active))
        await asyncio.sleep(0)
        async with OtherSpanOnTheThread(tracer):
            other_active.set()
            return await task

    p = start_profiler(tracer)
    span = asyncio.run(main())
    profile = stop_profiler(p)

    assert published == [span.span_id, span.span_id]
    assert sample_labels(profile, "on_cpu_work", value_type="cpu-time") == {span_labels(span, "task-endpoint")}
