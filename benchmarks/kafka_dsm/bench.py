"""Kafka produce/consume workload to measure DSM overhead on top of APM.

The script has no tracer-specific code: run it with plain `python` for the baseline and with
`ddtrace-run python` for the tracer scenarios (see run.sh). DSM is toggled only through
DD_DATA_STREAMS_ENABLED.

Each iteration creates a fresh single-partition topic and consumer group, then times produce
(1000 unkeyed 1024-byte messages with 5 headers + flush) and consume (1000 messages, sync commit
every COMMIT_EVERY messages and after the last one). Topic creation, client construction, group
join, gc.collect() + gc.freeze() and consumer.close() are untimed. Process RSS is sampled after each timed iteration.
Group join, flush and consume each fail after TIMEOUT_S. The run's topics and groups are deleted
at exit.
With PROFILE=1 cProfile runs only during the timed phases of the timed (non-warmup) iterations.
"""

import cProfile
import gc
import json
import math
import os
import pstats
import re
import statistics
import subprocess
import sys
import time
import uuid

from confluent_kafka import Consumer
from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient
from confluent_kafka.admin import NewTopic


def int_env(name, default, minimum):
    value = os.environ.get(name, default)
    if not re.fullmatch(r"[0-9]+", value) or int(value) < minimum:
        print(f"{name} must be an integer >= {minimum}, got {value!r}", file=sys.stderr)
        sys.exit(2)
    return int(value)


COMMIT_EVERY = int_env("COMMIT_EVERY", "100", 1)
NUM_THREADS = int(os.environ.get("NUM_THREADS", "1"))
WARMUP = int_env("WARMUP", "5", 0)
COUNT = int_env("COUNT", "25", 1)
BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
TOPIC_BASE = os.environ.get("KAFKA_TOPIC", "benchmark-topic-python-default")
OUTPUT = os.environ.get("TIMEIT_OUTPUT", "results.json")
EXPERIMENT_NAME = os.environ.get("EXPERIMENT_NAME", "kafka")
# Per-iteration data goes next to OUTPUT so OUTPUT keeps the canonical schema.
RUNS_OUTPUT = (OUTPUT[: -len(".json")] if OUTPUT.endswith(".json") else OUTPUT) + ".runs.json"
PROFILE = os.environ.get("PROFILE", "0") == "1"
# PROFILE=1 writes <PROFILE_OUTPUT>.prof and <PROFILE_OUTPUT>.top.txt
PROFILE_OUTPUT = os.environ.get("PROFILE_OUTPUT", "profile")
PROFILE_TOP_N = int(os.environ.get("PROFILE_TOP_N", "40"))
MESSAGES = 1000
RUN_ID = uuid.uuid4().hex[:8]
# Bounds each blocking wait (group join, flush, consume) so a broker problem fails fast.
TIMEOUT_S = 60
# Non-secret DD_* settings copied into the runs file; never dump the whole environment.
RECORDED_ENV = (
    "DD_DATA_STREAMS_ENABLED",
    "DD_ENV",
    "DD_SERVICE",
    "DD_TRACE_AGENT_URL",
    "DD_TRACE_ENABLED",
    "DD_TRACE_STARTUP_LOGS",
)
# Topics and groups created by this run, deleted once all iterations are done.
created_topics = []
created_groups = []


# "localhost" resolves to ::1 first, where Docker's published port refuses connections; every
# client would log that failure and retry over IPv4, adding noise.
CLIENT_CONFIG = {"bootstrap.servers": BOOTSTRAP_SERVERS, "broker.address.family": "v4"}


def rss_bytes():
    """Current (not peak) resident set size of this process."""
    try:
        with open("/proc/self/status") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except FileNotFoundError:
        pass
    # No /proc (macOS): ps reports RSS in KiB.
    return int(subprocess.check_output(["ps", "-o", "rss=", "-p", str(os.getpid())])) * 1024


