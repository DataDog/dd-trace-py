#!/usr/bin/env python3
"""
Long-running script that throws various exceptions for testing exception profiling.

Usage:
    # Without profiling (baseline)
    python test_exception_profiling.py

    # With exception profiling enabled
    DD_PROFILING_ENABLED=true DD_PROFILING_EXCEPTION_ENABLED=true python test_exception_profiling.py

    # With profiling + output to file
    DD_PROFILING_ENABLED=true \
    DD_PROFILING_EXCEPTION_ENABLED=true \
    DD_PROFILING_OUTPUT_PPROF=profiles/exception_test \
    python test_exception_profiling.py
"""

import glob
import os
import random
import sys
import time


# =============================================================================
# Exception hierarchy for testing
# =============================================================================


class DatabaseError(Exception):
    """Simulated database error."""

    pass


class DBConnectionError(DatabaseError):
    """Database connection failed."""

    pass


class QueryError(DatabaseError):
    """Query execution failed."""

    pass


class ValidationError(Exception):
    """Input validation failed."""

    pass


class RateLimitError(Exception):
    """Rate limit exceeded."""

    pass


# =============================================================================
# Functions that throw exceptions at various stack depths
# =============================================================================


def deep_call_chain(depth: int, exc_type: type, message: str):
    """Create a deep call stack before raising."""
    if depth <= 0:
        raise exc_type(message)
    else:
        deep_call_chain(depth - 1, exc_type, message)


def simulate_database_query(query_id: int):
    """Simulate a database query that sometimes fails."""
    # 10% chance of connection error
    if random.random() < 0.10:
        deep_call_chain(5, DBConnectionError, f"Connection timeout for query {query_id}")

    # 5% chance of query error
    if random.random() < 0.05:
        raise QueryError(f"Syntax error in query {query_id}")


def simulate_validation(data: dict):
    """Simulate input validation."""
    if "email" not in data:
        raise ValidationError("Missing required field: email")

    if not data.get("email", "").endswith("@example.com"):
        deep_call_chain(3, ValidationError, "Invalid email domain")


def simulate_rate_limiter(request_id: int):
    """Simulate a rate limiter."""
    # 2% chance of rate limit
    if random.random() < 0.02:
        raise RateLimitError(f"Rate limit exceeded for request {request_id}")


def simulate_iterator():
    """Simulate iterator exhaustion (StopIteration)."""
    items = iter([1, 2, 3])
    while True:
        try:
            next(items)
        except StopIteration:
            break


def simulate_key_lookup():
    """Simulate dict key errors (common pattern)."""
    data = {"a": 1, "b": 2}
    keys_to_try = ["a", "b", "c", "d", "e"]

    for key in keys_to_try:
        try:
            _ = data[key]
        except KeyError:
            pass  # Expected for missing keys


def simulate_chained_exception():
    """Simulate exception chaining (raise from)."""
    try:
        deep_call_chain(4, DBConnectionError, "Primary DB down")
    except DBConnectionError:
        try:
            # Try fallback
            deep_call_chain(3, DBConnectionError, "Fallback DB also down")
        except DBConnectionError as e2:
            raise DatabaseError("All databases unavailable") from e2


def simulate_request(request_id: int):
    """Simulate a single request with various exception scenarios."""

    # Rate limiting check
    try:
        simulate_rate_limiter(request_id)
    except RateLimitError:
        return  # Request rejected

    # Input validation
    try:
        # 30% have invalid data
        if random.random() < 0.30:
            simulate_validation({})  # Missing email
        else:
            simulate_validation({"email": "user@example.com"})
    except ValidationError:
        pass  # Validation failed, but we continue

    # Database operations
    try:
        simulate_database_query(request_id)
    except DatabaseError:
        # Try with chained exception handling
        try:
            simulate_chained_exception()
        except DatabaseError:
            pass  # All DBs failed

    # Common patterns that throw exceptions
    simulate_iterator()
    simulate_key_lookup()


# =============================================================================
# Profile utilities
# =============================================================================


