"""Kafka produce/consume workload to measure DSM overhead on top of APM.

The script has no tracer-specific code: run it with plain `python` for the baseline and with
`ddtrace-run python` for the tracer scenarios (see run.sh). DSM is toggled only through
DD_DATA_STREAMS_ENABLED.

Each iteration creates a fresh single-partition topic, then times produce (1000 messages with
5 headers + flush) and consume (1000 messages, sync commit every COMMIT_EVERY messages and after
the last one). Topic creation, client construction, group join and consumer.close() are untimed.
With PROFILE=1 cProfile runs only during the timed phases of the timed (non-warmup) iterations.
"""

import cProfile
import json
import os
import pstats
import resource
import sys
import time
import uuid

from confluent_kafka import Consumer
from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient
from confluent_kafka.admin import NewTopic


COMMIT_EVERY = int(os.environ.get("COMMIT_EVERY", "100"))
NUM_THREADS = int(os.environ.get("NUM_THREADS", "1"))
WARMUP = int(os.environ.get("WARMUP", "5"))
COUNT = int(os.environ.get("COUNT", "25"))
BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
TOPIC_BASE = os.environ.get("KAFKA_TOPIC", "benchmark-topic-python-default")
OUTPUT = os.environ.get("TIMEIT_OUTPUT", "results.json")
PROFILE = os.environ.get("PROFILE", "0") == "1"
# PROFILE=1 writes <PROFILE_OUTPUT>.prof and <PROFILE_OUTPUT>.top.txt
PROFILE_OUTPUT = os.environ.get("PROFILE_OUTPUT", "profile")
PROFILE_TOP_N = int(os.environ.get("PROFILE_TOP_N", "40"))
MESSAGES = 1000
RUN_ID = uuid.uuid4().hex[:8]


# "localhost" resolves to ::1 first, where Docker's published port refuses connections; every
# client would log that failure and retry over IPv4, adding noise.
CLIENT_CONFIG = {"bootstrap.servers": BOOTSTRAP_SERVERS, "broker.address.family": "v4"}


def max_rss_mb():
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss is bytes on macOS and kilobytes on Linux.
    return rss / (1024 * 1024 if sys.platform == "darwin" else 1024)


def run_iteration(index, profiler):
    topic = f"{TOPIC_BASE}-{RUN_ID}-{index}"
    admin = AdminClient(CLIENT_CONFIG)
    for future in admin.create_topics([NewTopic(topic, 1, 1)]).values():
        future.result()

    # Setup (untimed): producer + consumer, join the group.
    producer = Producer(CLIENT_CONFIG)
    # Connect and fetch metadata now so the first timed produce() does not pay for it.
    producer.list_topics(topic, timeout=10)
    consumer = Consumer(
        {
            **CLIENT_CONFIG,
            "group.id": f"group-{RUN_ID}-{index}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([topic])
    while not consumer.assignment():
        consumer.poll(0.1)
    # Must be a dict: ddtrace's DSM hook skips list/tuple headers.
    headers = {f"h{k}": f"v{k}".encode() for k in range(5)}
    payload = b"x" * 100

    if profiler is not None:
        profiler.enable()
    t0 = time.perf_counter()
    for _ in range(MESSAGES):
        producer.produce(topic, value=payload, headers=headers)
        producer.poll(0)
    producer.flush()
    t1 = time.perf_counter()
    received = 0
    while received < MESSAGES:
        msg = consumer.poll(1.0)
        if msg is None or msg.error():
            continue
        received += 1
        if received % COMMIT_EVERY == 0 or received == MESSAGES:
            consumer.commit(msg, asynchronous=False)
    t2 = time.perf_counter()
    if profiler is not None:
        profiler.disable()

    consumer.close()  # Teardown (untimed)
    return (t1 - t0) * 1000, (t2 - t1) * 1000


def write_profile(profiler):
    profiler.dump_stats(f"{PROFILE_OUTPUT}.prof")
    with open(f"{PROFILE_OUTPUT}.top.txt", "w") as out:
        for sort_key in ("tottime", "cumulative"):
            out.write(f"=== top {PROFILE_TOP_N} by {sort_key} ({COUNT} timed iterations, produce+consume only)\n")
            pstats.Stats(profiler, stream=out).strip_dirs().sort_stats(sort_key).print_stats(PROFILE_TOP_N)


def main():
    if NUM_THREADS != 1:
        print("NUM_THREADS > 1 is not implemented", file=sys.stderr)
        sys.exit(2)
    for i in range(WARMUP):
        run_iteration(i, None)

    profiler = cProfile.Profile() if PROFILE else None
    runs = []
    for i in range(COUNT):
        produce_ms, consume_ms = run_iteration(WARMUP + i, profiler)
        runs.append({"produce_ms": produce_ms, "consume_ms": consume_ms})
        print(f"iter {i}: produce={produce_ms:.1f}ms consume={consume_ms:.1f}ms", flush=True)

    with open(OUTPUT, "w") as out:
        json.dump(
            {
                "runs": runs,
                "max_rss_mb": max_rss_mb(),
                "commit_every": COMMIT_EVERY,
                "profile": PROFILE,
                "env": {k: v for k, v in os.environ.items() if k.startswith("DD_")},
            },
            out,
            indent=1,
        )
    if profiler is not None:
        write_profile(profiler)


main()
