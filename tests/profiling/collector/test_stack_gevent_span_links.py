"""Tests for the span links of gevent greenlets: each greenlet sample gets the span of its own greenlet.

The key tests read the greenlet keys directly, and do not monkey patch, so that threading.Thread stays a native thread.
The profile tests check the span labels of greenlet samples, with the gevent integration of the tracer patched, as
ddtrace-run patches it in an application.
"""

import sys
from typing import TYPE_CHECKING
from typing import Optional

import pytest

from ddtrace.internal.datadog.profiling import stack
from tests.profiling.collector.test_stack_logical_attribution import GEVENT_COMPATIBLE_WITH_PYTHON_VERSION


if TYPE_CHECKING:
    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling.collector.stack import StackCollector
    from ddtrace.trace import Tracer
    from tests.profiling.collector.pprof_pb2 import Profile  # pyright: ignore[reportMissingModuleSource]
    from tests.profiling.collector.pprof_pb2 import Sample  # pyright: ignore[reportMissingModuleSource]


pytestmark = [
    pytest.mark.skipif(not stack.is_available, reason="stack profiler not available"),
    pytest.mark.skipif(
        not GEVENT_COMPATIBLE_WITH_PYTHON_VERSION,
        reason=f"gevent is not compatible with Python {'.'.join(map(str, tuple(sys.version_info)[:3]))}",
    ),
]


def start_stack_collector(tracer: "Tracer", name: str) -> "StackCollector":
    """Start the exporter, which the sampler hands its samples to, then a stack collector that publishes spans."""
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector.stack import StackCollector

    ddup.config(env="test", service=name, version="0", output_filename="/tmp/" + name)
    ddup.start()
    collector = StackCollector(tracer=tracer)
    collector.start()
    return collector


def greenlet_entry(greenlet_id: Optional[int] = None) -> "_stack.SpanLinkEntry":
    """Return the span link entry of a greenlet, by default the current one, or None if it has no key."""
    import gevent.thread

    from ddtrace.internal.datadog.profiling.stack import _stack

    # The sampler keys a greenlet by gevent.thread.get_ident(greenlet), which is its id().
    found = _stack._span_link_entry(
        _stack.SPAN_LINK_DOMAIN_GREENLET, gevent.thread.get_ident() if greenlet_id is None else greenlet_id
    )
    return None if found is None else found[0]


def span_ids(profile: "Profile", samples: list["Sample"]) -> set[Optional[int]]:
    """Return the span ids of the samples, as the tracer's unsigned span ids."""
    from tests.profiling.collector import pprof_utils

    found = {pprof_utils.get_num_label(profile, sample, "span id") for sample in samples}
    # pprof numeric labels are signed int64 values.
    return {None if span_id is None else span_id % (1 << 64) for span_id in found}


def busy(seconds: float) -> None:
    import time

    deadline = time.thread_time_ns() + int(seconds * 1e9)
    while time.thread_time_ns() < deadline:
        pass


@pytest.mark.subprocess(err=None)
def test_greenlet_switch_is_on_only_while_greenlets_are_tracked() -> None:
    import gevent
    from greenlet import gettrace

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import _gevent

    calls = []
    set_logical_attribution = stack.set_logical_attribution

    def recording_set_logical_attribution(domain: int, enabled: bool) -> None:
        tracking = (_gevent._greenlet_tracer_enabled, gettrace() is _gevent.greenlet_tracer, gevent.spawn.__module__)
        calls.append((domain, enabled, tracking))
        set_logical_attribution(domain, enabled)

    stack.set_logical_attribution = recording_set_logical_attribution
    _gevent.patch()
    _gevent.unpatch()

    tracking = (True, True, "ddtrace.profiling._gevent")
    assert calls == [
        # On only once greenlets are tracked.
        (_stack.SPAN_LINK_DOMAIN_GREENLET, True, tracking),
        # Off while greenlets are still tracked.
        (_stack.SPAN_LINK_DOMAIN_GREENLET, False, tracking),
    ]


