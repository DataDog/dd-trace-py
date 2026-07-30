import os

import pytest


@pytest.mark.skipif(not os.getenv("DD_PROFILE_TEST_GEVENT"), reason="requires the profiling gevent test environment")
@pytest.mark.subprocess(
    ddtrace_run=True,
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_gevent_propagated_context",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_stack_preserves_propagated_local_root_in_gevent_greenlet():
    from gevent import monkey

    monkey.patch_all()

    import os
    import time

    import gevent

    from ddtrace._trace.context import Context
    from ddtrace.internal.datadog.profiling import context_meta
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    # A greenlet inherits a propagated tracing Context and creates a local child span. The child sample must use the
    # child's span ID while retaining the local-root ID copied from the propagated parent Context. This verifies that
    # gevent context propagation does not depend on thread-local profiler state shared by all greenlets.
    propagated_span_id = 0x101
    propagated_local_root_span_id = 0x202
    propagated = Context(trace_id=0x303, span_id=propagated_span_id)
    context_meta.attach_profiler_link(propagated, propagated_local_root_span_id, "web")

    def greenlet_child_work():
        cpu_deadline = time.thread_time_ns() + 300_000_000
        while time.thread_time_ns() < cpu_deadline:
            pass
        wall_deadline = time.monotonic() + 0.2
        while time.monotonic() < wall_deadline:
            gevent.sleep(0.01)

    def greenlet_main():
        with tracer.trace("greenlet.child") as child_span:
            greenlet_child_work()
        return child_span.span_id

    p = profiler.Profiler(tracer=tracer)
    p.start()
    tracer.context_provider.activate(propagated)
    child_span_id = gevent.spawn(greenlet_main).get(timeout=5)
    tracer.context_provider.activate(None)
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    pprof_utils.assert_profile_has_sample(
        profile,
        samples=wall_samples,
        expected_sample=pprof_utils.StackEvent(
            locations=[pprof_utils.StackLocation(function_name="greenlet_child_work", filename="", line_no=-1)],
            span_id=child_span_id,
            local_root_span_id=propagated_local_root_span_id,
        ),
        print_samples_on_failure=True,
    )
