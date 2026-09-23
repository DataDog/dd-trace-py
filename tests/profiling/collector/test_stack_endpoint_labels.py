import pytest


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
        time.sleep(0.3)

    def child_work():
        time.sleep(0.3)

    def parent_work_after_child():
        time.sleep(0.3)

    def untraced_work_after_root():
        time.sleep(0.3)

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

    def traced_worker_body():
        time.sleep(0.3)

    def traced_worker_work():
        worker_thread_ids.append(threading.get_ident())
        with tracer.trace("worker.request", resource=endpoint, span_type=ext.SpanTypes.WEB):
            traced_worker_body()

    def untraced_worker_work():
        worker_thread_ids.append(threading.get_ident())
        time.sleep(0.3)

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
    traced_samples = pprof_utils.get_samples_with_function(profile, wall_samples, "traced_worker_body")
    untraced_samples = pprof_utils.get_samples_with_function(profile, wall_samples, "untraced_worker_work")

    assert traced_samples
    assert untraced_samples
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") == endpoint for sample in traced_samples)
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") is None for sample in untraced_samples)
    assert all(pprof_utils.get_num_label(profile, sample, "span id") is None for sample in untraced_samples)
    assert all(pprof_utils.get_num_label(profile, sample, "local root span id") is None for sample in untraced_samples)


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_clears_cross_thread_finished_span",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_stack_clears_span_finished_on_another_thread():
    import os
    import threading
    import time

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    endpoint = "cross-thread-endpoint"
    worker_ready = threading.Event()
    span_finished = threading.Event()

    def worker_before_finish():
        time.sleep(0.3)

    def worker_after_finish():
        time.sleep(0.3)

    def worker(span):
        tracer.context_provider.activate(span)
        worker_before_finish()
        worker_ready.set()
        assert span_finished.wait(5)
        worker_after_finish()
        tracer.context_provider.activate(None)

    tracer._endpoint_call_counter_span_processor.enable()
    p = profiler.Profiler(tracer=tracer)
    p.start()
    span = tracer.trace("cross-thread.request", resource=endpoint, span_type=ext.SpanTypes.WEB)
    thread = threading.Thread(target=worker, args=(span,))
    thread.start()
    assert worker_ready.wait(5)
    span.finish()
    span_finished.set()
    thread.join()
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    before_samples = pprof_utils.get_samples_with_function(profile, wall_samples, "worker_before_finish")
    after_samples = pprof_utils.get_samples_with_function(profile, wall_samples, "worker_after_finish")

    assert before_samples
    assert after_samples
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") == endpoint for sample in before_samples)
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") is None for sample in after_samples)
    assert all(pprof_utils.get_num_label(profile, sample, "span id") is None for sample in after_samples)
    assert all(pprof_utils.get_num_label(profile, sample, "local root span id") is None for sample in after_samples)


@pytest.mark.subprocess(
    env={
        "DD_PROFILING_OUTPUT_PPROF": "/tmp/test_stack_preserves_child_after_root_finish",
        "_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED": "0",
    },
    err=None,
)
def test_stack_preserves_active_child_when_local_root_finishes_first():
    import os
    import threading
    import time

    from ddtrace import ext
    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils

    endpoint = "out-of-order-finish-endpoint"
    worker_ready = threading.Event()
    root_finished = threading.Event()

    def worker_after_root_finish():
        time.sleep(0.3)

    def worker(child):
        tracer.context_provider.activate(child)
        worker_ready.set()
        assert root_finished.wait(5)
        worker_after_root_finish()
        tracer.context_provider.activate(None)

    tracer._endpoint_call_counter_span_processor.enable()
    p = profiler.Profiler(tracer=tracer)
    p.start()
    root = tracer.trace("out-of-order.request", resource=endpoint, span_type=ext.SpanTypes.WEB)
    child = tracer.trace("out-of-order.child")
    root_span_id = pprof_utils.reinterpret_int_as_int64(root.span_id)
    child_span_id = pprof_utils.reinterpret_int_as_int64(child.span_id)

    thread = threading.Thread(target=worker, args=(child,))
    thread.start()
    assert worker_ready.wait(5)
    root.finish()
    root_finished.set()
    thread.join(5)
    assert not thread.is_alive()
    child.finish()
    p.stop()

    profile = pprof_utils.parse_newest_profile(os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid()))
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    child_samples = pprof_utils.get_samples_with_function(profile, wall_samples, "worker_after_root_finish")

    assert child_samples
    assert all(pprof_utils.get_str_label(profile, sample, "trace endpoint") == endpoint for sample in child_samples)
    assert all(pprof_utils.get_num_label(profile, sample, "span id") == child_span_id for sample in child_samples)
    assert all(
        pprof_utils.get_num_label(profile, sample, "local root span id") == root_span_id for sample in child_samples
    )
