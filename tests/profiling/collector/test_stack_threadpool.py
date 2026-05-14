"""
Tests for profiler span linkage in thread pool workers.
"""

from __future__ import annotations

import concurrent.futures
import os
from pathlib import Path
import time
from typing import TYPE_CHECKING
import uuid

import pytest

from ddtrace import ext
from ddtrace.contrib.internal.futures.patch import patch as futures_patch
from ddtrace.contrib.internal.futures.patch import unpatch as futures_unpatch
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling.collector import stack
from ddtrace.trace import Tracer
from tests.profiling.collector import pprof_utils


if TYPE_CHECKING:
    from tests.profiling.collector.pprof_pb2 import Sample  # pyright: ignore[reportMissingModuleSource]


@pytest.fixture(autouse=True)
def patch_futures():
    futures_patch()
    try:
        yield
    finally:
        futures_unpatch()


def _get_threadpool_samples(profile) -> list[Sample]:
    """
    Return all samples from ThreadPoolExecutor worker threads.
    """
    result = []
    for sample in profile.sample:
        label = pprof_utils.get_label_with_key(profile.string_table, sample, "thread name")
        if label is not None and "ThreadPoolExecutor" in profile.string_table[label.str]:
            result.append(sample)
    return result


def test_threadpool_worker_with_explicit_child_span(tmp_path: Path, tracer: Tracer) -> None:
    """
    A worker that creates an explicit child span should have its profiler
    samples linked to the child span's IDs.
    """
    test_name = "test_threadpool_worker_with_explicit_child_span"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    tracer._endpoint_call_counter_span_processor.enable()

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
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

    profile = pprof_utils.parse_newest_profile(output_filename)

    # All span-tagged samples in the profile. child_span_id is unique to the
    # worker thread so any hit here proves the worker is linked correctly.
    all_span_samples = pprof_utils.get_samples_with_label_key(profile, "span id")
    assert all_span_samples, (
        "Profile has no samples with a 'span id' label — is the StackCollector running and the tracer connected?"
    )

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


def test_threadpool_worker_context_propagated_not_linked_to_profiler(tmp_path: Path, tracer: Tracer) -> None:
    """
    Worker thread profiler samples should carry the parent span's span_id when
    the futures integration propagates the parent Context (no explicit child span).
    """
    test_name = "test_threadpool_worker_context_propagated_not_linked"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    tracer._endpoint_call_counter_span_processor.enable()

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    resource = str(uuid.uuid4())
    span_type = ext.SpanTypes.WEB

    def worker_context_only() -> None:
        # no tracer.trace() call here; only the propagated Context is active
        for _ in range(20):
            time.sleep(0.1)

    with stack.StackCollector(tracer=tracer):
        with tracer.trace("executing_queries", resource=resource, span_type=span_type) as parent_span:
            parent_span_id = parent_span.span_id
            local_root_span_id = parent_span._local_root.span_id
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(worker_context_only).result()

    ddup.upload(tracer=tracer)

    profile = pprof_utils.parse_newest_profile(output_filename)

    # Check that the worker was actually sampled by looking for ThreadPoolExecutor
    # thread-name labels
    all_worker_samples = _get_threadpool_samples(profile)
    assert all_worker_samples, (
        "No samples found from ThreadPoolExecutor worker threads "
        "(looked for 'thread name' label containing 'ThreadPoolExecutor'). "
        "The worker may not have run long enough or the profiler may not record "
        "thread names for executor threads. Increase sleep iterations if needed."
    )

    worker_samples_with_span_id = [
        s for s in all_worker_samples if pprof_utils.get_label_with_key(profile.string_table, s, "span id") is not None
    ]
    assert worker_samples_with_span_id, (
        "Worker thread samples have no 'span id' label — the profiler did not link "
        "the worker thread to the parent span even though the futures integration "
        "propagated the trace Context via tracer._activate_context()."
    )

    # _wrap_submit attaches _local_root_span_id and _span_type
    # to the Context, so the worker gets the full parent span info including
    # trace_type.
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