@pytest.mark.subprocess(err=None)
def test_untracking_a_greenlet_removes_its_greenlet_key() -> None:
    import gevent
    import gevent.event

    from ddtrace.profiling import _gevent
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_stack_gevent_span_links import greenlet_entry
    from tests.profiling.collector.test_stack_gevent_span_links import start_stack_collector
    from tests.profiling.collector.test_stack_span_links import span_entry

    collector = start_stack_collector(tracer, "test_untracking_a_greenlet_removes_its_greenlet_key")
    span = tracer.start_span("greenlet", activate=False)
    ready = gevent.event.Event()
    release = gevent.event.Event()

    def holds_span() -> None:
        # The span stays active when the greenlet ends, so only untracking can remove the key.
        tracer.context_provider.activate(span)
        ready.set()
        release.wait(5)

    worker = gevent.spawn(holds_span)
    worker_id = id(worker)
    assert ready.wait(5)
    assert worker_id in _gevent._natively_tracked
    assert greenlet_entry(worker_id) == span_entry(span)

    release.set()
    worker.join(5)
    # The untracking callback runs from the event loop after the greenlet ends.
    for _ in range(100):
        if worker_id not in _gevent._natively_tracked:
            break
        gevent.sleep(0.01)
    assert worker_id not in _gevent._natively_tracked
    assert worker_id not in _gevent._seeded
    assert greenlet_entry(worker_id) is None

    collector.stop()
    span.finish()


@pytest.mark.subprocess(err=None)
def test_spawning_a_greenlet_erases_a_key_left_at_its_address() -> None:
    import gevent

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import _gevent
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_stack_gevent_span_links import greenlet_entry
    from tests.profiling.collector.test_stack_gevent_span_links import start_stack_collector
    from tests.profiling.collector.test_stack_span_links import span_entry

    collector = start_stack_collector(tracer, "test_spawning_a_greenlet_erases_a_key_left_at_its_address")
    seen = {}

    def reads_own_key() -> None:
        seen["entry"] = greenlet_entry()

    fresh = gevent.Greenlet(reads_own_key)
    fresh_id = id(fresh)
    # A key and a seeding record that a dead greenlet at the same address left behind.
    stale = tracer.start_span("stale", activate=False)
    stack.link(stack.lifecycle(), False, 0, fresh_id, span_entry(stale))
    _gevent._seeded[fresh_id] = stack.lifecycle()
    assert greenlet_entry(fresh_id) == span_entry(stale)

    # What Greenlet.spawn does after it creates a greenlet.
    _gevent.track_gevent_greenlet(fresh)
    assert greenlet_entry(fresh_id) is None
    assert fresh_id not in _gevent._seeded

    fresh.start()
    fresh.join(5)
    assert seen == {"entry": None}

    collector.stop()
    stale.finish()


@pytest.mark.subprocess(err=None)
def test_collecting_a_suspended_greenlet_of_an_exited_thread_removes_its_key() -> None:
    import threading
    import weakref

    import greenlet

    from ddtrace.profiling import _gevent
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_stack_gevent_span_links import greenlet_entry
    from tests.profiling.collector.test_stack_gevent_span_links import start_stack_collector
    from tests.profiling.collector.test_stack_span_links import span_entry

    collector = start_stack_collector(
        tracer, "test_collecting_a_suspended_greenlet_of_an_exited_thread_removes_its_key"
    )
    span = tracer.start_span("greenlet", activate=False)
    seen = {}

    class NamedGreenlet(greenlet.greenlet):
        # The greenlet tracer tracks any greenlet with a name. A suspended gevent.Greenlet of an exited thread is
        # never freed with gevent 25.9 and greenlet 3.3, even by the garbage collector, so it cannot show the cleanup.
        name = "named"

    def native_thread() -> None:
        # The greenlet tracer is installed per native thread.
        _gevent.patch()
        thread_main = greenlet.getcurrent()

        def suspended_forever() -> None:
            tracer.context_provider.activate(span)
            thread_main.switch()

        # The greenlet tracer tracks this greenlet when it first switches to it.
        suspended = NamedGreenlet(suspended_forever)
        suspended.switch()
        seen["greenlet"] = suspended
        seen["tracked"] = id(suspended) in _gevent._natively_tracked
        seen["entry"] = greenlet_entry(id(suspended))
        # The thread exits while the greenlet is suspended, so no switch ever shows that the greenlet is gone.

    thread = threading.Thread(target=native_thread)
    thread.start()
    thread.join(10)
    assert not thread.is_alive()
    assert seen["tracked"]
    assert seen["entry"] == span_entry(span)

    suspended_id = id(seen["greenlet"])
    suspended_ref = weakref.ref(seen.pop("greenlet"))
    assert suspended_ref() is None
    assert suspended_id not in _gevent._natively_tracked
    assert greenlet_entry(suspended_id) is None

    collector.stop()
    span.finish()