def decompress_pprof_files(output_prefix: str):
    """Decompress zstd-compressed pprof files for use with go tool pprof."""
    try:
        import zstandard as zstd
    except ImportError:
        print("  Note: Install 'zstandard' package to auto-decompress pprof files")
        return []

    # Find all pprof files matching the prefix
    pattern = f"{output_prefix}.*.pprof"
    pprof_files = glob.glob(pattern)

    if not pprof_files:
        print(f"  No pprof files found matching: {pattern}")
        return []

    decompressed_files = []
    for pprof_file in sorted(pprof_files):
        # Skip already decompressed files
        if ".decompressed." in pprof_file:
            continue

        output_file = pprof_file.replace(".pprof", ".decompressed.pprof")

        try:
            dctx = zstd.ZstdDecompressor()
            with open(pprof_file, "rb") as f_in:
                with open(output_file, "wb") as f_out:
                    dctx.copy_stream(f_in, f_out)

            size = os.path.getsize(output_file)
            print(f"  Decompressed: {output_file} ({size:,} bytes)")
            decompressed_files.append(output_file)
        except Exception as e:
            print(f"  Failed to decompress {pprof_file}: {e}")

    return decompressed_files


# =============================================================================
# Main loop
# =============================================================================


def main():
    print("=" * 60)
    print("Exception Profiling Test Script")
    print("=" * 60)
    print()

    # Check if profiling is enabled
    import os

    profiling_enabled = os.environ.get("DD_PROFILING_ENABLED", "").lower() in ("true", "1")
    exception_profiling = os.environ.get("DD_PROFILING_EXCEPTION_ENABLED", "").lower() in ("true", "1")

    print(f"Python version: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    print(f"DD_PROFILING_ENABLED: {profiling_enabled}")
    print(f"DD_PROFILING_EXCEPTION_ENABLED: {exception_profiling}")
    print()

    if profiling_enabled:
        print("Starting Datadog profiler...")
        from ddtrace.profiling import Profiler

        profiler = Profiler()
        profiler.start()
        print("Profiler started!")
        print()

    # Configuration
    DURATION_SECONDS = 360
    REQUESTS_PER_SECOND = 100

    print(f"Running for {DURATION_SECONDS} seconds at ~{REQUESTS_PER_SECOND} requests/sec")
    print()

    # Counters
    total_requests = 0

    start_time = time.time()
    last_report = start_time

    try:
        while (time.time() - start_time) < DURATION_SECONDS:
            batch_start = time.time()

            # Process a batch of requests
            for _ in range(REQUESTS_PER_SECOND):
                total_requests += 1
                simulate_request(total_requests)

            # Report progress every 10 seconds
            now = time.time()
            if now - last_report >= 10:
                elapsed = now - start_time
                rate = total_requests / elapsed
                print(f"[{elapsed:.0f}s] Processed {total_requests:,} requests ({rate:.0f}/sec)")
                last_report = now

            # Sleep to maintain target rate
            batch_duration = time.time() - batch_start
            sleep_time = max(0, 1.0 - batch_duration)
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        print("\nInterrupted by user")

    finally:
        elapsed = time.time() - start_time
        print()
        print("=" * 60)
        print("Summary")
        print("=" * 60)
        print(f"Total time: {elapsed:.1f} seconds")
        print(f"Total requests: {total_requests:,}")
        print(f"Average rate: {total_requests / elapsed:.0f} requests/sec")
        print()

        if profiling_enabled:
            print("Stopping profiler and flushing data...")
            profiler.stop()
            print("Done!")


            # If exception profiling is enabled, try to get stats
            if exception_profiling:
                try:
                    for collector in profiler._profiler._collectors:
                        if hasattr(collector, "get_stats"):
                            stats = collector.get_stats()
                            print()
                            print("Exception Profiler Stats:")
                            print(f"  Total exceptions seen: {stats.get('total_exceptions', 'N/A'):,}")
                            print(f"  Exceptions sampled: {stats.get('sampled_exceptions', 'N/A'):,}")
                except Exception as e:
                    print(f"Could not get exception stats: {e}")

            # Decompress pprof files if output was specified
            output_pprof = os.environ.get("DD_PROFILING_OUTPUT_PPROF")
            if output_pprof:
                print()
                decompressed = decompress_pprof_files(output_pprof)
                if decompressed:
                    print()
                    print(f"go tool pprof -http=:8080 {decompressed[0]}")


if __name__ == "__main__":
    main()
