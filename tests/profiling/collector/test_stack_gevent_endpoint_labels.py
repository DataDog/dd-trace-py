import os

import pytest


pytestmark = pytest.mark.skipif(
    not os.getenv("DD_PROFILE_TEST_GEVENT"), reason="requires the profiling gevent test environment"
)


@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_isolates_blocked_greenlet_endpoints",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_stack_isolates_concurrently_blocked_greenlet_endpoints():
    from gevent import monkey

    monkey.patch_all()

    import os

    import gevent
    from gevent import event

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    endpoint_a = "blocked-greenlet-a"
    endpoint_b = "blocked-greenlet-b"

    def wait_under_endpoint_a(ready, release):
        ready.set()
        release.wait(timeout=5)

    def wait_under_endpoint_b(ready, release):
        ready.set()
        release.wait(timeout=5)

    def greenlet_a(ready, release):
        with tracer.trace("greenlet.a.request", resource=endpoint_a, span_type=ext.SpanTypes.WEB):
            wait_under_endpoint_a(ready, release)

    def greenlet_b(ready, release):
        with tracer.trace("greenlet.b.request", resource=endpoint_b, span_type=ext.SpanTypes.WEB):
            wait_under_endpoint_b(ready, release)

    ready_a = event.Event()
    ready_b = event.Event()
    release = event.Event()

    tracer._endpoint_call_counter_span_processor.enable()
    p = profiler.Profiler(tracer=tracer)
    p.start()
    worker_a = gevent.spawn(greenlet_a, ready_a, release)
    worker_b = gevent.spawn(greenlet_b, ready_b, release)
    ready_a.wait(timeout=5)
    ready_b.wait(timeout=5)

    # Both greenlets are suspended under different endpoints on the same thread. Greenlet B activated last, so a
    # physical-thread-only link would incorrectly label Greenlet A's off-CPU samples with endpoint B.
    gevent.sleep(0.8)
    release.set()
    gevent.joinall((worker_a, worker_b), timeout=5, raise_error=True)
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    samples_a = pprof_utils.get_samples_with_function(profile, wall_samples, "wait_under_endpoint_a")
    samples_b = pprof_utils.get_samples_with_function(profile, wall_samples, "wait_under_endpoint_b")

    assert samples_a
    assert samples_b
    labels_a = {pprof_utils.get_str_label(profile, sample, "trace endpoint") for sample in samples_a}
    labels_b = {pprof_utils.get_str_label(profile, sample, "trace endpoint") for sample in samples_b}
    # Samples can be unattributed at lifecycle boundaries, but must never inherit the other greenlet's endpoint.
    assert endpoint_a in labels_a
    assert labels_a <= {None, endpoint_a}
    assert endpoint_b in labels_b
    assert labels_b <= {None, endpoint_b}


@pytest.mark.skipif(not os.sys.platform.startswith("linux"), reason="fork test only on linux")
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_preserves_greenlet_span_after_fork",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_stack_preserves_greenlet_span_after_fork():
    from gevent import monkey

    monkey.patch_all()

    import os
    import time
    import traceback

    import gevent

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    def child_work_without_switch():
        deadline = time.thread_time_ns() + 500_000_000
        while time.thread_time_ns() < deadline:
            pass

    def fork_under_active_span():
        with tracer.trace("greenlet.fork.request") as span:
            pid = os.fork()
            if pid == 0:
                try:
                    child_work_without_switch()
                    p.stop()

                    profile = pprof_utils.parse_newest_profile(
                        os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
                    )
                    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
                    samples = pprof_utils.get_samples_with_function(profile, wall_samples, "child_work_without_switch")

                    assert samples
                    span_ids = {pprof_utils.get_num_label(profile, sample, "span id") for sample in samples}
                    # pprof numeric labels are signed int64 values; normalize the tracer's uint64 span ID.
                    assert any(span_id is not None and span_id % (1 << 64) == span.span_id for span_id in span_ids)
                except BaseException:
                    traceback.print_exc()
                    os._exit(1)
                os._exit(0)

            _, status = os.waitpid(pid, 0)
            assert os.waitstatus_to_exitcode(status) == 0

    p = profiler.Profiler(tracer=tracer)
    p.start()
    gevent.spawn(fork_under_active_span).get(timeout=10)
    p.stop()