@pytest.mark.subprocess(err=None)
def test_main_and_raw_greenlets_get_no_greenlet_key() -> None:
    import threading

    import gevent
    import greenlet

    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import _gevent
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_stack_gevent_span_links import greenlet_entry
    from tests.profiling.collector.test_stack_gevent_span_links import start_stack_collector
    from tests.profiling.collector.test_stack_span_links import span_entry

    collector = start_stack_collector(tracer, "test_main_and_raw_greenlets_get_no_greenlet_key")
    main = greenlet.getcurrent()
    seen = {}

    def thread_entry() -> "_stack.SpanLinkEntry":
        found = _stack._span_link_entry(_stack.SPAN_LINK_DOMAIN_THREAD, threading.get_ident())
        return None if found is None else found[0]

    def raw() -> None:
        with tracer.trace("raw") as raw_span:
            seen["raw thread entry"] = (thread_entry(), span_entry(raw_span))
            main.switch()

    # A raw greenlet has no name, so the stack profiler cannot track it. Neither can it track the main greenlet.
    raw_greenlet = greenlet.greenlet(raw)
    with tracer.trace("main"):
        raw_greenlet.switch()
        # Switch through the hub too, so the greenlet tracer sees the main greenlet more than once.
        gevent.sleep(0)
    raw_greenlet.switch()
    assert raw_greenlet.dead

    # Their samples are rendered as thread samples, which get the thread's span.
    entry, expected = seen["raw thread entry"]
    assert entry == expected
    for greenlet_id in (id(main), id(raw_greenlet)):
        assert greenlet_id not in _gevent._natively_tracked
        assert greenlet_id not in _gevent._seeded
        assert greenlet_entry(greenlet_id) is None

    collector.stop()


@pytest.mark.subprocess(err=None, parametrize={"CONTEXT_PROVIDER": ["default", "ci_visibility"]})
def test_restart_inside_a_greenlet_keeps_its_span_after_a_switch() -> None:
    import os

    import gevent

    from ddtrace.internal.ci_visibility.context import CIContextProvider
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_stack_gevent_span_links import greenlet_entry
    from tests.profiling.collector.test_stack_gevent_span_links import start_stack_collector
    from tests.profiling.collector.test_stack_span_links import span_entry

    if os.environ["CONTEXT_PROVIDER"] == "ci_visibility":
        # This provider is not in inheritance mode, and keeps its spans in its own ContextVar, so the greenlet's
        # DD_CONTEXTVAR holds no span. Seeding is skipped for such a provider: it would read that empty ContextVar and
        # erase the label that start republished.
        tracer.context_provider = CIContextProvider()
    collector = start_stack_collector(tracer, "test_restart_inside_a_greenlet_keeps_its_span_after_a_switch")
    seen = {}

    def restarts_in_span() -> None:
        with tracer.trace("greenlet") as span:
            seen["span"] = span_entry(span)
            seen["before restart"] = greenlet_entry()
            collector.stop()
            collector.start()
            # start() republishes the active span of the current greenlet.
            seen["after restart"] = greenlet_entry()
            gevent.sleep(0)
            seen["after switch"] = greenlet_entry()

    gevent.spawn(restarts_in_span).join(5)
    collector.stop()

    expected = seen.pop("span")
    assert seen == {"before restart": expected, "after restart": expected, "after switch": expected}


