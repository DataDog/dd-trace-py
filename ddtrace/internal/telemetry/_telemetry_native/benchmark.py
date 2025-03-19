import time
import ddtrace
import statistics
from typing import List
import random

from ddtrace.internal.native._native_telemetry import (
    NativeTelemetryWorker,
    PyMetricType,
    PyMetricNamespace,
    PyConfig
)

from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.telemetry.writer import TelemetryWriter

def run_native_benchmark(
    num_metrics: int,
    points_per_metric: int,
    num_iterations: int = 5
) -> List[float]:
    times = []

    config = PyConfig()
    config.endpoint = "file:///tmp/telemetry"
    config.telemetry_heartbeat_interval = 1000

    for _ in range(num_iterations):
        worker = NativeTelemetryWorker(
            host="test-host",
            service="test-service",
            config=config
        )
        worker.send_start()

        tags = (("tag1", "value1"), ("tag2", "value2"))
        start = time.time()
        # Create metrics contexts
        for i in range(num_metrics):
            context = worker.register_metric_context(
                name=f"test.metric.{i}",
                tags=tags,
                metric_type=PyMetricType.Count,
                common=True,
                namespace=PyMetricNamespace.Tracers
            )

            for _ in range(points_per_metric):
                worker.add_point(
                    value=int(random.random() * 100),
                    context=context,
                    extra_tags=None,
                )

        times.append(time.time() - start)
        worker.send_stop()

    return times

def run_python_benchmark(
    num_metrics: int,
    points_per_metric: int,
    num_iterations: int = 5
) -> List[float]:
    times = []

    tags = (("tag1", "value1"), ("tag2", "value2"))
    for _ in range(num_iterations):
        ddtrace.internal.telemetry.telemetry_writer.disable()
        writer = TelemetryWriter()
        writer.enable()

        # Create metrics and send points
        start = time.time()
        for i in range(num_metrics):
            metric_name = f"test.metric.{i}"

            for _ in range(points_per_metric):
                writer.add_count_metric(
                    TELEMETRY_NAMESPACE.TRACERS,
                    metric_name,
                    int(random.random() * 100),
                    tags=tags
                )
        times.append(time.time() - start)
        writer.disable()

    return times

def print_stats(name: str, times: List[float]) -> None:
    print(f"\n{name} Statistics:")
    print(f"Mean: {statistics.mean(times):.6f} seconds")
    print(f"Median: {statistics.median(times):.6f} seconds")
    print(f"Std Dev: {statistics.stdev(times):.6f} seconds")
    print(f"Min: {min(times):.6f} seconds")
    print(f"Max: {max(times):.6f} seconds")

def main():
    # Benchmark parameters
    NUM_METRICS = 100
    POINTS_PER_METRIC = 1000
    NUM_ITERATIONS = 5

    print(f"Running benchmarks with:")
    print(f"- {NUM_METRICS} metrics")
    print(f"- {POINTS_PER_METRIC} points per metric")
    print(f"- {NUM_ITERATIONS} iterations")

    # Run Native (Rust) benchmarks
    native_times = run_native_benchmark(NUM_METRICS, POINTS_PER_METRIC, NUM_ITERATIONS)
    print_stats("Native (Rust) Implementation", native_times)

    # Run Python benchmarks
    python_times = run_python_benchmark(NUM_METRICS, POINTS_PER_METRIC, NUM_ITERATIONS)
    print_stats("Python Implementation", python_times)

    # Calculate and print speedup
    avg_speedup = statistics.mean(python_times) / statistics.mean(native_times)
    print(f"\nAverage speedup (Python/Native): {avg_speedup:.2f}x")

if __name__ == "__main__":
    main()