def run_iteration(index, profiler):
    topic = f"{TOPIC_BASE}-{RUN_ID}-{index}"
    admin = AdminClient(CLIENT_CONFIG)
    for future in admin.create_topics([NewTopic(topic, 1, 1)]).values():
        future.result()
    created_topics.append(topic)

    # Setup (untimed): producer + consumer, join the group.
    producer = Producer(CLIENT_CONFIG)
    # Connect and fetch metadata now so the first timed produce() does not pay for it.
    producer.list_topics(topic, timeout=10)
    group = f"group-{RUN_ID}-{index}"
    consumer = Consumer(
        {
            **CLIENT_CONFIG,
            "group.id": group,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    created_groups.append(group)
    try:
        consumer.subscribe([topic])
        deadline = time.monotonic() + TIMEOUT_S
        while not consumer.assignment():
            if time.monotonic() >= deadline:
                raise TimeoutError(f"No partition assigned for {topic} within {TIMEOUT_S}s")
            consumer.poll(0.1)
        # Dict (map form): older ddtrace DSM hooks skip list/tuple headers.
        headers = {f"key{k}": f"value{k}".encode() for k in range(5)}
        payload = b"x" * 1024

        # Like benchmarks/bm: keep the setup heap out of the timed collections so GC work per iteration is stable.
        gc.collect()
        gc.freeze()
        if profiler is not None:
            profiler.enable()
        t0 = time.perf_counter()
        for _ in range(MESSAGES):
            producer.produce(topic, value=payload, headers=headers)
            producer.poll(0)
        undelivered = producer.flush(TIMEOUT_S)
        t1 = time.perf_counter()
        if undelivered:
            raise TimeoutError(f"{undelivered} messages still undelivered after {TIMEOUT_S}s")
        received = 0
        deadline = time.monotonic() + TIMEOUT_S
        while received < MESSAGES:
            msg = consumer.poll(1.0)
            if msg is None or msg.error():
                # Deadline is checked only off the hot path, so it adds no per-message cost.
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Received only {received}/{MESSAGES} messages within {TIMEOUT_S}s")
                continue
            received += 1
            if received % COMMIT_EVERY == 0 or received == MESSAGES:
                consumer.commit(msg, asynchronous=False)
        t2 = time.perf_counter()
        if profiler is not None:
            profiler.disable()
    finally:
        gc.unfreeze()
        consumer.close()  # Teardown (untimed); also frees the group if the iteration fails
    return (t1 - t0) * 1000, (t2 - t1) * 1000


def write_profile(profiler):
    profiler.dump_stats(f"{PROFILE_OUTPUT}.prof")
    with open(f"{PROFILE_OUTPUT}.top.txt", "w") as out:
        for sort_key in ("tottime", "cumulative"):
            out.write(f"=== top {PROFILE_TOP_N} by {sort_key} ({COUNT} timed iterations, produce+consume only)\n")
            pstats.Stats(profiler, stream=out).strip_dirs().sort_stats(sort_key).print_stats(PROFILE_TOP_N)


def delete_created_resources():
    """Best-effort, untimed cleanup so repeated runs do not pile up topics and groups."""
    try:
        admin = AdminClient(CLIENT_CONFIG)
        futures = []
        if created_groups:
            futures += admin.delete_consumer_groups(created_groups, request_timeout=TIMEOUT_S).items()
        if created_topics:
            timeouts = {"operation_timeout": TIMEOUT_S, "request_timeout": TIMEOUT_S}
            futures += admin.delete_topics(created_topics, **timeouts).items()
    except Exception as e:
        print(f"cleanup: skipped: {e}", file=sys.stderr)
        return
    for name, future in futures:
        try:
            future.result(timeout=TIMEOUT_S)
        except Exception as e:
            print(f"cleanup: could not delete {name}: {e}", file=sys.stderr)


def main():
    if NUM_THREADS != 1:
        print("NUM_THREADS > 1 is not implemented", file=sys.stderr)
        sys.exit(2)
    try:
        run_benchmark()
    finally:
        delete_created_resources()


def run_benchmark():
    for i in range(WARMUP):
        run_iteration(i, None)

    profiler = cProfile.Profile() if PROFILE else None
    runs = []
    iteration_wall_ms = []
    for i in range(COUNT):
        start = time.perf_counter()
        produce_ms, consume_ms = run_iteration(WARMUP + i, profiler)
        iteration_wall_ms.append((time.perf_counter() - start) * 1000)
        rss = rss_bytes()
        runs.append({"produce_ms": produce_ms, "consume_ms": consume_ms, "rss_bytes": rss})
        print(f"iter {i}: produce={produce_ms:.1f}ms consume={consume_ms:.1f}ms rss={rss}", flush=True)

    durations = [r["produce_ms"] + r["consume_ms"] for r in runs]
    std_err = statistics.stdev(durations) / math.sqrt(len(durations)) if len(durations) > 1 else 0.0
    median_ms = statistics.median(durations)
    result = {
        "median": median_ms,
        "metrics": {
            "process.internal_duration_ms.median": median_ms,
            "process.internal_duration_ms.std_err": std_err,
            # median_low keeps a whole byte count for even COUNT.
            "process.rss_bytes.median": statistics.median_low(r["rss_bytes"] for r in runs),
            "phase.produce_ms.median": statistics.median(r["produce_ms"] for r in runs),
            "phase.consume_ms.median": statistics.median(r["consume_ms"] for r in runs),
            "config.commit_every": COMMIT_EVERY,
        },
        # For bp-analyzer's TimeItSharp converter: it reads only `data`, under TimeItSharp metric names
        # (runtime.dotnet.mem.committed is mapped to RSS whatever the runtime).
        "name": EXPERIMENT_NAME,
        "warmUpCount": WARMUP,
        "count": COUNT,
        "data": [
            {
                "status": "Passed",
                "metrics": {
                    "process.internal_duration_ms": duration_ms,
                    "process.corrected_duration_ms": wall_ms,
                    "runtime.dotnet.mem.committed": r["rss_bytes"],
                },
            }
            for duration_ms, wall_ms, r in zip(durations, iteration_wall_ms, runs)
        ],
    }
    with open(OUTPUT, "w") as out:
        json.dump([result], out, indent=1)
    with open(RUNS_OUTPUT, "w") as out:
        json.dump(
            {
                "runs": runs,
                "commit_every": COMMIT_EVERY,
                "profile": PROFILE,
                "env": {k: os.environ[k] for k in RECORDED_ENV if k in os.environ},
            },
            out,
            indent=1,
        )
    if profiler is not None:
        write_profile(profiler)


main()