@pytest.mark.subprocess(err=None, parametrize={"RESTART": ["before_write", "after_write"]})
def test_restart_during_seeding_seeds_again_on_the_next_switch() -> None:
    import os

    import gevent
    import gevent.event

    from ddtrace.profiling import _gevent
    from ddtrace.profiling import _span_links
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_stack_gevent_span_links import greenlet_entry
    from tests.profiling.collector.test_stack_gevent_span_links import start_stack_collector
    from tests.profiling.collector.test_stack_span_links import span_entry

    collector = start_stack_collector(tracer, "test_restart_during_seeding_seeds_again_on_the_next_switch")
    suspended = gevent.event.Event()
    resume = gevent.event.Event()
    seen = {}

    def worker() -> None:
        with tracer.trace("greenlet") as span:
            seen["span"] = span_entry(span)
            suspended.set()
            resume.wait(5)
            seen["seeded after restart"] = _gevent._seeded.get(id(gevent.getcurrent())) == _span_links.lifecycle_mirror
            # This switch seeds the greenlet again.
            gevent.sleep(0)
            seen["seeded after switch"] = _gevent._seeded.get(id(gevent.getcurrent())) == _span_links.lifecycle_mirror
            seen["entry after switch"] = greenlet_entry()

    worker_greenlet = gevent.spawn(worker)
    worker_id = id(worker_greenlet)
    assert suspended.wait(5)
    # The worker is suspended across this stop and start, so it must be seeded when it resumes.
    collector.stop()
    collector.start()
    assert greenlet_entry(worker_id) is None

    publish = _span_links.publish
    restarted = []

    def publish_with_restart(*args, **kwargs) -> None:
        greenlet_id = args[4]
        if greenlet_id != worker_id or restarted:
            publish(*args, **kwargs)
            return
        restarted.append(True)
        if os.environ["RESTART"] == "before_write":
            collector.stop()
            collector.start()
            publish(*args, **kwargs)
        else:
            publish(*args, **kwargs)
            collector.stop()
            collector.start()

    _span_links.publish = publish_with_restart
    try:
        resume.set()
        worker_greenlet.join(5)
    finally:
        _span_links.publish = publish
    collector.stop()

    assert restarted
    assert seen["seeded after restart"] is False
    assert seen["seeded after switch"] is True
    assert seen["entry after switch"] == seen["span"]


@pytest.mark.subprocess(err=None)
def test_greenlet_tracer_does_no_seeding_while_the_profiler_is_stopped() -> None:
    import gevent

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import _gevent
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_stack_gevent_span_links import greenlet_entry
    from tests.profiling.collector.test_stack_gevent_span_links import start_stack_collector

    collector = start_stack_collector(tracer, "test_greenlet_tracer_does_no_seeding_while_the_profiler_is_stopped")
    collector.stop()

    seeded = dict(_gevent._seeded)
    lifecycle_calls = []
    lifecycle = stack.lifecycle
    tracked = []

    def counting_lifecycle() -> int:
        lifecycle_calls.append(True)
        return lifecycle()

    def traced() -> None:
        with tracer.trace("greenlet"):
            # Greenlets are still tracked, so only the stopped lifecycle prevents seeding.
            tracked.append(id(gevent.getcurrent()) in _gevent._natively_tracked)
            gevent.sleep(0)
            assert greenlet_entry() is None

    stack.lifecycle = counting_lifecycle
    try:
        with tracer.trace("main"):
            greenlets = [gevent.spawn(traced) for _ in range(3)]
            gevent.joinall(greenlets, timeout=5, raise_error=True)
    finally:
        stack.lifecycle = lifecycle

    assert tracked == [True, True, True]
    assert lifecycle_calls == []
    assert _gevent._seeded == seeded


@pytest.mark.subprocess(err=None)
def test_span_finishing_before_the_seeding_write_lands_moves_the_key_to_its_parent() -> None:
    import threading

    import gevent
    import gevent.event

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_stack_gevent_span_links import greenlet_entry
    from tests.profiling.collector.test_stack_gevent_span_links import start_stack_collector
    from tests.profiling.collector.test_stack_span_links import span_entry

    collector = start_stack_collector(
        tracer, "test_span_finishing_before_the_seeding_write_lands_moves_the_key_to_its_parent"
    )
    suspended = gevent.event.Event()
    resume = gevent.event.Event()
    spans = {}
    seen = {}

    def worker() -> None:
        with tracer.trace("root") as root:
            with tracer.trace("child") as child:
                spans["root"] = root
                spans["child"] = child
                suspended.set()
                resume.wait(5)
                seen["entry"] = greenlet_entry()

    worker_greenlet = gevent.spawn(worker)
    worker_id = id(worker_greenlet)
    assert suspended.wait(5)
    # The worker is suspended across this stop and start, so it is seeded when it resumes.
    collector.stop()
    collector.start()

    link = stack.link
    finished = []

    def finish_then_link(token, thread, task_id, greenlet_id, entry) -> None:
        if greenlet_id == worker_id and not finished:
            # The seeding resolved the child before it finished on another thread.
            finished.append(True)
            finisher = threading.Thread(target=spans["child"].finish)
            finisher.start()
            finisher.join(5)
            assert not finisher.is_alive()
        link(token, thread, task_id, greenlet_id, entry)

    stack.link = finish_then_link
    try:
        resume.set()
        worker_greenlet.join(5)
    finally:
        stack.link = link
    collector.stop()

    assert finished
    assert seen["entry"] == span_entry(spans["root"])


