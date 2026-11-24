import threading

from celery import Celery
from celery import signals
from celery.contrib.testing.worker import start_worker
from kombu import Exchange
from kombu import Queue

from ddtrace.contrib.internal.celery.patch import patch as patch_celery
from ddtrace.contrib.internal.kombu.patch import patch as patch_kombu
from ddtrace.internal.datastreams import data_streams_processor


QUEUE = "dsm-celery-tasks"
ROUTED_EXCHANGE = "dsm-celery-orders"
ROUTED_QUEUE = "dsm-celery-orders-queue"
ROUTING_KEY = "orders.created"


def pathway_stats():
    stats = {}
    for bucket in data_streams_processor()._buckets.values():
        for key, value in bucket.pathway_stats.items():
            stats[key] = value
    return stats


def checkpoints(direction):
    return [(tags, h, parent) for (tags, h, parent) in pathway_stats() if f"direction:{direction}" in tags.split(",")]


def _capture_publish_and_run():
    published_headers = {}
    task_ran = threading.Event()

    def capture_headers(headers=None, **kwargs):
        published_headers.update(headers or {})

    def mark_task_ran(**kwargs):
        task_ran.set()

    signals.before_task_publish.connect(capture_headers, weak=False)
    signals.task_postrun.connect(mark_task_ran, weak=False)
    return published_headers, task_ran


def run_one_task(broker_url, with_kombu=False, task_protocol=None, args=(2, 3)):
    patch_celery()
    if with_kombu:
        patch_kombu()

    app = Celery("dsm_celery", broker=broker_url)
    app.conf.task_default_queue = QUEUE
    app.conf.broker_connection_retry_on_startup = True
    if task_protocol is not None:
        app.conf.task_protocol = task_protocol

    @app.task(name="dsm_celery.add")
    def add(*values):
        return len(values)

    published_headers, task_ran = _capture_publish_and_run()

    with start_worker(app, pool="solo", perform_ping_check=False, shutdown_timeout=30):
        add.delay(*args)
        assert task_ran.wait(timeout=30)
    return published_headers


def run_task_sent_by_name_with_routing_key(broker_url):
    patch_celery()

    worker_app = Celery("dsm_celery_worker", broker=broker_url)
    worker_app.conf.broker_connection_retry_on_startup = True
    worker_app.conf.task_queues = [
        Queue(ROUTED_QUEUE, Exchange(ROUTED_EXCHANGE, type="direct"), routing_key=ROUTING_KEY)
    ]
    worker_app.conf.task_default_queue = ROUTED_QUEUE

    @worker_app.task(name="dsm_celery.create_order")
    def create_order(order_id):
        return order_id

    producer_app = Celery("dsm_celery_producer", broker=broker_url)
    producer_app.conf.broker_connection_retry_on_startup = True

    _, task_ran = _capture_publish_and_run()

    with start_worker(worker_app, pool="solo", perform_ping_check=False, shutdown_timeout=30):
        producer_app.send_task("dsm_celery.create_order", args=(1,), exchange=ROUTED_EXCHANGE, routing_key=ROUTING_KEY)
        assert task_ran.wait(timeout=30)


def assert_linked_edge(broker_type):
    outs = [c for c in checkpoints("out") if f"topic:{QUEUE}" in c[0].split(",")]
    ins = checkpoints("in")
    expected_tags = f"topic:{QUEUE},type:{broker_type}"
    assert [c[0] for c in outs] == [f"direction:out,{expected_tags}"], pathway_stats()
    assert [c[0] for c in ins] == [f"direction:in,{expected_tags}"], pathway_stats()
    assert ins[0][2] == outs[0][1], pathway_stats()
    for key in (outs[0], ins[0]):
        assert pathway_stats()[key].full_pathway_latency.count == 1, pathway_stats()
    assert all(c[0] == f"direction:out,{expected_tags}" for c in checkpoints("out")), pathway_stats()
