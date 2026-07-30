import pytest


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_clears_finished_endpoint",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_stack_clears_finished_endpoint_before_untraced_work():
    import os
    import time

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    endpoint = "sync-endpoint"

    def traced_work():
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            time.sleep(0.01)

    def untraced_work():
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            time.sleep(0.01)

    tracer._endpoint_call_counter_span_processor.enable()
    p = profiler.Profiler(tracer=tracer)
    p.start()
    with tracer.trace("sync.request", resource=endpoint, span_type=ext.SpanTypes.WEB):
        traced_work()
    untraced_work()
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    traced_samples = pprof_utils.get_samples_with_function(profile, wall_samples, "traced_work")
    untraced_samples = pprof_utils.get_samples_with_function(profile, wall_samples, "untraced_work")

    assert traced_samples
    assert untraced_samples
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") == endpoint for sample in traced_samples)
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") is None for sample in untraced_samples)
    assert all(pprof_utils.get_num_label(profile, sample, "span id") is None for sample in untraced_samples)
    assert all(pprof_utils.get_num_label(profile, sample, "local root span id") is None for sample in untraced_samples)


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_restores_parent_span",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_stack_restores_parent_span_before_clearing_finished_trace():
    import os
    import time

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    endpoint = "nested-endpoint"

    def parent_work_before_child():
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            time.sleep(0.01)

    def child_work():
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            time.sleep(0.01)

    def parent_work_after_child():
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            time.sleep(0.01)

    def untraced_work_after_root():
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            time.sleep(0.01)

    tracer._endpoint_call_counter_span_processor.enable()
    p = profiler.Profiler(tracer=tracer)
    p.start()
    with tracer.trace("nested.request", resource=endpoint, span_type=ext.SpanTypes.WEB) as root_span:
        parent_work_before_child()
        with tracer.trace("nested.child") as child_span:
            child_work()
        parent_work_after_child()
    untraced_work_after_root()
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    root_span_id = pprof_utils.reinterpret_int_as_int64(root_span.span_id)
    child_span_id = pprof_utils.reinterpret_int_as_int64(child_span.span_id)

    samples_by_function = {
        function_name: pprof_utils.get_samples_with_function(profile, wall_samples, function_name)
        for function_name in (
            "parent_work_before_child",
            "child_work",
            "parent_work_after_child",
            "untraced_work_after_root",
        )
    }
    for function_name, samples in samples_by_function.items():
        assert samples, function_name

    for function_name in ("parent_work_before_child", "parent_work_after_child"):
        samples = samples_by_function[function_name]
        assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") == endpoint for sample in samples)
        assert any(pprof_utils.get_num_label(profile, sample, "span id") == root_span_id for sample in samples)
        assert all(
            pprof_utils.get_num_label(profile, sample, "local root span id") == root_span_id for sample in samples
        )

    child_samples = samples_by_function["child_work"]
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") == endpoint for sample in child_samples)
    assert any(pprof_utils.get_num_label(profile, sample, "span id") == child_span_id for sample in child_samples)
    assert all(
        pprof_utils.get_num_label(profile, sample, "local root span id") == root_span_id for sample in child_samples
    )

    untraced_samples = samples_by_function["untraced_work_after_root"]
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") is None for sample in untraced_samples)
    assert all(pprof_utils.get_num_label(profile, sample, "span id") is None for sample in untraced_samples)
    assert all(pprof_utils.get_num_label(profile, sample, "local root span id") is None for sample in untraced_samples)


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_clears_reused_worker_endpoint",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_stack_clears_finished_endpoint_on_reused_worker_thread():
    import concurrent.futures
    import os
    import threading
    import time

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    endpoint = "worker-endpoint"
    worker_thread_ids = []

    def traced_worker_work():
        worker_thread_ids.append(threading.get_ident())
        with tracer.trace("worker.request", resource=endpoint, span_type=ext.SpanTypes.WEB):
            deadline = time.monotonic() + 0.3
            while time.monotonic() < deadline:
                time.sleep(0.01)

    def untraced_worker_work():
        worker_thread_ids.append(threading.get_ident())
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            time.sleep(0.01)

    tracer._endpoint_call_counter_span_processor.enable()
    p = profiler.Profiler(tracer=tracer)
    p.start()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(traced_worker_work).result()
        executor.submit(untraced_worker_work).result()
    p.stop()

    assert len(worker_thread_ids) == 2
    assert worker_thread_ids[0] == worker_thread_ids[1]

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    traced_samples = pprof_utils.get_samples_with_function(profile, wall_samples, "traced_worker_work")
    untraced_samples = pprof_utils.get_samples_with_function(profile, wall_samples, "untraced_worker_work")

    assert traced_samples
    assert untraced_samples
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") == endpoint for sample in traced_samples)
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") is None for sample in untraced_samples)
    assert all(pprof_utils.get_num_label(profile, sample, "span id") is None for sample in untraced_samples)
    assert all(pprof_utils.get_num_label(profile, sample, "local root span id") is None for sample in untraced_samples)