@pytest.mark.subprocess(err=None)
def test_activation_in_an_asyncio_task_inside_a_greenlet_writes_the_task_and_greenlet_keys_in_one_call() -> None:
    # A greenlet can run its own asyncio loop. A span activated in a task of that loop is the span of the task and of
    # the greenlet that runs the loop, so the activation listener writes the thread, task and greenlet keys with one
    # stack.link call.
    import asyncio

    import gevent

    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.internal.datadog.profiling.stack import _stack
    from ddtrace.profiling import _gevent
    from ddtrace.trace import tracer
    from tests.profiling.collector.test_stack_gevent_span_links import greenlet_entry
    from tests.profiling.collector.test_stack_gevent_span_links import start_stack_collector
    from tests.profiling.collector.test_stack_span_links import span_entry

    collector = start_stack_collector(
        tracer, "test_activation_in_an_asyncio_task_inside_a_greenlet_writes_the_task_and_greenlet_keys_in_one_call"
    )
    link = stack.link
    link_calls = []
    seen = {}

    def recording_link(token, thread, task_id, greenlet_id, entry) -> None:
        # entry[0] is the span id. The stored keys below check the whole entry.
        link_calls.append((thread, task_id, greenlet_id, entry[0]))
        link(token, thread, task_id, greenlet_id, entry)

    async def activates_a_span() -> None:
        task_id = id(asyncio.current_task())
        greenlet_id = id(gevent.getcurrent())
        span = tracer.start_span("task", activate=False)
        # Record only this activation, not the writes of the task creation hook.
        stack.link = recording_link
        try:
            tracer.context_provider.activate(span)
        finally:
            stack.link = link
        seen["ids"] = (task_id, greenlet_id, greenlet_id in _gevent._natively_tracked)
        seen["span id"] = span.span_id
        seen["span"] = span_entry(span)
        found = _stack._span_link_entry(_stack.SPAN_LINK_DOMAIN_ASYNCIO_TASK, task_id)
        seen["task entry"] = None if found is None else found[0]
        seen["greenlet entry"] = greenlet_entry(greenlet_id)
        tracer.context_provider.activate(None)
        span.finish()

    def runs_a_loop() -> None:
        asyncio.run(asyncio.wait_for(activates_a_span(), 10))

    worker = gevent.spawn(runs_a_loop)
    worker.join(10)
    assert worker.dead
    worker.get()
    collector.stop()

    task_id, greenlet_id, greenlet_tracked = seen["ids"]
    assert greenlet_tracked
    assert link_calls == [(True, task_id, greenlet_id, seen["span id"])]
    assert seen["task entry"] == seen["span"]
    assert seen["greenlet entry"] == seen["span"]


@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_gevent_span_links_suspended",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_suspended_greenlets_on_one_thread_keep_their_own_spans() -> None:
    from gevent import monkey

    monkey.patch_all()

    import os

    import gevent
    import gevent.event

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_stack_gevent_span_links import span_ids

    def wait_in_span_a(ready, release) -> None:
        ready.set()
        release.wait(5)

    def wait_in_span_b(ready, release) -> None:
        ready.set()
        release.wait(5)

    def greenlet_a(ready, release) -> None:
        with tracer.trace("a") as span:
            spans["a"] = span.span_id
            wait_in_span_a(ready, release)

    def greenlet_b(ready, release) -> None:
        with tracer.trace("b") as span:
            spans["b"] = span.span_id
            wait_in_span_b(ready, release)

    spans = {}
    ready_a = gevent.event.Event()
    ready_b = gevent.event.Event()
    release = gevent.event.Event()

    p = profiler.Profiler(tracer=tracer)
    p.start()
    workers = [gevent.spawn(greenlet_a, ready_a, release), gevent.spawn(greenlet_b, ready_b, release)]
    assert ready_a.wait(5)
    assert ready_b.wait(5)
    # Greenlet B activated its span last, so that is the thread's span while both wait.
    gevent.sleep(0.8)
    release.set()
    gevent.joinall(workers, timeout=5, raise_error=True)
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    span_ids_a = span_ids(profile, pprof_utils.get_samples_with_function(profile, wall_samples, "wait_in_span_a"))
    span_ids_b = span_ids(profile, pprof_utils.get_samples_with_function(profile, wall_samples, "wait_in_span_b"))
    # A sample that started just before a key was written can have no span.
    assert spans["a"] in span_ids_a
    assert span_ids_a <= {spans["a"], None}
    assert spans["b"] in span_ids_b
    assert span_ids_b <= {spans["b"], None}


