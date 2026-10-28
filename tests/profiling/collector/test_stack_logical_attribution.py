"""Tests for the set_logical_attribution switches of the stack extension.

While a domain's switch is off, which is the native default, task and greenlet samples get their thread's span, as
they did before tasks and greenlets had span link keys of their own. Once it is on, they get only their own key's
span, or no span at all. The sampler reads only the switch of the domain it renders on a thread: tasks on a thread
with an asyncio loop, greenlets otherwise. So the other domain's switch changes nothing there.

The profiler turns SPAN_LINK_DOMAIN_ASYNCIO_TASK on when it hooks asyncio (see _install_task_span_link_hooks), so the
asyncio tests here set that switch explicitly.
"""

import os
import sys
from typing import TYPE_CHECKING

import pytest

from ddtrace.internal.datadog.profiling import stack


if TYPE_CHECKING:
    from tests.profiling.collector.pprof_pb2 import Profile  # pyright: ignore[reportMissingModuleSource]
    from tests.profiling.collector.pprof_pb2 import Sample  # pyright: ignore[reportMissingModuleSource]


pytestmark = pytest.mark.skipif(not stack.is_available, reason="stack profiler not available")

if stack.is_available:
    from ddtrace.internal.datadog.profiling.stack import _stack

# Python 3.11.9 to 3.12.4 are not compatible with gevent, see tests/profiling/collector/test_stack.py.
GEVENT_COMPATIBLE_WITH_PYTHON_VERSION = os.getenv("DD_PROFILE_TEST_GEVENT", False) and (
    sys.version_info < (3, 11, 9) or sys.version_info >= (3, 12, 5)
)

# The spans the end-to-end tests link to the thread and to one task or greenlet on it.
THREAD_SPAN_ID = 1001
THREAD_TRACE_TYPE = "thread-type"
UNIT_SPAN_ID = 2001
UNIT_TRACE_TYPE = "unit-type"


def assert_unit_span_labels(
    profile: "Profile", linked: list["Sample"], unlinked: list["Sample"], attributed: bool
) -> None:
    """Check the span labels of the samples of a linked unit and of an unlinked unit on the same thread.

    The thread itself is linked to THREAD_SPAN_ID, and the linked unit to UNIT_SPAN_ID.
    """
    from tests.profiling.collector import pprof_utils

    assert linked
    assert unlinked
    # Task and greenlet samples carry a task name, so these are not plain thread samples.
    assert all(pprof_utils.get_str_label(profile, s, "task name") for s in linked + unlinked)

    def span_ids(samples: list["Sample"]) -> set[object]:
        return {pprof_utils.get_num_label(profile, s, "span id") for s in samples}

    def trace_types(samples: list["Sample"]) -> set[object]:
        return {pprof_utils.get_str_label(profile, s, "trace type") for s in samples}

    if attributed:
        # The linked unit's own key is created while it runs. A sample whose unwind started just before that write
        # ignores the new key (its birth is after the unwind started) and gets no span, so None is allowed here.
        assert UNIT_SPAN_ID in span_ids(linked)
        assert span_ids(linked) <= {UNIT_SPAN_ID, None}
        assert trace_types(linked) <= {UNIT_TRACE_TYPE, None}
        # An unlinked unit gets no span, never the thread's span.
        assert span_ids(unlinked) == {None}
        assert trace_types(unlinked) == {None}
    else:
        assert span_ids(linked) == {THREAD_SPAN_ID}
        assert span_ids(unlinked) == {THREAD_SPAN_ID}
        assert trace_types(linked + unlinked) == {THREAD_TRACE_TYPE}


