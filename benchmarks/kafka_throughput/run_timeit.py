"""Timing harness for the Kafka throughput workload.

Python stand-in for ``dotnet timeit --count 25 --warmup 5``. Runs the workload
for a fixed number of warmup + timed iterations in-process (so the tracer, when
enabled via ``ddtrace-run``, stays active across all iterations), then emits a
JSON artifact in the shape ``steps/compare-results.py`` expects:

    [
      {
        "median": <median duration ms>,
        "metrics": {
          "process.internal_duration_ms.median": <ms>,
          "process.internal_duration_ms.std_err": <ms>,
          "process.rss_bytes.median": <bytes>
        }
      }
    ]

Duration is wall-clock per iteration; the memory metric is process RSS (the
cross-language analog of .NET's ``runtime.dotnet.mem.committed``). Medians are
used for robustness against outliers, matching the .NET gate.
"""

import json
import os
import statistics
import time

import psutil

import kafka_throughput


def main():
    warmup = int(os.environ.get("WARMUP", "5"))
    count = int(os.environ.get("COUNT", "25"))
    output_path = os.environ.get("TIMEIT_OUTPUT", "results.json")

    proc = psutil.Process()
    run_id = 0

    # Warmup iterations are discarded.
    for _ in range(warmup):
        kafka_throughput.run_benchmark(run_id)
        run_id += 1

    durations_ms = []
    rss_bytes = []
    produce_ms = []
    consume_ms = []
    for _ in range(count):
        start = time.perf_counter()
        phases = kafka_throughput.run_benchmark(run_id)
        durations_ms.append((time.perf_counter() - start) * 1000.0)
        rss_bytes.append(proc.memory_info().rss)
        produce_ms.append(phases["produce_ms"])
        consume_ms.append(phases["consume_ms"])
        run_id += 1

    duration_median = statistics.median(durations_ms)
    if len(durations_ms) > 1:
        duration_std_err = statistics.stdev(durations_ms) / (len(durations_ms) ** 0.5)
    else:
        duration_std_err = 0.0
    rss_median = statistics.median(rss_bytes)
    produce_median = statistics.median(produce_ms)
    consume_median = statistics.median(consume_ms)

    result = [
        {
            "median": duration_median,
            "metrics": {
                "process.internal_duration_ms.median": duration_median,
                "process.internal_duration_ms.std_err": duration_std_err,
                "process.rss_bytes.median": rss_median,
                # Diagnostic breakdown (not gated) — localizes DSM cost by phase.
                "phase.produce_ms.median": produce_median,
                "phase.consume_ms.median": consume_median,
            },
        }
    ]

    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    print(
        "Duration median: %.3f ms (± %.3f) | RSS median: %.2f MB | "
        "produce median: %.3f ms | consume+commit median: %.3f ms | runs: %d (warmup %d)"
        % (
            duration_median,
            duration_std_err,
            rss_median / 1_000_000,
            produce_median,
            consume_median,
            count,
            warmup,
        )
    )


if __name__ == "__main__":
    main()