@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_gevent_span_links_own_or_no_span",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_greenlet_samples_get_their_own_span_or_no_span_never_the_thread_span() -> None:
    from gevent import monkey

    monkey.patch_all()

    import os

    import gevent
    import gevent.event

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_stack_gevent_span_links import busy
    from tests.profiling.collector.test_stack_gevent_span_links import span_ids

    def work_in_own_span() -> None:
        busy(0.3)

    def work_without_span() -> None:
        busy(0.3)

    def traced(thread_span_active) -> None:
        with tracer.trace("greenlet") as span:
            spans["greenlet"] = span.span_id
            thread_span_active.wait(5)
            work_in_own_span()

    def untraced(thread_span_active) -> None:
        thread_span_active.wait(5)
        work_without_span()

    spans = {}
    thread_span_active = gevent.event.Event()

    p = profiler.Profiler(tracer=tracer)
    p.start()
    # Both greenlets are spawned outside any span, so neither inherits one.
    workers = [gevent.spawn(traced, thread_span_active), gevent.spawn(untraced, thread_span_active)]
    gevent.sleep(0)
    # The main greenlet activates its span after the greenlets started, so it is the thread's span while they work.
    with tracer.trace("thread") as thread_span:
        spans["thread"] = thread_span.span_id
        thread_span_active.set()
        gevent.joinall(workers, timeout=5, raise_error=True)
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    cpu_samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    own = span_ids(profile, pprof_utils.get_samples_with_function(profile, cpu_samples, "work_in_own_span"))
    none = span_ids(profile, pprof_utils.get_samples_with_function(profile, cpu_samples, "work_without_span"))
    assert spans["greenlet"] in own
    assert own <= {spans["greenlet"], None}
    assert none == {None}


@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_gevent_span_links_inherited",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_spawned_greenlet_samples_get_the_span_it_was_spawned_in() -> None:
    from gevent import monkey

    monkey.patch_all()

    import os

    import gevent
    import gevent.event

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_stack_gevent_span_links import busy
    from tests.profiling.collector.test_stack_gevent_span_links import span_ids

    def inherited_work() -> None:
        busy(0.3)

    def child(child_running, other_span_active) -> None:
        # The gevent integration activated the span this greenlet was spawned in before this runs.
        child_running.set()
        other_span_active.wait(5)
        inherited_work()

    def spawner(child_running, other_span_active) -> None:
        with tracer.trace("spawner") as span:
            spans["spawner"] = span.span_id
            gevent.spawn(child, child_running, other_span_active).join(5)

    def other(child_running, other_span_active, release) -> None:
        child_running.wait(5)
        # The last span activated on the thread before the child works.
        with tracer.trace("other") as span:
            spans["other"] = span.span_id
            other_span_active.set()
            release.wait(5)

    spans = {}
    child_running = gevent.event.Event()
    other_span_active = gevent.event.Event()
    release = gevent.event.Event()

    p = profiler.Profiler(tracer=tracer)
    p.start()
    spawning = gevent.spawn(spawner, child_running, other_span_active)
    waiting = gevent.spawn(other, child_running, other_span_active, release)
    spawning.join(5)
    release.set()
    waiting.join(5)
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    cpu_samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    inherited = span_ids(profile, pprof_utils.get_samples_with_function(profile, cpu_samples, "inherited_work"))
    assert spans["spawner"] in inherited
    assert inherited <= {spans["spawner"], None}


