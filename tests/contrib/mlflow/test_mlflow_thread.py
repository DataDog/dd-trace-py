from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from time import sleep

import mlflow
import pytest

from ddtrace.contrib.internal.futures.patch import patch as futures_patch
from ddtrace.contrib.internal.futures.patch import unpatch as futures_unpatch


SNAPSHOT_IGNORES = ["meta.mlflow.run_id"]


@pytest.fixture(autouse=True)
def patch_futures():
    # Required so tracing context propagates into worker threads.
    futures_patch()
    try:
        yield
    finally:
        futures_unpatch()


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_mlflow_multithreaded_steps_on_the_same_run(test_spans, assert_run_id_on_all_spans):
    """Test multithreading when each thread creates new step on the same run"""

    def _worker(worker_id, shared_run_id):
        for step in range(2):
            # ensure steps are always in the same order in the snapshots
            sleep((worker_id * 2 + step) * 0.8)
            mlflow.log_metric("loss_%d" % worker_id, worker_id + step, step=worker_id * 2 + step, run_id=shared_run_id)

    with mlflow.start_run() as run:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_worker, worker_id, run.info.run_id) for worker_id in range(2)]
            for future in as_completed(futures):
                future.result()

    assert_run_id_on_all_spans(test_spans)


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
@pytest.mark.subprocess(err=None)
def test_mlflow_multithreaded_steps_without_creating_new_run():
    """Tests that a new run is automatically created when logging a new step in a new thread

    Run this case in a subprocess so implicit thread-local runs finish at process
    exit and are available to snapshot assertions.
    """
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import as_completed
    from time import sleep

    import mlflow

    from ddtrace import patch

    patch(mlflow=True, futures=True)

    def _worker(worker_id):
        # Log metrics without passing run_id so MLflow will create a thread-local run
        for step in range(2):
            # ensure steps are always in the same order in the snapshots
            sleep((worker_id * 2 + step) * 0.8)
            # mlflow will automatically create a new run
            mlflow.log_metric("loss_%d" % worker_id, worker_id + step, step=worker_id * 2 + step)

    with mlflow.start_run():
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_worker, worker_id) for worker_id in range(2)]
            for future in as_completed(futures):
                future.result()


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_mlflow_multithreaded_steps_specifying_new_run(test_spans, assert_run_id_on_all_spans):
    """Tests each worker creates and logs to its own explicitly opened run."""

    def _worker(worker_id):
        with mlflow.start_run(run_name="worker-%d" % worker_id):
            for step in range(2):
                # ensure steps are always in the same order in the snapshots
                sleep((worker_id * 2 + step) * 0.8)
                mlflow.log_metric("loss_%d" % worker_id, worker_id + step, step=worker_id * 2 + step)

    with mlflow.start_run():
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_worker, worker_id) for worker_id in range(2)]
            for future in as_completed(futures):
                future.result()

    assert_run_id_on_all_spans(test_spans)


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_mlflow_multithreaded_steps_specifying_same_run(test_spans, assert_run_id_on_all_spans):
    """Tests cross-thread reuse of one run id.
    We are not properly supporting this case for now but we ensure we are not breaking anything or
    leaking any data
    """

    def _worker(worker_id, run_id):
        with mlflow.start_run(run_name="worker-%d" % worker_id, run_id=run_id):
            for step in range(2):
                # ensure steps are always in the same order in the snapshots
                sleep((worker_id * 2 + step) * 0.8)
                mlflow.log_metric("loss_%d" % worker_id, worker_id + step, step=worker_id * 2 + step)

    with mlflow.start_run() as run:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_worker, worker_id, run.info.run_id) for worker_id in range(2)]
            for future in as_completed(futures):
                future.result()

    assert_run_id_on_all_spans(test_spans)
