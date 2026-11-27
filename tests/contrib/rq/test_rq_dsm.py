import pytest


DSM_ENV = {"DD_DATA_STREAMS_ENABLED": "true", "_DD_TRACE_STATS_WRITER_INTERVAL": "1000"}


@pytest.mark.subprocess(env=DSM_ENV, out=None, err=None)
def test_simple_worker_links_enqueue_to_perform():
    import os
    import tempfile

    import redis
    import rq

    from ddtrace.contrib.internal.rq.patch import patch
    from tests.contrib.config import REDIS_CONFIG
    from tests.contrib.rq.dsm_helpers import entries
    from tests.contrib.rq.dsm_helpers import in_tags
    from tests.contrib.rq.dsm_helpers import install_capture
    from tests.contrib.rq.dsm_helpers import out_tags
    from tests.contrib.rq.dsm_helpers import read_stats
    from tests.contrib.rq.jobs import job_add1

    patch()
    path = os.path.join(tempfile.mkdtemp(), "stats.jsonl")
    processor = install_capture(path)
    connection = redis.Redis(port=REDIS_CONFIG["port"])
    queue = rq.Queue("dsm-simple", connection=connection)
    queue.empty()

    job = queue.enqueue(job_add1, 1)
    assert "dd-pathway-ctx-base64" in rq.job.Job.fetch(job.id, connection=connection).meta
    rq.SimpleWorker([queue], connection=connection).work(burst=True)
    processor.periodic()

    stats = read_stats(path)
    [(out_hash, out_parent, out_count)] = entries(stats, out_tags("dsm-simple"))
    [(_in_hash, in_parent, in_count)] = entries(stats, in_tags("dsm-simple"))
    assert out_parent == 0
    assert in_parent == out_hash
    assert (out_count, in_count) == (1, 1)


@pytest.mark.subprocess(env=DSM_ENV, out=None, err=None)
def test_forking_worker_flushes_in_the_work_horse():
    import os
    import tempfile

    import redis
    import rq

    from ddtrace.contrib.internal.rq.patch import patch
    from tests.contrib.config import REDIS_CONFIG
    from tests.contrib.rq.dsm_helpers import entries
    from tests.contrib.rq.dsm_helpers import in_tags
    from tests.contrib.rq.dsm_helpers import install_capture
    from tests.contrib.rq.dsm_helpers import out_tags
    from tests.contrib.rq.dsm_helpers import read_stats
    from tests.contrib.rq.jobs import job_add1

    patch()
    path = os.path.join(tempfile.mkdtemp(), "stats.jsonl")
    processor = install_capture(path)
    connection = redis.Redis(port=REDIS_CONFIG["port"])
    queue = rq.Queue("dsm-fork", connection=connection)
    queue.empty()

    job = queue.enqueue(job_add1, 1)
    rq.Worker([queue], connection=connection).work(burst=True)
    assert job.get_status(refresh=True) == "finished"
    processor.periodic()

    stats = read_stats(path)
    [(out_hash, _out_parent, out_count)] = entries(stats, out_tags("dsm-fork"))
    [(_in_hash, in_parent, in_count)] = entries(stats, in_tags("dsm-fork"))
    assert in_parent == out_hash
    assert (out_count, in_count) == (1, 1)


@pytest.mark.subprocess(env=DSM_ENV, out=None, err=None)
def test_job_that_enqueues_carries_the_pathway():
    import os
    import tempfile

    import redis
    import rq

    from ddtrace.contrib.internal.rq.patch import patch
    from tests.contrib.config import REDIS_CONFIG
    from tests.contrib.rq.dsm_helpers import entries
    from tests.contrib.rq.dsm_helpers import in_tags
    from tests.contrib.rq.dsm_helpers import install_capture
    from tests.contrib.rq.dsm_helpers import out_tags
    from tests.contrib.rq.dsm_helpers import read_stats
    from tests.contrib.rq.jobs import job_forward

    patch()
    path = os.path.join(tempfile.mkdtemp(), "stats.jsonl")
    processor = install_capture(path)
    connection = redis.Redis(port=REDIS_CONFIG["port"])
    first = rq.Queue("dsm-first", connection=connection)
    second = rq.Queue("dsm-second", connection=connection)
    first.empty()
    second.empty()

    first.enqueue(job_forward, "dsm-second", 1)
    rq.SimpleWorker([first], connection=connection).work(burst=True)
    rq.SimpleWorker([second], connection=connection).work(burst=True)
    processor.periodic()

    stats = read_stats(path)
    [(out1, _, _)] = entries(stats, out_tags("dsm-first"))
    [(in1, in1_parent, _)] = entries(stats, in_tags("dsm-first"))
    [(out2, out2_parent, _)] = entries(stats, out_tags("dsm-second"))
    [(_in2, in2_parent, _)] = entries(stats, in_tags("dsm-second"))
    assert in1_parent == out1
    assert out2_parent == in1
    assert in2_parent == out2