@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_gevent_span_links_finished",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_spawned_greenlet_samples_never_get_a_span_that_finished_before_it_ran() -> None:
    from gevent import monkey

    monkey.patch_all()

    import os

    import gevent
    import gevent.event

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_stack_gevent_span_links import busy
    from tests.profiling.collector.test_stack_gevent_span_links import span_ids

    def work_after_child_span_finished() -> None:
        busy(0.3)

    def work_after_root_span_finished() -> None:
        busy(0.3)

    def late_greenlet(work, running, other_span_active) -> None:
        # The tracer can read the active span on a switch back into this greenlet. That read replaces the finished
        # span with its unfinished parent, or with no span, and the activation also writes the thread's span. Take
        # that switch before the other greenlet activates the thread's last span.
        gevent.sleep(0)
        running.set()
        other_span_active.wait(5)
        work()

    def hold_other_span(running, other_span_active, release) -> None:
        running.wait(5)
        # The last span activated on the thread before the late greenlet works. Samples labeled with the thread's
        # span would get this span.
        with tracer.trace("other") as span:
            spans["other"].add(span.span_id)
            other_span_active.set()
            release.wait(5)

    spans = {"other": set()}
    p = profiler.Profiler(tracer=tracer)
    p.start()

    running, other_span_active, release = gevent.event.Event(), gevent.event.Event(), gevent.event.Event()
    holder = gevent.spawn(hold_other_span, running, other_span_active, release)
    with tracer.trace("parent") as parent:
        spans["parent"] = parent.span_id
        with tracer.trace("child") as child_span:
            spans["child"] = child_span.span_id
            after_child = gevent.spawn(late_greenlet, work_after_child_span_finished, running, other_span_active)
        # The child span finished before the greenlet spawned in it runs. Its unfinished parent is still active.
        after_child.join(5)
    release.set()
    holder.join(5)

    running, other_span_active, release = gevent.event.Event(), gevent.event.Event(), gevent.event.Event()
    holder = gevent.spawn(hold_other_span, running, other_span_active, release)
    with tracer.trace("root") as root:
        spans["root"] = root.span_id
        after_root = gevent.spawn(late_greenlet, work_after_root_span_finished, running, other_span_active)
    # The root span finished before the greenlet spawned in it runs, and nothing is left to inherit.
    after_root.join(5)
    release.set()
    holder.join(5)
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    cpu_samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    child_ids = span_ids(
        profile, pprof_utils.get_samples_with_function(profile, cpu_samples, "work_after_child_span_finished")
    )
    root_ids = span_ids(
        profile, pprof_utils.get_samples_with_function(profile, cpu_samples, "work_after_root_span_finished")
    )
    assert spans["parent"] in child_ids
    assert child_ids <= {spans["parent"], None}
    assert root_ids == {None}


@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_gevent_span_links_started_late",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_greenlets_running_in_spans_before_profiling_starts_get_their_spans() -> None:
    from gevent import monkey

    monkey.patch_all()

    import os

    import gevent
    import gevent.event

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_stack_gevent_span_links import busy
    from tests.profiling.collector.test_stack_gevent_span_links import span_ids

    def work_in_span_a() -> None:
        busy(0.01)

    def work_in_span_b() -> None:
        busy(0.01)

    def greenlet_a(started, stop) -> None:
        with tracer.trace("a") as span:
            spans["a"] = span.span_id
            started.set()
            while not stop.is_set():
                work_in_span_a()
                gevent.sleep(0)

    def greenlet_b(started, stop) -> None:
        with tracer.trace("b") as span:
            spans["b"] = span.span_id
            started.set()
            while not stop.is_set():
                work_in_span_b()
                gevent.sleep(0)

    spans = {}
    started_a = gevent.event.Event()
    started_b = gevent.event.Event()
    stop = gevent.event.Event()
    # The profiler is not started, so these greenlets are not tracked when they are spawned.
    workers = [gevent.spawn(greenlet_a, started_a, stop), gevent.spawn(greenlet_b, started_b, stop)]
    assert started_a.wait(5)
    assert started_b.wait(5)

    p = profiler.Profiler(tracer=tracer)
    p.start()
    gevent.sleep(0.8)
    stop.set()
    gevent.joinall(workers, timeout=5, raise_error=True)
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    cpu_samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    span_ids_a = span_ids(profile, pprof_utils.get_samples_with_function(profile, cpu_samples, "work_in_span_a"))
    span_ids_b = span_ids(profile, pprof_utils.get_samples_with_function(profile, cpu_samples, "work_in_span_b"))
    assert spans["a"] in span_ids_a
    assert span_ids_a <= {spans["a"], None}
    assert spans["b"] in span_ids_b
    assert span_ids_b <= {spans["b"], None}