@pytest.mark.parametrize("domain", [-1, 0, 3])
def test_set_logical_attribution_rejects_domains_without_a_switch(domain: int) -> None:
    # 0 is the thread domain, which has no switch.
    assert domain not in (_stack.SPAN_LINK_DOMAIN_ASYNCIO_TASK, _stack.SPAN_LINK_DOMAIN_GREENLET)
    with pytest.raises(ValueError):
        stack.set_logical_attribution(domain, True)


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_logical_attribution_asyncio",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    parametrize={"LOGICAL_ATTRIBUTION_SWITCH": ["none", "greenlet", "asyncio_task"]},
    err=None,
)
def test_asyncio_task_samples_use_the_thread_span_until_the_task_switch_is_on() -> None:
    """Task keys are ignored unless the AsyncioTask switch is on. The Greenlet switch does not affect tasks."""
    import asyncio
    import os
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_stack_logical_attribution import THREAD_SPAN_ID
    from tests.profiling.collector.test_stack_logical_attribution import THREAD_TRACE_TYPE
    from tests.profiling.collector.test_stack_logical_attribution import UNIT_SPAN_ID
    from tests.profiling.collector.test_stack_logical_attribution import UNIT_TRACE_TYPE
    from tests.profiling.collector.test_stack_logical_attribution import assert_unit_span_labels

    thread_span = (THREAD_SPAN_ID, 1000, THREAD_TRACE_TYPE, _stack.SPAN_LINK_KIND_SPAN)
    unit_span = (UNIT_SPAN_ID, 2000, UNIT_TRACE_TYPE, _stack.SPAN_LINK_KIND_SPAN)

    switch = os.environ["LOGICAL_ATTRIBUTION_SWITCH"]
    # The profiler turned the AsyncioTask switch on when it hooked asyncio, on import, so the other cases turn it off.
    stack.set_logical_attribution(_stack.SPAN_LINK_DOMAIN_ASYNCIO_TASK, switch == "asyncio_task")
    if switch == "greenlet":
        stack.set_logical_attribution(_stack.SPAN_LINK_DOMAIN_GREENLET, True)

    def busy() -> None:
        deadline = time.thread_time_ns() + 300_000_000
        while time.thread_time_ns() < deadline:
            pass

    def linked_task_work() -> None:
        busy()

    def unlinked_task_work() -> None:
        busy()

    async def linked_task() -> None:
        stack.link(stack.lifecycle(), False, id(asyncio.current_task()), 0, unit_span)
        linked_task_work()

    async def unlinked_task() -> None:
        unlinked_task_work()

    async def main() -> None:
        # The thread's span is what every task sample got before tasks had keys.
        stack.link(stack.lifecycle(), True, 0, 0, thread_span)
        await asyncio.gather(asyncio.create_task(linked_task()), asyncio.create_task(unlinked_task()))

    p = profiler.Profiler()
    p.start()
    # Nothing in the profiler opens the keyed span links yet, so the test opens them for its own writes.
    stack.links_open()
    asyncio.run(main())
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    assert_unit_span_labels(
        profile,
        pprof_utils.get_samples_with_function(profile, wall_samples, "linked_task_work"),
        pprof_utils.get_samples_with_function(profile, wall_samples, "unlinked_task_work"),
        attributed=switch == "asyncio_task",
    )


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_logical_attribution_unconfigured_loop",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
    timeout=60,
)
def test_asyncio_task_on_a_loop_never_passed_to_set_event_loop_is_attributed() -> None:
    """The sampler picks the task domain from the loop it actually reads, not only from the configured loop.

    A loop made with new_event_loop and run with run_until_complete is never passed to set_event_loop, so the thread's
    configured loop stays empty and only the running-loop slot holds it. Its tasks must still be read as task samples
    with the AsyncioTask switch on: the linked task gets its own span and the unlinked task gets no span. If the domain
    came from the configured loop, the sampler would read the Greenlet switch, which is off, and both tasks would get
    the thread's span.
    """
    import asyncio
    import os
    import threading
    import time

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_stack_logical_attribution import THREAD_SPAN_ID
    from tests.profiling.collector.test_stack_logical_attribution import THREAD_TRACE_TYPE
    from tests.profiling.collector.test_stack_logical_attribution import UNIT_SPAN_ID
    from tests.profiling.collector.test_stack_logical_attribution import UNIT_TRACE_TYPE
    from tests.profiling.collector.test_stack_logical_attribution import assert_unit_span_labels

    thread_span = (THREAD_SPAN_ID, 1000, THREAD_TRACE_TYPE, _stack.SPAN_LINK_KIND_SPAN)
    unit_span = (UNIT_SPAN_ID, 2000, UNIT_TRACE_TYPE, _stack.SPAN_LINK_KIND_SPAN)

    stack.set_logical_attribution(_stack.SPAN_LINK_DOMAIN_ASYNCIO_TASK, True)

    def busy() -> None:
        deadline = time.thread_time_ns() + 300_000_000
        while time.thread_time_ns() < deadline:
            pass

    def linked_task_work() -> None:
        busy()

    def unlinked_task_work() -> None:
        busy()

    async def linked_task() -> None:
        stack.link(stack.lifecycle(), False, id(asyncio.current_task()), 0, unit_span)
        linked_task_work()

    async def unlinked_task() -> None:
        unlinked_task_work()

    async def main() -> None:
        # The case under test: the loop is only in the running-loop slot, never in the configured slot.
        slots = _stack._asyncio_loop_slots(threading.get_ident())
        assert slots is not None
        assert slots["configured_loop"] == 0, slots
        assert slots["running_loop"] == id(asyncio.get_running_loop()), slots
        stack.link(stack.lifecycle(), True, 0, 0, thread_span)
        loop = asyncio.get_running_loop()
        await asyncio.gather(loop.create_task(linked_task()), loop.create_task(unlinked_task()))

    p = profiler.Profiler()
    p.start()
    # Nothing in the profiler opens the keyed span links yet, so the test opens them for its own writes.
    stack.links_open()
    loop = asyncio.new_event_loop()
    loop.run_until_complete(main())
    loop.close()
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    assert_unit_span_labels(
        profile,
        pprof_utils.get_samples_with_function(profile, wall_samples, "linked_task_work"),
        pprof_utils.get_samples_with_function(profile, wall_samples, "unlinked_task_work"),
        attributed=True,
    )


