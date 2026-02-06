#!/usr/bin/env python3
"""Benchmark exception profiling overhead."""

import os
import sys
import time


# Check Python version - exception profiling requires 3.10+
if sys.version_info < (3, 10):
    print(f"Python {sys.version_info.major}.{sys.version_info.minor} - Exception profiling requires Python 3.10+")
    sys.exit(1)

# Check if profiling is enabled
exception_profiling_enabled = os.environ.get("DD_PROFILING_EXCEPTION_ENABLED", "false").lower() == "true"
show_samples = os.environ.get("DD_SHOW_SAMPLES", "false").lower() == "true"

# Import profiler if enabled
if exception_profiling_enabled:
    from ddtrace.profiling import Profiler
    from ddtrace.profiling.collector import exception

# Storage for captured samples when DD_SHOW_SAMPLES=true
captured_samples = []


def create_deep_exception(depth):
    if depth <= 0:
        raise ValueError("Test exception")
    else:
        create_deep_exception(depth - 1)


def benchmark_exceptions(count, depth):
    exceptions_raised = 0

    for _ in range(count):
        try:
            create_deep_exception(depth)
        except ValueError:
            exceptions_raised += 1

    return exceptions_raised


def print_samples(samples, max_samples=5):
    print(f"\n--- Sample Exceptions (showing {min(len(samples), max_samples)} of {len(samples)}) ---")
    for i, sample in enumerate(samples[:max_samples]):
        print(f"\nSample {i + 1}:")
        print(f"  Type: {sample['exception_type']}")
        print(f"  Message: {sample.get('exception_message', 'N/A')}")
        print(f"  Stack trace ({len(sample['frames'])} frames):")
        # Show first 5 and last 5 frames if there are many
        frames = sample["frames"]
        if len(frames) <= 10:
            for j, frame in enumerate(frames):
                print(f"    {j + 1}. {frame['function']} ({frame['filename']}:{frame['lineno']})")
        else:
            for j, frame in enumerate(frames[:5]):
                print(f"    {j + 1}. {frame['function']} ({frame['filename']}:{frame['lineno']})")
            print(f"    ... ({len(frames) - 10} more frames) ...")
            for j, frame in enumerate(frames[-5:], start=len(frames) - 4):
                print(f"    {j}. {frame['function']} ({frame['filename']}:{frame['lineno']})")
    print("--- End Samples ---\n")


def main():
    global captured_samples

    # Warmup
    for _ in range(1000):
        try:
            create_deep_exception(100)
        except ValueError:
            pass

    # Configuration
    EXCEPTION_COUNT = 100_000
    STACK_DEPTH = 50

    status = 'ON' if exception_profiling_enabled else 'OFF'
    print(f"Python {sys.version_info.major}.{sys.version_info.minor} - Exception Profiling: {status}")

    if exception_profiling_enabled:
        # Start profiler with exception profiling
        profiler = Profiler(_exception_profiling_enabled=True)
        profiler.start()

        # If showing samples, monkey-patch the collector to capture samples
        if show_samples:
            for collector in profiler._collectors:
                if isinstance(collector, exception.ExceptionCollector):
                    # Save the original _collect_exception method
                    original_collect = collector._collect_exception

                    def capturing_collect(exc_type, exc_value, exc_traceback, _orig=original_collect):
                        # Capture sample data before calling original
                        exception_type = (
                            f"{exc_type.__module__}.{exc_type.__name__}" if exc_type.__module__ else exc_type.__name__
                        )
                        frames = []
                        tb = exc_traceback
                        while tb is not None:
                            frame = tb.tb_frame
                            code = frame.f_code
                            frames.append(
                                {
                                    "filename": code.co_filename,
                                    "function": code.co_name,
                                    "lineno": tb.tb_lineno,
                                }
                            )
                            tb = tb.tb_next

                        captured_samples.append(
                            {
                                "exception_type": exception_type,
                                "exception_message": str(exc_value),
                                "frames": frames,
                            }
                        )

                        # Call original
                        return _orig(exc_type, exc_value, exc_traceback)

                    collector._collect_exception = capturing_collect
                    break

    # Run benchmark
    start_time = time.perf_counter()
    exceptions = benchmark_exceptions(EXCEPTION_COUNT, STACK_DEPTH)
    elapsed = time.perf_counter() - start_time

    # Calculate metrics
    rate = exceptions / elapsed
    overhead_per_exception = (elapsed / exceptions) * 1_000_000  # microseconds

    print(f"Time: {elapsed:.3f}s")
    print(f"Rate: {rate:.0f} exc/s")
    print(f"Overhead: {overhead_per_exception:.2f} μs/exc")

    if exception_profiling_enabled:
        # Get exception collector stats
        for collector in profiler._collectors:
            if isinstance(collector, exception.ExceptionCollector):
                stats = collector.get_stats()
                print(f"Tracked: {stats['total_exceptions']} exceptions")
                print(f"Sampled: {stats['sampled_exceptions']} exceptions")
                break

        profiler.stop()

        # Print captured samples if enabled
        if show_samples and captured_samples:
            print_samples(captured_samples)


if __name__ == "__main__":
    main()
