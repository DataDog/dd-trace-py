"""Tests for thread reservoir-sampling cap (max_threads_per_sample).

The sampler's reservoir sampling logic caps the number of threads sampled per
cycle to `max_threads`.  These tests verify the cap is respected by reading
`sample_count` and `sampling_event_count` from the internal metadata JSON that
ddup writes alongside each pprof file.
"""

import pytest


@pytest.mark.subprocess(
    env=dict(
        _DD_PROFILING_STACK_MAX_THREADS="1",
        _DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED="0",
    ),
)
def test_thread_subsampling_cap_respected() -> None:
    """With max_threads=1, at most 1 thread is sampled per cycle, even with many threads alive."""
    import threading
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.utils import get_all_metadata_from_agent
    from tests.profiling.utils import with_profiling_test_agent

    N_THREADS = 10
    max_threads = 1

    stop_event = threading.Event()

    def worker() -> None:
        while not stop_event.is_set():
            time.sleep(0.01)

    test_name = "test_thread_subsampling_cap"

    with with_profiling_test_agent() as agent_client:
        assert ddup.is_available
        ddup.config(env="test", service=test_name, version="my_version")
        ddup.start()
        ddup.upload()  # flush initial empty state; resets stats counters

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(N_THREADS)]
        for t in threads:
            t.start()

        with stack.StackCollector():
            time.sleep(2)

        stop_event.set()
        for t in threads:
            t.join(timeout=2)

        ddup.upload()

        files = get_all_metadata_from_agent(agent_client, min_count=1)

    assert files, "No internal metadata received from test agent"

    for metadata in files:
        sampling_event_count: int = metadata.get("sampling_event_count", 0)
        sample_count: int = metadata.get("sample_count", 0)

        if sampling_event_count == 0:
            continue  # empty upload window; skip

        assert sample_count <= sampling_event_count * max_threads, (
            f"Reservoir sampling cap violated: sample_count={sample_count} "
            f"> sampling_event_count={sampling_event_count} * max_threads={max_threads}. "
            f"Full metadata: {metadata}"
        )


@pytest.mark.subprocess(
    env=dict(
        _DD_PROFILING_STACK_MAX_THREADS="0",  # 0 = unlimited
        _DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED="0",
    ),
)
def test_thread_subsampling_all_threads_sampled_without_cap() -> None:
    """Without a cap (max_threads=0), all threads are sampled each cycle.

    With N_THREADS additional threads running, sample_count should be
    significantly greater than sampling_event_count.
    """
    import threading
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.utils import get_all_metadata_from_agent
    from tests.profiling.utils import with_profiling_test_agent

    N_THREADS = 10

    stop_event = threading.Event()

    def worker() -> None:
        while not stop_event.is_set():
            time.sleep(0.01)

    test_name = "test_thread_subsampling_nocap"

    with with_profiling_test_agent() as agent_client:
        assert ddup.is_available
        ddup.config(env="test", service=test_name, version="my_version")
        ddup.start()
        ddup.upload()  # flush initial empty state; resets stats counters

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(N_THREADS)]
        for t in threads:
            t.start()

        with stack.StackCollector():
            time.sleep(2)

        stop_event.set()
        for t in threads:
            t.join(timeout=2)

        ddup.upload()

        files = get_all_metadata_from_agent(agent_client, min_count=1)

    total_events: int = 0
    total_samples: int = 0
    for metadata in files:
        total_events += metadata.get("sampling_event_count", 0)
        total_samples += metadata.get("sample_count", 0)

    assert total_events > 0, "Expected at least one sampling event"
    # With N_THREADS + 1 (main) threads and no cap, sample_count should be much
    # larger than event_count.  We only assert > 1x as a conservative lower bound.
    assert total_samples > total_events, (
        f"Expected sample_count > sampling_event_count without a thread cap, "
        f"got total_samples={total_samples}, total_events={total_events}"
    )
