"""
Tests for profiler span linkage in thread pool workers.
"""

from __future__ import annotations

import pytest

from ddtrace.internal.datadog.profiling import stack as stack_module


def test_link_span_plain_context_uses_span_id_as_local_root() -> None:
    """Context activation without profiler _meta must link using span_id as local root."""
    from ddtrace._trace.context import Context

    if not stack_module.is_available:
        pytest.skip("stack profiler not available")

    ctx = Context(trace_id=123, span_id=456)
    stack_module.link_span(ctx)


def test_link_span_context_reads_profiler_meta() -> None:
    """Context._meta profiler keys supply full local root and span type linkage."""
    from ddtrace._trace.context import Context
    from ddtrace.internal.datadog.profiling import context_meta

    if not stack_module.is_available:
        pytest.skip("stack profiler not available")

    ctx = Context(trace_id=123, span_id=456)
    context_meta.attach_profiler_link(ctx, local_root_span_id=789, span_type="web")
    stack_module.link_span(ctx)


# The two tests below use ddup.config()/ddup.upload() directly.
# They run as @pytest.mark.subprocess to avoid libdatadog's tokio runtime
# thread-affinity constraint, which conflicts with pytest's fixture threading.
@pytest.mark.subprocess(err=None)
def test_threadpool_worker_with_explicit_child_span() -> None:
    """A worker that creates an explicit child span should have its profiler
    samples linked to the child span's IDs.
    """
    import concurrent.futures
    import time
    import uuid

    from ddtrace import ext
    from ddtrace.contrib.internal.futures.patch import patch as futures_patch
    from ddtrace.contrib.internal.futures.patch import unpatch as futures_unpatch
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils
    from tests.profiling.utils import with_profiling_test_agent

    futures_patch()
    try:
        test_name = "test_threadpool_worker_with_explicit_child_span"

        tracer._endpoint_call_counter_span_processor.enable()

        assert ddup.is_available
        with with_profiling_test_agent() as agent_client:
            ddup.config(env="test", service=test_name, version="my_version")
            ddup.start()
            ddup.upload()

            resource = str(uuid.uuid4())
            span_type = ext.SpanTypes.WEB

            child_span_ids: list[int] = []

            def worker_with_span() -> None:
                with tracer.trace("worker.query", resource=resource, span_type=span_type) as child_span:
                    child_span_ids.append(child_span.span_id)
                    for _ in range(20):
                        time.sleep(0.1)

            with stack.StackCollector(tracer=tracer):
                with tracer.trace("executing_queries", resource=resource, span_type=span_type) as parent_span:
                    local_root_span_id = parent_span._local_root.span_id
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                        executor.submit(worker_with_span).result()

            ddup.upload(tracer=tracer)

            assert child_span_ids, "worker_with_span() did not run"
            child_span_id = child_span_ids[0]

            profile = pprof_utils.get_profile_from_agent(agent_client)

            all_span_samples = pprof_utils.get_samples_with_label_key(profile, "span id")
            assert all_span_samples, "Profile has no samples with a 'span id' label — is the StackCollector running?"

            pprof_utils.assert_profile_has_sample(
                profile,
                samples=all_span_samples,
                expected_sample=pprof_utils.StackEvent(
                    span_id=child_span_id,
                    local_root_span_id=local_root_span_id,
                    trace_type=span_type,
                ),
                print_samples_on_failure=True,
            )
    finally:
        futures_unpatch()


@pytest.mark.subprocess(err=None)
def test_threadpool_worker_context_propagated_not_linked_to_profiler() -> None:
    """Worker thread profiler samples should carry the parent span's span_id when
    the futures integration propagates the parent Context (no explicit child span).
    """
    import concurrent.futures
    import time
    import uuid

    from ddtrace import ext
    from ddtrace.contrib.internal.futures.patch import patch as futures_patch
    from ddtrace.contrib.internal.futures.patch import unpatch as futures_unpatch
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils
    from tests.profiling.utils import with_profiling_test_agent

    def _get_threadpool_samples(profile):
        result = []
        for sample in profile.sample:
            label = pprof_utils.get_label_with_key(profile.string_table, sample, "thread name")
            if label is not None and "ThreadPoolExecutor" in profile.string_table[label.str]:
                result.append(sample)
        return result

    futures_patch()
    try:
        test_name = "test_threadpool_worker_context_propagated_not_linked"

        tracer._endpoint_call_counter_span_processor.enable()

        assert ddup.is_available
        with with_profiling_test_agent() as agent_client:
            ddup.config(env="test", service=test_name, version="my_version")
            ddup.start()
            ddup.upload()

            resource = str(uuid.uuid4())
            span_type = ext.SpanTypes.WEB

            def worker_context_only() -> None:
                for _ in range(20):
                    time.sleep(0.1)

            with stack.StackCollector(tracer=tracer):
                with tracer.trace("executing_queries", resource=resource, span_type=span_type) as parent_span:
                    parent_span_id = parent_span.span_id
                    local_root_span_id = parent_span._local_root.span_id
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                        executor.submit(worker_context_only).result()

            ddup.upload(tracer=tracer)

            profile = pprof_utils.get_profile_from_agent(agent_client)

            all_worker_samples = _get_threadpool_samples(profile)
            assert all_worker_samples, "No samples found from ThreadPoolExecutor worker threads."

            worker_samples_with_span_id = [
                s
                for s in all_worker_samples
                if pprof_utils.get_label_with_key(profile.string_table, s, "span id") is not None
            ]
            assert worker_samples_with_span_id, "Worker thread samples have no 'span id' label."

            pprof_utils.assert_profile_has_sample(
                profile,
                samples=worker_samples_with_span_id,
                expected_sample=pprof_utils.StackEvent(
                    span_id=parent_span_id,
                    local_root_span_id=local_root_span_id,
                    trace_type=span_type,
                ),
                print_samples_on_failure=True,
            )
    finally:
        futures_unpatch()
