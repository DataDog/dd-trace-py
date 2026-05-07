from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from threading import Event

import mlflow
from mlflow.tracking import fluent as mlflow_fluent
import pytest

from ddtrace.contrib.internal.futures.patch import patch as futures_patch
from ddtrace.contrib.internal.futures.patch import unpatch as futures_unpatch


SNAPSHOT_IGNORES = ["meta.mlflow.run_id", "meta._dd.hostname"]


def _has_thread_local_active_run_stack():
    active_run_stack = getattr(mlflow_fluent, "_active_run_stack", None)
    return hasattr(active_run_stack, "get")


def _wait_for_ordered_step(ordering_events, step_order):
    assert ordering_events[step_order].wait(timeout=5), "timed out waiting for worker scheduling"
    ordering_events[step_order + 1].set()


@pytest.fixture(autouse=True)
def patch_futures():
    # Required so tracing context propagates into worker threads.
    futures_patch()
    try:
        yield
    finally:
        futures_unpatch()


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_mlflow_multithreaded_steps_on_the_same_run(
    test_spans, assert_run_id_on_all_spans, assert_hostname_on_all_spans
):
    """Test multithreading when each thread creates new step on the same run"""
    ordering_events = [Event() for _ in range(5)]
    ordering_events[0].set()

    def _worker(worker_id, shared_run_id):
        for step in range(2):
            step_order = worker_id * 2 + step
            _wait_for_ordered_step(ordering_events, step_order)
            mlflow.log_metric("loss_%d" % worker_id, worker_id + step, step=step_order, run_id=shared_run_id)

    with mlflow.start_run() as run:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_worker, worker_id, run.info.run_id) for worker_id in range(2)]
            for future in as_completed(futures):
                future.result()

    assert_run_id_on_all_spans(test_spans)
    assert_hostname_on_all_spans(test_spans)


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
@pytest.mark.subprocess(err=None)
@pytest.mark.skipif(
    not _has_thread_local_active_run_stack(),
    reason="requires MLflow thread-local active run stack",
)
def test_mlflow_multithreaded_steps_without_explicit_run_id_thread_local():
    """Tests worker threads implicitly create their own runs when run stack is thread-local.

    Run this case in a subprocess so implicit thread-local runs finish at process
    exit and are available to snapshot assertions.
    """
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import as_completed
    from threading import Event

    import mlflow

    from ddtrace import patch

    patch(mlflow=True, futures=True)

    def _wait_for_ordered_step(ordering_events, step_order):
        assert ordering_events[step_order].wait(timeout=5), "timed out waiting for worker scheduling"
        ordering_events[step_order + 1].set()

    ordering_events = [Event() for _ in range(5)]
    ordering_events[0].set()

    def _worker(worker_id):
        # Log metrics without passing run_id so MLflow will create a thread-local run
        for step in range(2):
            step_order = worker_id * 2 + step
            _wait_for_ordered_step(ordering_events, step_order)
            # mlflow will automatically create a new run
            mlflow.log_metric("loss_%d" % worker_id, worker_id + step, step=step_order)

    with mlflow.start_run():
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_worker, worker_id) for worker_id in range(2)]
            for future in as_completed(futures):
                future.result()


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
@pytest.mark.subprocess(err=None)
@pytest.mark.skipif(
    _has_thread_local_active_run_stack(),
    reason="applies only to MLflow versions with process-global active run stack",
)
def test_mlflow_multithreaded_steps_without_explicit_run_id_global_stack():
    """Tests worker threads keep logging on the active parent run on old MLflow."""
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import as_completed
    from threading import Event

    import mlflow

    from ddtrace import patch

    patch(mlflow=True, futures=True)

    def _wait_for_ordered_step(ordering_events, step_order):
        assert ordering_events[step_order].wait(timeout=5), "timed out waiting for worker scheduling"
        ordering_events[step_order + 1].set()

    ordering_events = [Event() for _ in range(5)]
    ordering_events[0].set()

    def _worker(worker_id):
        for step in range(2):
            step_order = worker_id * 2 + step
            _wait_for_ordered_step(ordering_events, step_order)
            mlflow.log_metric("loss_%d" % worker_id, worker_id + step, step=step_order)

    with mlflow.start_run():
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_worker, worker_id) for worker_id in range(2)]
            for future in as_completed(futures):
                future.result()


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_mlflow_multithreaded_steps_specifying_new_run(
    test_spans, assert_run_id_on_all_spans, assert_hostname_on_all_spans
):
    """Tests each worker creates and logs to its own explicitly opened run."""
    ordering_events = [Event() for _ in range(5)]
    ordering_events[0].set()

    def _worker(worker_id):
        if not _has_thread_local_active_run_stack():
            # when the run are not thread local, any run can be the active one
            with mlflow.start_run(run_name=f"worker-{worker_id}", nested=True) as run:
                for step in range(2):
                    step_order = worker_id * 2 + step
                    _wait_for_ordered_step(ordering_events, step_order)
                    # We have to specify run_id here
                    mlflow.log_metric("loss_%d" % worker_id, worker_id + step, run_id=run.info.run_id, step=step_order)
        else:
            with mlflow.start_run(run_name=f"worker-{worker_id}"):
                for step in range(2):
                    step_order = worker_id * 2 + step
                    _wait_for_ordered_step(ordering_events, step_order)
                    mlflow.log_metric("loss_%d" % worker_id, worker_id + step, step=step_order)

    with mlflow.start_run():
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_worker, worker_id) for worker_id in range(2)]
            for future in as_completed(futures):
                future.result()

    assert_run_id_on_all_spans(test_spans)
    assert_hostname_on_all_spans(test_spans)


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
@pytest.mark.skipif(
    not _has_thread_local_active_run_stack(),
    reason="requires MLflow thread-local active run stack",
)
def test_mlflow_multithreaded_steps_specifying_same_run(
    test_spans, assert_run_id_on_all_spans, assert_hostname_on_all_spans
):
    """Tests cross-thread reuse of one run id.
    We are not properly supporting this case for now but we ensure we are not breaking anything or
    leaking any data
    """
    ordering_events = [Event() for _ in range(5)]
    ordering_events[0].set()

    def _worker(worker_id, run_id):
        with mlflow.start_run(run_name="worker-%d" % worker_id, run_id=run_id):
            for step in range(2):
                step_order = worker_id * 2 + step
                _wait_for_ordered_step(ordering_events, step_order)
                mlflow.log_metric("loss_%d" % worker_id, worker_id + step, step=step_order)

    with mlflow.start_run() as run:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_worker, worker_id, run.info.run_id) for worker_id in range(2)]
            for future in as_completed(futures):
                future.result()

    assert_run_id_on_all_spans(test_spans)
    assert_hostname_on_all_spans(test_spans)
