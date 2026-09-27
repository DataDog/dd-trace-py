from __future__ import annotations

import pytest


pytest.importorskip("anyio")


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_anyio_to_thread_span_links",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_anyio_to_thread_span_links() -> None:
    import os
    import threading
    import time

    import anyio

    from ddtrace import ext
    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    assert stack.is_available, stack.failure_msg

    endpoint = "anyio-worker-endpoint"
    worker_thread_ids = []
    span_ids = []

    def traced_worker_work() -> None:
        worker_thread_ids.append(threading.get_ident())
        time.sleep(0.4)

    def untraced_worker_work() -> None:
        worker_thread_ids.append(threading.get_ident())
        time.sleep(0.4)

    tracer._endpoint_call_counter_span_processor.enable()
    p = profiler.Profiler()
    p.start()

    async def main() -> None:
        with tracer.trace("anyio.request", resource=endpoint, span_type=ext.SpanTypes.WEB) as span:
            span_ids.append(span.span_id)
            await anyio.to_thread.run_sync(traced_worker_work)
        await anyio.to_thread.run_sync(untraced_worker_work)

    anyio.run(main)
    p.stop()

    assert len(worker_thread_ids) == 2
    assert worker_thread_ids[0] == worker_thread_ids[1]

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    traced_samples = pprof_utils.get_samples_with_function(profile, wall_samples, "traced_worker_work")
    untraced_samples = pprof_utils.get_samples_with_function(profile, wall_samples, "untraced_worker_work")

    assert traced_samples
    assert untraced_samples
    assert len(span_ids) == 1
    span_id = pprof_utils.reinterpret_int_as_int64(span_ids[0])
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") == endpoint for sample in traced_samples)
    assert all(pprof_utils.get_num_label(profile, sample, "span id") == span_id for sample in traced_samples)
    assert all(pprof_utils.get_num_label(profile, sample, "local root span id") == span_id for sample in traced_samples)
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") is None for sample in untraced_samples)
    assert all(pprof_utils.get_num_label(profile, sample, "span id") is None for sample in untraced_samples)