@pytest.mark.skipif(
    not GEVENT_COMPATIBLE_WITH_PYTHON_VERSION,
    reason=f"gevent is not compatible with Python {'.'.join(map(str, tuple(sys.version_info)[:3]))}",
)
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={"_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0"},
    parametrize={"LOGICAL_ATTRIBUTION_SWITCH": ["none", "asyncio_task", "greenlet"]},
    err=None,
)
def test_greenlet_samples_use_the_thread_span_until_the_greenlet_switch_is_on() -> None:
    """Greenlet keys are ignored unless the Greenlet switch is on. The AsyncioTask switch does not affect greenlets."""
    from gevent import monkey

    monkey.patch_all()

    import os
    import time

    import gevent
    import gevent.thread

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling.collector import stack as stack_collector
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_stack_logical_attribution import THREAD_SPAN_ID
    from tests.profiling.collector.test_stack_logical_attribution import THREAD_TRACE_TYPE
    from tests.profiling.collector.test_stack_logical_attribution import UNIT_SPAN_ID
    from tests.profiling.collector.test_stack_logical_attribution import UNIT_TRACE_TYPE
    from tests.profiling.collector.test_stack_logical_attribution import assert_unit_span_labels

    thread_span = (THREAD_SPAN_ID, 1000, THREAD_TRACE_TYPE, _stack.SPAN_LINK_KIND_SPAN)
    unit_span = (UNIT_SPAN_ID, 2000, UNIT_TRACE_TYPE, _stack.SPAN_LINK_KIND_SPAN)

    switch = os.environ["LOGICAL_ATTRIBUTION_SWITCH"]
    if switch == "asyncio_task":
        stack.set_logical_attribution(_stack.SPAN_LINK_DOMAIN_ASYNCIO_TASK, True)
    elif switch == "greenlet":
        stack.set_logical_attribution(_stack.SPAN_LINK_DOMAIN_GREENLET, True)

    pprof_prefix = "/tmp/test_stack_logical_attribution_gevent"
    ddup.config(env="test", service="test_stack_logical_attribution", version="0", output_filename=pprof_prefix)
    ddup.start()

    def busy() -> None:
        deadline = time.thread_time_ns() + 300_000_000
        while time.thread_time_ns() < deadline:
            pass

    def linked_greenlet_work() -> None:
        busy()

    def unlinked_greenlet_work() -> None:
        busy()

    def linked_greenlet() -> None:
        # The sampler keys greenlets by gevent.thread.get_ident(greenlet), which is the greenlet's id().
        stack.link(stack.lifecycle(), False, 0, gevent.thread.get_ident(), unit_span)
        linked_greenlet_work()

    def unlinked_greenlet() -> None:
        unlinked_greenlet_work()

    with stack_collector.StackCollector():
        # Nothing in the profiler opens the keyed span links yet, so the test opens them for its own writes.
        stack.links_open()
        # The thread's span is what every greenlet sample got before greenlets had keys.
        stack.link(stack.lifecycle(), True, 0, 0, thread_span)
        gevent.joinall([gevent.spawn(linked_greenlet), gevent.spawn(unlinked_greenlet)], timeout=30)

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(pprof_prefix + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    assert_unit_span_labels(
        profile,
        pprof_utils.get_samples_with_function(profile, wall_samples, "linked_greenlet_work"),
        pprof_utils.get_samples_with_function(profile, wall_samples, "unlinked_greenlet_work"),
        attributed=switch == "greenlet",
    )