@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_preserves_greenlet_span_after_restart",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_stack_preserves_greenlet_span_after_restart():
    from gevent import monkey

    monkey.patch_all()

    import os
    import time

    import gevent
    from gevent import event

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    def work_after_restart():
        deadline = time.thread_time_ns() + 500_000_000
        while time.thread_time_ns() < deadline:
            pass

    started = event.Event()
    resume = event.Event()
    span_ids = []

    def worker():
        with tracer.trace("greenlet.restart.request") as span:
            span_ids.append(span.span_id)
            started.set()
            resume.wait(timeout=5)
            work_after_restart()

    p = profiler.Profiler(tracer=tracer)
    p.start()
    greenlet = gevent.spawn(worker)
    started.wait(timeout=5)
    p.stop()
    p.start()
    resume.set()
    greenlet.get(timeout=5)
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    samples = pprof_utils.get_samples_with_function(profile, wall_samples, "work_after_restart")

    assert samples
    sampled_span_ids = {pprof_utils.get_num_label(profile, sample, "span id") for sample in samples}
    assert any(span_id is not None and span_id % (1 << 64) == span_ids[0] for span_id in sampled_span_ids)


@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_clears_finished_inherited_greenlet_endpoint",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_stack_clears_finished_endpoint_from_inherited_greenlet():
    from gevent import monkey

    monkey.patch_all()

    import os
    import time

    import gevent
    from gevent import event

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    endpoint = "finished-parent-greenlet"

    def inherited_work_while_parent_active():
        deadline = time.thread_time_ns() + 400_000_000
        while time.thread_time_ns() < deadline:
            pass

    def work_after_parent_finished():
        deadline = time.thread_time_ns() + 400_000_000
        while time.thread_time_ns() < deadline:
            pass

    def inherited_child(started, resume):
        inherited_work_while_parent_active()
        started.set()
        resume.wait(timeout=5)
        work_after_parent_finished()

    started = event.Event()
    resume = event.Event()

    tracer._endpoint_call_counter_span_processor.enable()
    p = profiler.Profiler(tracer=tracer)
    p.start()
    with tracer.trace("greenlet.parent.request", resource=endpoint, span_type=ext.SpanTypes.WEB):
        child = gevent.spawn(inherited_child, started, resume)
        started.wait(timeout=5)

    # The child inherited the parent span when it was created, but that source has now finished. Its resumed work must
    # remain unattributed rather than borrowing either the finished endpoint or the physical thread's current link.
    resume.set()
    child.get(timeout=5)
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    inherited_samples = pprof_utils.get_samples_with_function(
        profile, wall_samples, "inherited_work_while_parent_active"
    )
    finished_samples = pprof_utils.get_samples_with_function(profile, wall_samples, "work_after_parent_finished")

    assert inherited_samples
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") == endpoint for sample in inherited_samples)
    assert finished_samples
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") is None for sample in finished_samples)
    assert all(pprof_utils.get_num_label(profile, sample, "span id") is None for sample in finished_samples)
    assert all(pprof_utils.get_num_label(profile, sample, "local root span id") is None for sample in finished_samples)


@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_gevent_import_keeps_thread_endpoint",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_stack_gevent_import_keeps_physical_thread_attribution():
    import os
    import time

    import gevent  # noqa: F401

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    endpoint = "gevent-import-thread-endpoint"

    def synchronous_work():
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            time.sleep(0.01)

    tracer._endpoint_call_counter_span_processor.enable()
    p = profiler.Profiler(tracer=tracer)
    p.start()
    with tracer.trace("sync.request", resource=endpoint, span_type=ext.SpanTypes.WEB):
        synchronous_work()
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    samples = pprof_utils.get_samples_with_function(profile, wall_samples, "synchronous_work")

    assert samples
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") == endpoint for sample in samples)
