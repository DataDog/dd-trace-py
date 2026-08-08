"""Kafka produce/consume throughput workload for DSM overhead benchmarking.

Python port of the .NET ``Samples.KafkaBenchmark`` used by the
``dd-trace-dotnet/data-streams-monitoring`` benchmarking-platform harness.

The workload is intentionally identical to the .NET one so the two languages'
DSM overhead numbers are comparable:

  * ``NUM_THREADS`` (default 5) parallel workers.
  * Each worker produces ``MESSAGE_COUNT`` (1000) messages, each carrying 5
    headers, to its own topic, flushes, then synchronously consumes and commits
    all 1000 messages back.

Tracing/DSM are controlled purely by environment (``ddtrace-run`` +
``DD_DATA_STREAMS_ENABLED``); this module contains no tracer-specific code, so
the DSM-on and DSM-off experiments run the exact same bytes.
"""

import os
import threading
import time

from confluent_kafka import Consumer
from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient
from confluent_kafka.admin import NewTopic


BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
CONSUMER_GROUP_ID = "benchmark-consumer-group"
# .NET builds a 32-char payload but sends str(i) as the value; we mirror the
# wire behavior (value == str(i)) and keep the constant for parity/reference.
MESSAGE_SIZE = 32
MESSAGE_COUNT = 1000
NUM_HEADERS = 5


def _create_topics(topics):
    """Create one single-partition topic per worker; ignore pre-existing ones."""
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP_SERVERS})
    new_topics = [NewTopic(t, num_partitions=1, replication_factor=1) for t in topics]
    futures = admin.create_topics(new_topics)
    for topic, future in futures.items():
        try:
            future.result()
        except Exception as exc:  # noqa: BLE001 - "already exists" is expected on reruns
            if "already exists" not in str(exc).lower():
                raise


def _run_worker(topic, group_id):
    producer = Producer(
        {
            "bootstrap.servers": BOOTSTRAP_SERVERS,
            "acks": "all",
            "message.send.max.retries": 1,
            "retry.backoff.ms": 100,
        }
    )

    consumer = Consumer(
        {
            "bootstrap.servers": BOOTSTRAP_SERVERS,
            "group.id": group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
            "session.timeout.ms": 30000,
            "heartbeat.interval.ms": 3000,
        }
    )
    consumer.subscribe([topic])

    try:
        # Phase 1: produce all messages.
        headers = [("key%d" % j, ("value%d" % j).encode("utf-8")) for j in range(NUM_HEADERS)]
        produce_start = time.perf_counter()
        for i in range(MESSAGE_COUNT):
            producer.produce(
                topic,
                key=str(time.time_ns()),
                value=str(i),
                headers=headers,
            )
        producer.flush()
        produce_ms = (time.perf_counter() - produce_start) * 1000.0

        # Phase 2: consume and commit all messages synchronously.
        consume_start = time.perf_counter()
        consumed = 0
        for i in range(MESSAGE_COUNT):
            msg = consumer.poll(10.0)
            if msg is None:
                raise RuntimeError("Failed to consume message %d within timeout on topic %s" % (i, topic))
            if msg.error() is not None:
                raise RuntimeError("Consumer error on topic %s: %s" % (topic, msg.error()))
            consumer.commit(msg, asynchronous=False)
            consumed += 1
        consume_ms = (time.perf_counter() - consume_start) * 1000.0

        if consumed != MESSAGE_COUNT:
            raise RuntimeError("Consumed %d/%d messages on topic %s" % (consumed, MESSAGE_COUNT, topic))

        return produce_ms, consume_ms
    finally:
        consumer.close()


def run_benchmark(run_id=0):
    """Run one full multi-threaded produce/consume pass.

    ``run_id`` makes topic and consumer-group names unique per invocation so
    repeated in-process iterations (warmup + timed runs) stay isolated instead
    of re-reading messages committed by previous iterations.
    """
    base_topic = os.environ.get("KAFKA_TOPIC", "benchmark-topic")
    thread_count = int(os.environ.get("NUM_THREADS", "5"))

    topics = ["%s-%d-%d" % (base_topic, run_id, t) for t in range(thread_count)]
    _create_topics(topics)

    errors = []
    phase_times = []  # (produce_ms, consume_ms) per worker

    def _target(topic, group_id):
        try:
            phase_times.append(_run_worker(topic, group_id))
        except Exception as exc:  # noqa: BLE001 - surface worker failures to the caller
            errors.append(exc)

    threads = []
    for t, topic in enumerate(topics):
        group_id = "%s-%d-%d" % (CONSUMER_GROUP_ID, run_id, t)
        thread = threading.Thread(target=_target, args=(topic, group_id))
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join()

    if errors:
        raise errors[0]

    # Mean per-worker phase durations (workers run concurrently, so this
    # approximates per-phase wall-clock and lets us localize where DSM cost lands
    # — produce vs synchronous consume+commit).
    n = len(phase_times) or 1
    mean_produce_ms = sum(p for p, _ in phase_times) / n
    mean_consume_ms = sum(c for _, c in phase_times) / n
    return {"produce_ms": mean_produce_ms, "consume_ms": mean_consume_ms}


if __name__ == "__main__":
    print(run_benchmark())
    print("Benchmark completed successfully")
