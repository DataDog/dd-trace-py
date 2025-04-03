import os
import subprocess
import time

import pytest
import redis
import rq

from ddtrace.contrib.internal.rq.patch import get_version
from ddtrace.contrib.internal.rq.patch import patch
from ddtrace.contrib.internal.rq.patch import unpatch
from ddtrace.trace import Pin
from tests.contrib.patch import emit_integration_and_version_to_test_agent
from tests.utils import override_config
from tests.utils import snapshot
from tests.utils import snapshot_context

from ..config import REDIS_CONFIG
from .jobs import JobClass
from .jobs import MyException
from .jobs import job_add1
from .jobs import job_fail


# Span data which isn't static to ignore in the snapshots.
snapshot_ignores = ["meta.job.id", "meta.error.stack", "meta.traceparent", "meta.tracestate"]

rq_version = tuple(int(x) for x in rq.__version__.split(".")[:3])


@pytest.fixture()
def connection():
    yield redis.Redis(port=REDIS_CONFIG["port"])


@pytest.fixture()
def queue(connection):
    patch()
    try:
        q = rq.Queue("q", connection=connection)
        yield q
    finally:
        unpatch()


@pytest.fixture()
def sync_queue(connection):
    patch()
    try:
        sync_q = rq.Queue("sync-q", is_async=False, connection=connection)
        yield sync_q
    finally:
        unpatch()


@snapshot(ignores=snapshot_ignores)
def test_sync_queue_enqueue(sync_queue):
    sync_queue.enqueue(job_add1, 1)


def test_and_implement_get_version():
    version = get_version()
    assert type(version) == str
    assert version != ""

    emit_integration_and_version_to_test_agent("rq", version)


@snapshot(ignores=snapshot_ignores, variants={"": rq_version >= (1, 10, 1), "pre_1_10_1": rq_version < (1, 10, 1)})
def test_queue_failing_job(sync_queue):
    # Exception raising behavior was changed in 1.10.1
    # https://github.com/rq/rq/commit/93f34c796f541ea4b1c156426d6524df05753826
    if rq_version >= (1, 10, 1):
        sync_queue.enqueue(job_fail)
        return

    with pytest.raises(MyException):
        sync_queue.enqueue(job_fail)


@snapshot(ignores=snapshot_ignores)
def test_sync_worker(queue):
    job = queue.enqueue(job_add1, 1)
    worker = rq.SimpleWorker([queue], connection=queue.connection)
    worker.work(burst=True)
    assert job.result == 2


@snapshot(ignores=snapshot_ignores)
def test_sync_worker_ttl(queue):
    # queue a job where the result expires immediately
    job = queue.enqueue(job_add1, 1, result_ttl=0)
    worker = rq.SimpleWorker([queue], connection=queue.connection)
    worker.work(burst=True)
    assert job.get_status() is None
    assert job.result is None


@snapshot(ignores=snapshot_ignores)
def test_sync_worker_multiple_jobs(queue):
    jobs = []
    for i in range(3):
        jobs.append(queue.enqueue(job_add1, i))
    worker = rq.SimpleWorker([queue], connection=queue.connection)
    worker.work(burst=True)
    assert [job.result for job in jobs] == [1, 2, 3]


@snapshot(ignores=snapshot_ignores)
def test_sync_worker_config_service(queue):
    job = queue.enqueue(job_add1, 10)
    with override_config("rq_worker", dict(service="my-worker-svc")):
        worker = rq.SimpleWorker([queue], connection=queue.connection)
        worker.work(burst=True)
    assert job.result == 11


@snapshot(ignores=snapshot_ignores)
def test_queue_pin_service(queue):
    Pin._override(queue, service="my-pin-svc")
    job = queue.enqueue(job_add1, 10)
    worker = rq.SimpleWorker([queue], connection=queue.connection)
    worker.work(burst=True)
    assert job.result == 11


@snapshot(ignores=snapshot_ignores)
def test_sync_worker_pin_service(queue):
    job = queue.enqueue(job_add1, 10)
    worker = rq.SimpleWorker([queue], connection=queue.connection)
    Pin._override(worker, service="my-pin-svc")
    worker.work(burst=True)
    assert job.result == 11


@snapshot(ignores=snapshot_ignores)
def test_worker_failing_job(queue):
    queue.enqueue(job_fail)
    worker = rq.SimpleWorker([queue], connection=queue.connection)
    worker.work(burst=True)


@snapshot(ignores=snapshot_ignores)
def test_worker_class_job(queue):
    queue.enqueue(JobClass().job_on_class, 2)
    queue.enqueue(JobClass(), 4)
    worker = rq.SimpleWorker([queue], connection=queue.connection)
    worker.work(burst=True)


@pytest.mark.parametrize("distributed_tracing_enabled", [False, None])
@pytest.mark.parametrize("worker_service_name", [None, "custom-worker-service"])
def test_enqueue(queue, distributed_tracing_enabled, worker_service_name):
    token = "tests.contrib.rq.test_rq.test_enqueue_distributed_tracing_enabled_%s_worker_service_%s" % (
        distributed_tracing_enabled,
        worker_service_name,
    )
    num_traces_expected = 2 if distributed_tracing_enabled is False else 1
    with snapshot_context(token, ignores=snapshot_ignores, wait_for_num_traces=num_traces_expected):
        env = os.environ.copy()
        env["DD_TRACE_REDIS_ENABLED"] = "false"
        if distributed_tracing_enabled is not None:
            env["DD_RQ_DISTRIBUTED_TRACING_ENABLED"] = str(distributed_tracing_enabled)
        if worker_service_name is not None:
            env["DD_SERVICE"] = "custom-worker-service"
        p = subprocess.Popen(["ddtrace-run", "rq", "worker", "q"], env=env)
        try:
            job = queue.enqueue(job_add1, 1)
            # Wait for job to complete
            for _ in range(100):
                if job.result is not None:
                    break
                time.sleep(0.1)
            assert job.result == 2
        finally:
            p.terminate()
            # Wait for trace to be sent
            time.sleep(0.5)


@pytest.mark.snapshot(
    ignores=snapshot_ignores + ["meta.error.message", "meta.error.type"],
    variants={
        "": rq_version >= (1, 10, 1),  # Exception handling changed in 1.10.1
        "pre_1_10_1": rq_version < (1, 10, 1),
    },
)
@pytest.mark.parametrize(
    "service_schema",
    [
        (None, None),
        (None, "v0"),
        (None, "v1"),
        ("mysvc", None),
        ("mysvc", "v0"),
        ("mysvc", "v1"),
    ],
)
def test_schematization(ddtrace_run_python_code_in_subprocess, service_schema):
    service, schema = service_schema
    code = """
import pytest
import rq
import sys
from tests.contrib.rq.test_rq import queue
from tests.contrib.rq.test_rq import connection
from tests.contrib.rq.jobs import JobClass
from tests.contrib.rq.jobs import job_add1
from tests.contrib.rq.jobs import job_fail

def test_worker_class_job(queue):
    queue.enqueue(JobClass(), 4, key="abc")
    queue.fetch_job("abc")
    worker = rq.SimpleWorker([queue], connection=queue.connection)
    worker.work(burst=True)

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """
    env = os.environ.copy()
    if service:
        env["DD_SERVICE"] = service
    if schema:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema
    env["DD_TRACE_REDIS_ENABLED"] = "false"
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, (err.decode(), out.decode())
    assert err == b"", err.decode()