@pytest.mark.subprocess(env=DSM_ENV, out=None, err=None)
def test_scheduled_dependent_and_bulk_jobs_link():
    from datetime import datetime
    from datetime import timedelta
    from datetime import timezone
    import os
    import tempfile
    import uuid

    import redis
    import rq
    from rq.scheduler import RQScheduler

    from ddtrace.contrib.internal.rq.patch import patch
    from tests.contrib.config import REDIS_CONFIG
    from tests.contrib.rq.dsm_helpers import entries
    from tests.contrib.rq.dsm_helpers import in_tags
    from tests.contrib.rq.dsm_helpers import install_capture
    from tests.contrib.rq.dsm_helpers import out_tags
    from tests.contrib.rq.dsm_helpers import read_stats
    from tests.contrib.rq.jobs import job_add1

    patch()
    path = os.path.join(tempfile.mkdtemp(), "stats.jsonl")
    processor = install_capture(path)
    connection = redis.Redis(port=REDIS_CONFIG["port"])
    name = "dsm-kinds-%s" % uuid.uuid4().hex
    queue = rq.Queue(name, connection=connection)

    expected = 3
    queue.enqueue_at(datetime.now(timezone.utc) - timedelta(seconds=1), job_add1, 1)
    parent = queue.enqueue(job_add1, 2)
    queue.enqueue(job_add1, 3, depends_on=parent)
    if hasattr(queue, "enqueue_many"):
        queue.enqueue_many([rq.Queue.prepare_data(job_add1, (4,)), rq.Queue.prepare_data(job_add1, (5,))])
        expected += 2
    scheduler = RQScheduler([queue], connection=connection)
    assert scheduler.acquire_locks() == {name}
    scheduler.enqueue_scheduled_jobs()
    scheduler.release_locks()
    rq.SimpleWorker([queue], connection=connection).work(burst=True)
    processor.periodic()

    stats = read_stats(path)
    [(out_hash, _, out_count)] = entries(stats, out_tags(name))
    [(_, in_parent, in_count)] = entries(stats, in_tags(name))
    assert in_parent == out_hash
    assert out_count == expected
    assert in_count == expected


@pytest.mark.subprocess(env=DSM_ENV, out=None, err=None)
def test_retry_links_to_the_original_enqueue():
    import os
    import tempfile
    import uuid

    import redis
    import rq
    from rq import Retry

    from ddtrace.contrib.internal.rq.patch import patch
    from tests.contrib.config import REDIS_CONFIG
    from tests.contrib.rq.dsm_helpers import entries
    from tests.contrib.rq.dsm_helpers import in_tags
    from tests.contrib.rq.dsm_helpers import install_capture
    from tests.contrib.rq.dsm_helpers import out_tags
    from tests.contrib.rq.dsm_helpers import read_stats
    from tests.contrib.rq.jobs import job_fail_once

    patch()
    path = os.path.join(tempfile.mkdtemp(), "stats.jsonl")
    processor = install_capture(path)
    connection = redis.Redis(port=REDIS_CONFIG["port"])
    queue = rq.Queue("dsm-retry", connection=connection)
    queue.empty()

    job = queue.enqueue(job_fail_once, "dsm-retry-%s" % uuid.uuid4(), retry=Retry(max=1))
    rq.SimpleWorker([queue], connection=connection).work(burst=True)
    assert job.get_status(refresh=True) == "finished"
    processor.periodic()

    stats = read_stats(path)
    [(out_hash, _, out_count)] = entries(stats, out_tags("dsm-retry"))
    [(_, in_parent, in_count)] = entries(stats, in_tags("dsm-retry"))
    assert in_parent == out_hash
    assert (out_count, in_count) == (1, 2)


@pytest.mark.subprocess(env=DSM_ENV, out=None, err=None)
def test_prebuilt_job_gets_one_checkpoint():
    import os
    import tempfile

    import redis
    import rq
    from rq.job import Job

    from ddtrace.contrib.internal.rq.patch import patch
    from tests.contrib.config import REDIS_CONFIG
    from tests.contrib.rq.dsm_helpers import entries
    from tests.contrib.rq.dsm_helpers import install_capture
    from tests.contrib.rq.dsm_helpers import out_tags
    from tests.contrib.rq.dsm_helpers import read_stats
    from tests.contrib.rq.jobs import job_add1

    patch()
    path = os.path.join(tempfile.mkdtemp(), "stats.jsonl")
    processor = install_capture(path)
    connection = redis.Redis(port=REDIS_CONFIG["port"])
    queue = rq.Queue("dsm-prebuilt", connection=connection)
    queue.empty()

    shared_meta = {"owner": "test"}
    queue.enqueue(job_add1, 1, meta=shared_meta)
    queue.enqueue_job(Job.create(job_add1, args=(2,), connection=connection))
    processor.periodic()

    assert shared_meta == {"owner": "test"}
    [(_, _, out_count)] = entries(read_stats(path), out_tags("dsm-prebuilt"))
    assert out_count == 2