@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_gevent_span_links_restart",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_greenlet_suspended_across_a_restart_keeps_its_span() -> None:
    from gevent import monkey

    monkey.patch_all()

    import os

    import gevent
    import gevent.event

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_stack_gevent_span_links import busy
    from tests.profiling.collector.test_stack_gevent_span_links import span_ids

    def work_after_restart() -> None:
        busy(0.5)

    def worker(suspended, resume) -> None:
        with tracer.trace("greenlet") as span:
            spans["greenlet"] = span.span_id
            suspended.set()
            resume.wait(5)
            # This work does not touch the tracer, so only seeding can label it.
            work_after_restart()

    spans = {}
    suspended = gevent.event.Event()
    resume = gevent.event.Event()

    p = profiler.Profiler(tracer=tracer)
    p.start()
    worker_greenlet = gevent.spawn(worker, suspended, resume)
    assert suspended.wait(5)
    p.stop()
    p.start()
    resume.set()
    worker_greenlet.join(5)
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    cpu_samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    after_restart = span_ids(profile, pprof_utils.get_samples_with_function(profile, cpu_samples, "work_after_restart"))
    assert spans["greenlet"] in after_restart
    assert after_restart <= {spans["greenlet"], None}


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="fork test only on linux")
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_gevent_span_links_fork",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_greenlet_that_forks_keeps_its_span_in_the_child_after_a_switch() -> None:
    from gevent import monkey

    monkey.patch_all()

    import os
    import traceback

    import gevent
    import gevent.event

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_stack_gevent_span_links import busy
    from tests.profiling.collector.test_stack_gevent_span_links import span_ids

    def child_work() -> None:
        busy(0.5)

    def other_in_span(release) -> None:
        with tracer.trace("other"):
            release.wait(5)

    def fork_in_span() -> None:
        with tracer.trace("greenlet") as span:
            pid = os.fork()
            if pid == 0:
                # Tracer._child_after_fork runs before the gevent support resets its tracking state, because the
                # tracer registered its fork hook first. Its activation re-dispatch writes this greenlet's key with
                # this span. Seeding on the switch below writes the same span again.
                try:
                    release = gevent.event.Event()
                    other = gevent.spawn(other_in_span, release)
                    # The child tracks its greenlets anew. This switch tracks the forking greenlet, and runs another
                    # greenlet, which activates the thread's last span.
                    gevent.sleep(0)
                    # This work does not touch the tracer.
                    child_work()
                    release.set()
                    other.join(5)
                    p.stop()

                    profile = pprof_utils.parse_newest_profile(
                        os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
                    )
                    cpu_samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
                    child_ids = span_ids(
                        profile, pprof_utils.get_samples_with_function(profile, cpu_samples, "child_work")
                    )
                    assert span.span_id in child_ids
                    assert child_ids <= {span.span_id, None}
                except BaseException:
                    traceback.print_exc()
                    os._exit(1)
                os._exit(0)

            _, status = os.waitpid(pid, 0)
            assert os.waitstatus_to_exitcode(status) == 0

    p = profiler.Profiler(tracer=tracer)
    p.start()
    gevent.spawn(fork_in_span).get(timeout=10)
    p.stop()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="fork test only on linux")
@pytest.mark.subprocess(err=None)
def test_greenlet_suspended_when_its_thread_forks_gets_its_span_in_the_child_on_its_next_switch() -> None:
    import os
    import traceback

    import gevent
    import gevent.event

    from ddtrace.trace import tracer
    from tests.profiling.collector.test_stack_gevent_span_links import greenlet_entry
    from tests.profiling.collector.test_stack_gevent_span_links import start_stack_collector
    from tests.profiling.collector.test_stack_span_links import span_entry

    collector = start_stack_collector(
        tracer, "test_greenlet_suspended_when_its_thread_forks_gets_its_span_in_the_child_on_its_next_switch"
    )
    resume = gevent.event.Event()
    seen = {}

    def suspended_in_span() -> None:
        with tracer.trace("suspended") as span:
            seen["span"] = span_entry(span)
            resume.wait(5)
            # This greenlet activates nothing after the fork.
            seen["after resume"] = greenlet_entry()

    suspended = gevent.spawn(suspended_in_span)
    gevent.sleep(0)
    assert greenlet_entry(id(suspended)) == seen["span"]

    pid = gevent.fork()
    if pid == 0:
        try:
            # The child starts with no span link keys. The suspended greenlet gets its key back when the switch into
            # it tracks and seeds it.
            assert greenlet_entry(id(suspended)) is None
            resume.set()
            suspended.join(5)
            assert seen["after resume"] == seen["span"]
        except BaseException:
            traceback.print_exc()
            os._exit(1)
        os._exit(0)

    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    resume.set()
    suspended.join(5)
    collector.stop()
