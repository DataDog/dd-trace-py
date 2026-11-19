import threading

from celery import Celery
from celery import signals
from celery.contrib.testing.worker import start_worker

from ddtrace.contrib.internal.celery.patch import patch as patch_celery
from ddtrace.contrib.internal.kombu.patch import patch as patch_kombu
from ddtrace.internal.datastreams import data_streams_processor


QUEUE = "dsm-celery-tasks"


def pathway_stats():
    stats = {}
    for bucket in data_streams_processor()._buckets.values():
        for key, value in bucket.pathway_stats.items():
            stats[key] = value
    return stats


def checkpoints(direction):
    return [(tags, h, parent) for (tags, h, parent) in pathway_stats() if f"direction:{direction}" in tags.split(",")]


def run_one_task(broker_url, with_kombu=False):
    patch_celery()
    if with_kombu:
        patch_kombu()

    app = Celery("dsm_celery", broker=broker_url)
    app.conf.task_default_queue = QUEUE
    app.conf.broker_connection_retry_on_startup = True

    @app.task(name="dsm_celery.add")
    def add(x, y):
        return x + y

    published_headers = {}
    task_ran = threading.Event()

    def capture_headers(headers=None, **kwargs):
        published_headers.update(headers or {})

    def mark_task_ran(**kwargs):
        task_ran.set()

    signals.before_task_publish.connect(capture_headers, weak=False)
    signals.task_postrun.connect(mark_task_ran, weak=False)

    with start_worker(app, pool="solo", perform_ping_check=False, shutdown_timeout=30):
        add.delay(2, 3)
        assert task_ran.wait(timeout=30)
    return published_headers


def assert_linked_edge(broker_type):
    outs = [c for c in checkpoints("out") if f"topic:{QUEUE}" in c[0].split(",")]
    ins = checkpoints("in")
    expected_tags = f"topic:{QUEUE},type:{broker_type}"
    assert [c[0] for c in outs] == [f"direction:out,{expected_tags}"], pathway_stats()
    assert [c[0] for c in ins] == [f"direction:in,{expected_tags}"], pathway_stats()
    assert ins[0][2] == outs[0][1], pathway_stats()
    assert all(c[0] == f"direction:out,{expected_tags}" for c in checkpoints("out")), pathway_stats()