@pytest.mark.subprocess(env=DSM_ENV, out=None, err=None)
def test_corrupt_pathway_does_not_break_the_job():
    import os
    import tempfile

    import redis
    import rq

    from ddtrace.contrib.internal.rq.patch import patch
    from tests.contrib.config import REDIS_CONFIG
    from tests.contrib.rq.dsm_helpers import entries
    from tests.contrib.rq.dsm_helpers import in_tags
    from tests.contrib.rq.dsm_helpers import install_capture
    from tests.contrib.rq.dsm_helpers import read_stats
    from tests.contrib.rq.jobs import job_add1

    patch()
    path = os.path.join(tempfile.mkdtemp(), "stats.jsonl")
    processor = install_capture(path)
    connection = redis.Redis(port=REDIS_CONFIG["port"])
    queue = rq.Queue("dsm-corrupt", connection=connection)
    queue.empty()

    job = queue.enqueue(job_add1, 1)
    job.meta["dd-pathway-ctx-base64"] = "not base64!"
    job.save_meta()
    rq.SimpleWorker([queue], connection=connection).work(burst=True)
    processor.periodic()

    assert job.get_status(refresh=True) == "finished"
    [(_, in_parent, in_count)] = entries(read_stats(path), in_tags("dsm-corrupt"))
    assert (in_parent, in_count) == (0, 1)


@pytest.mark.subprocess(env=DSM_ENV, out=None, err=None)
def test_sync_queue_sets_no_checkpoint():
    import os
    import tempfile

    import redis
    import rq

    from ddtrace.contrib.internal.rq.patch import patch
    from tests.contrib.config import REDIS_CONFIG
    from tests.contrib.rq.dsm_helpers import install_capture
    from tests.contrib.rq.dsm_helpers import read_stats
    from tests.contrib.rq.jobs import job_add1

    patch()
    path = os.path.join(tempfile.mkdtemp(), "stats.jsonl")
    processor = install_capture(path)
    queue = rq.Queue("dsm-sync", is_async=False, connection=redis.Redis(port=REDIS_CONFIG["port"]))

    job = queue.enqueue(job_add1, 1)
    processor.periodic()

    assert job.result == 2
    assert "dd-pathway-ctx-base64" not in job.meta
    assert read_stats(path) == {}


@pytest.mark.subprocess(env={"DD_DATA_STREAMS_ENABLED": "false"}, out=None, err=None)
def test_dsm_disabled_writes_nothing():
    import redis
    import rq

    from ddtrace.contrib.internal.rq.patch import patch
    from ddtrace.internal import datastreams
    from tests.contrib.config import REDIS_CONFIG
    from tests.contrib.rq.jobs import job_add1

    patch()
    connection = redis.Redis(port=REDIS_CONFIG["port"])
    queue = rq.Queue("dsm-off", connection=connection)
    queue.empty()

    job = queue.enqueue(job_add1, 1)
    rq.SimpleWorker([queue], connection=connection).work(burst=True)

    assert "dd-pathway-ctx-base64" not in rq.job.Job.fetch(job.id, connection=connection).meta
    assert datastreams._processor is None


@pytest.mark.subprocess(env=DSM_ENV, out=None, err=None)
def test_enqueue_span_gets_the_pathway_hash():
    import redis
    import rq

    from ddtrace.contrib.internal.rq.patch import patch
    from ddtrace.internal.datastreams import rq as dsm_rq
    from tests.contrib.config import REDIS_CONFIG
    from tests.contrib.rq.jobs import job_add1

    tagged = {}

    class Span:
        def _set_attribute(self, key, value):
            tagged[key] = value

    patch()
    connection = redis.Redis(port=REDIS_CONFIG["port"])
    queue = rq.Queue("dsm-span", connection=connection)
    job = queue.create_job(job_add1, args=(1,))
    dsm_rq.dsm_rq_enqueue_job(queue, job, Span())

    assert tagged["pathway.hash"] == str(dsm_rq._pathway_hash(job.meta["dd-pathway-ctx-base64"]))
    assert tagged["pathway.hash"] != "0"


def test_payload_size_counts_positional_and_keyword_arguments():
    from types import SimpleNamespace

    from ddtrace.internal.datastreams.rq import _payload_size

    assert _payload_size(SimpleNamespace(args=("abc", b"de", 7), kwargs={"k": "vw"})) == 8
    assert _payload_size(SimpleNamespace(args=None, kwargs=None)) == 0
