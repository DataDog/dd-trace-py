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

from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE, TELEMETRY_LOG_LEVEL
from ddtrace.internal.telemetry.writer import TelemetryWriter

def run_native_benchmark(
    inner_iterations: int,
    outer_iterations: int = 5
) -> List[float]:
    times = []

    config = PyConfig()
    config.endpoint = "file:///tmp/telemetry"
    config.telemetry_heartbeat_interval = 1000

    tags = (("tag1", "value1"), ("tag2", "value2"))

    for _ in range(outer_iterations):
        start = time.time()
        worker = NativeTelemetryWorker(
            host="test-host",
            service="test-service",
            config=config
        )
        worker.send_start()

        contexts = []
        for context_type in (PyMetricType.Count, PyMetricType.Gauge, PyMetricType.Distribution):
            contexts.append(worker.register_metric_context(
                name=f"test.metric.f{str(context_type)}",
                tags=tags,
                metric_type=context_type,
                common=True,
                namespace=PyMetricNamespace.Tracers
            ))

        for context in contexts:
            for _ in range(inner_iterations):
                worker.add_point(
                    value=int(random.random() * 100),
                    context=context,
                    extra_tags=None,
                )

        for i in range(inner_iterations):
            worker.add_log("identifier", f"some message{i}", 0, "")
            worker.add_integration(f"someintegration{i}", True, f"1.{i}", True, False)
            worker.add_config(f"someconfig{i}", f"somevalue{i}", "someorigin")

        times.append(time.time() - start)
        worker.send_stop()

    return times

def run_python_benchmark(
    inner_iterations: int,
    outer_iterations: int = 5
) -> List[float]:
    times = []

    tags = (("tag1", "value1"), ("tag2", "value2"))
    ddtrace.internal.telemetry.telemetry_writer.disable()
    for _ in range(outer_iterations):
        writer = TelemetryWriter()
        writer.enable()

        # Create metrics and send points
        start = time.time()

        for func in (writer.add_count_metric, writer.add_gauge_metric, writer.add_distribution_metric):
            for i in range(inner_iterations):
                func(
                    TELEMETRY_NAMESPACE.TRACERS,
                    f"test.metric.{i}",
                    int(random.random() * 100),
                    tags=tags
                )

        for i in range(inner_iterations):
            writer.add_log(TELEMETRY_LOG_LEVEL.ERROR, f"some message{i}", 0, "")
            writer.add_integration(f"someintegration{i}", True, f"1.{i}", True, False)
            writer.add_configuration(f"someconfig{i}", f"somevalue{i}", "someorigin")

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
    INNER_ITERATIONS = 10000
    OUTER_ITERATIONS = 5

    print(f"Running benchmarks with:")
    print(f"- {INNER_ITERATIONS} points per metric")
    print(f"- {OUTER_ITERATIONS} iterations")

    # Run Native (Rust) benchmarks
    native_times = run_native_benchmark(INNER_ITERATIONS, OUTER_ITERATIONS)
    print_stats("Native (Rust) Implementation", native_times)

    # Run Python benchmarks
    python_times = run_python_benchmark(INNER_ITERATIONS, OUTER_ITERATIONS)
    print_stats("Python Implementation", python_times)

    # Calculate and print speedup
    avg_speedup = statistics.mean(python_times) / statistics.mean(native_times)
    print(f"\nAverage speedup (Python/Native): {avg_speedup:.2f}x")

if __name__ == "__main__":
    main()
