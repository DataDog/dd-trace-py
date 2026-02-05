#!/usr/bin/env python3
"""
Benchmark script to measure lock counts and overhead for each scenario.

This script runs each simulator and collects:
- Total lock counts
- Memory usage
- CPU overhead
- Throughput metrics

Can be run with or without the Lock Profiler to measure overhead.

DoE (Design of Experiments) Features:
- JSON output format (--output json)
- Random seed control (--seed) for reproducibility
- Configurable warm-up phase (--warmup)
- All 5 scenarios supported
"""

import argparse
import gc
import json
import os
import random
import subprocess
import sys
import time
import tracemalloc
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple


# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# All available scenarios
ALL_SCENARIOS = ["dogweb", "trace", "profiler", "logs", "agent"]


def set_random_seed(seed: int) -> None:
    """Set random seed for reproducibility across all simulators"""
    random.seed(seed)
    # Note: Each simulator uses random module, so this affects them all


def measure_baseline_locks() -> Dict[str, int]:
    """Measure baseline lock counts in Python runtime"""
    # Count locks in threading module (these are always present)
    baseline = {
        "_active_limbo_lock": 1,  # threading._active_limbo_lock
        "_shutdown_locks": 1,  # threading._shutdown_locks
    }

    return baseline


def run_scenario_and_measure(scenario: str, duration: int, with_profiling: bool, seed: Optional[int] = None) -> Dict:
    """Run a scenario and measure lock usage"""

    # Set up environment
    env = os.environ.copy()

    if seed is not None:
        env["PYTHONHASHSEED"] = str(seed)

    if with_profiling:
        env["DD_PROFILING_ENABLED"] = "true"
        env["DD_PROFILING_LOCK_ENABLED"] = "true"
        env["DD_PROFILING_ENABLE_ASSERTS"] = "0"
        env["DD_SERVICE"] = f"{scenario}-benchmark"
        env["DD_ENV"] = "benchmark"
        # Disable sending to agent for benchmarks
        env["DD_TRACE_ENABLED"] = "false"
    else:
        env["DD_PROFILING_ENABLED"] = "false"
        env["DD_TRACE_ENABLED"] = "false"

    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Map scenario to script and args
    scenario_configs = {
        "dogweb": {"script": "dogweb_simulator.py", "args": ["--rps", "50", "--duration", str(duration)]},
        "trace": {"script": "trace_agent_simulator.py", "args": ["--sps", "500", "--duration", str(duration)]},
        "profiler": {"script": "profiler_backend_simulator.py", "args": ["--pps", "25", "--duration", str(duration)]},
        "logs": {"script": "logs_pipeline_simulator.py", "args": ["--eps", "500", "--duration", str(duration)]},
        "agent": {
            "script": "agent_checks_simulator.py",
            "args": ["--checks", "20", "--duration", str(duration)],
        },
    }

    config = scenario_configs.get(scenario)
    if not config:
        raise ValueError(f"Unknown scenario: {scenario}. Available: {ALL_SCENARIOS}")

    script_path = os.path.join(script_dir, config["script"])

    # Build command
    if with_profiling:
        cmd = ["ddtrace-run", "python", script_path] + config["args"]
    else:
        cmd = ["python", script_path] + config["args"]

    # Measure
    start_time = time.time()

    result = subprocess.run(cmd, env=env, capture_output=True, text=True)

    elapsed = time.time() - start_time

    # Parse output for lock count
    lock_count = 0
    for line in result.stdout.split("\n"):
        if "Total locks" in line or "locks created" in line:
            try:
                lock_count = int(line.split(":")[-1].strip())
            except ValueError:
                pass

    return {
        "scenario": scenario,
        "with_profiling": with_profiling,
        "duration_sec": elapsed,
        "lock_count": lock_count,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def run_inline_benchmark(
    scenario: str, duration: int, warmup_sec: int = 0, seed: Optional[int] = None
) -> Tuple[int, Dict[str, Any]]:
    """Run benchmark inline (in-process) for more accurate measurements"""

    # Set seed if provided
    if seed is not None:
        set_random_seed(seed)

    # Start memory tracking
    tracemalloc.start()
    gc.collect()

    start_memory = tracemalloc.get_traced_memory()[0]
    start_time = time.time()

    if scenario == "dogweb":
        from dogweb_simulator import DogwebSimulator
        from dogweb_simulator import simulate_traffic

        simulator = DogwebSimulator(num_db_pools=3, pool_size=20, num_cache_clients=2, num_workers=4)

        try:
            # Warm-up phase
            if warmup_sec > 0:
                simulate_traffic(simulator, rps=50, duration_seconds=warmup_sec)
                gc.collect()

            simulate_traffic(simulator, rps=50, duration_seconds=duration)
            lock_count = simulator.count_locks()
            metrics = simulator.get_metrics()
        finally:
            simulator.shutdown()

    elif scenario == "trace":
        from trace_agent_simulator import TraceAgentSimulator
        from trace_agent_simulator import simulate_traffic

        agent = TraceAgentSimulator(num_buffers=10, buffer_capacity=10000, num_writers=2)
        agent.start_processors(4)

        try:
            if warmup_sec > 0:
                simulate_traffic(agent, spans_per_second=500, duration=warmup_sec)
                gc.collect()

            simulate_traffic(agent, spans_per_second=500, duration=duration)
            lock_count = agent.count_locks()
            metrics = agent.get_metrics()
        finally:
            agent.shutdown()

    elif scenario == "profiler":
        from profiler_backend_simulator import ProfileProcessor
        from profiler_backend_simulator import simulate_traffic

        processor = ProfileProcessor()
        processor.start_workers(4)

        try:
            if warmup_sec > 0:
                simulate_traffic(processor, profiles_per_sec=25, duration=warmup_sec)
                gc.collect()

            simulate_traffic(processor, profiles_per_sec=25, duration=duration)
            time.sleep(1)  # Let processing complete
            lock_count = processor.count_locks()
            metrics = processor.get_metrics()
        finally:
            processor.shutdown()

    elif scenario == "logs":
        from logs_pipeline_simulator import LogsPipelineSimulator
        from logs_pipeline_simulator import simulate_traffic

        pipeline = LogsPipelineSimulator(num_intake_buffers=3, parser_concurrency=10, num_indexes=5)
        pipeline.start_workers(4)

        try:
            if warmup_sec > 0:
                simulate_traffic(pipeline, events_per_second=500, duration=warmup_sec)
                gc.collect()

            simulate_traffic(pipeline, events_per_second=500, duration=duration)
            time.sleep(1)  # Let processing complete
            lock_count = pipeline.count_locks()
            metrics = pipeline.get_stats()
        finally:
            pipeline.shutdown()

    elif scenario == "agent":
        from agent_checks_simulator import AgentChecksSimulator

        agent_sim = AgentChecksSimulator(num_checks=20, check_concurrency=10)
        agent_sim.start()

        try:
            if warmup_sec > 0:
                time.sleep(warmup_sec)
                gc.collect()

            time.sleep(duration)
            lock_count = agent_sim.count_locks()
            metrics = agent_sim.get_stats()
        finally:
            agent_sim.shutdown()

    else:
        raise ValueError(f"Unknown scenario: {scenario}. Available: {ALL_SCENARIOS}")

    elapsed = time.time() - start_time

    gc.collect()
    end_memory = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()

    return lock_count, {
        "elapsed_sec": elapsed,
        "memory_delta_bytes": end_memory - start_memory,
        "metrics": metrics,
    }


def format_results_json(results: List[Dict], args: argparse.Namespace) -> str:
    """Format results as JSON for DoE consumption"""
    output = {
        "benchmark": "realistic_lock_scenarios",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": {
            "duration_sec": args.duration,
            "warmup_sec": getattr(args, "warmup", 0),
            "seed": getattr(args, "seed", None),
            "mode": "inline" if args.inline else "subprocess",
        },
        "results": [],
    }

    for r in results:
        result_entry = {
            "scenario": r.get("scenario", "unknown"),
            "lock_count": r.get("lock_count", 0),
            "duration_sec": r.get("elapsed_sec", r.get("duration_sec", 0)),
            "memory_delta_bytes": r.get("memory_delta_bytes", 0),
        }

        # Include metrics if available (but not stdout/stderr)
        if "metrics" in r:
            result_entry["metrics"] = r["metrics"]

        if "with_profiling" in r:
            result_entry["with_profiling"] = r["with_profiling"]

        output["results"].append(result_entry)

    return json.dumps(output, indent=2, default=str)


def format_results_table(results: List[Dict]) -> str:
    """Format results as a human-readable table"""
    lines = []
    lines.append("=" * 70)
    lines.append("SUMMARY")
    lines.append("=" * 70)
    lines.append(f"{'Scenario':<20} {'Locks':<10} {'Duration':<12} {'Memory':<15}")
    lines.append("-" * 70)

    for r in results:
        scenario = r.get("scenario", "?")
        locks = r.get("lock_count", 0)
        duration = r.get("elapsed_sec", r.get("duration_sec", 0))
        memory = r.get("memory_delta_bytes", 0)

        lines.append(f"{scenario:<20} {locks:<10} {duration:<12.2f} {memory / 1024:<15.1f} KB")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark lock scenarios for DoE analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run all scenarios with JSON output
  python benchmark_locks.py --output json --duration 30

  # Run specific scenario with seed for reproducibility
  python benchmark_locks.py --scenario dogweb --seed 42 --inline

  # Run with warm-up phase for stable measurements
  python benchmark_locks.py --inline --warmup 5 --duration 30

  # Compare with/without profiling overhead
  python benchmark_locks.py --compare-profiling --duration 30
""",
    )
    parser.add_argument(
        "--scenario",
        choices=ALL_SCENARIOS + ["all"],
        default="all",
        help="Scenario to benchmark (default: all)",
    )
    parser.add_argument("--duration", type=int, default=30, help="Duration per scenario in seconds (default: 30)")
    parser.add_argument("--warmup", type=int, default=0, help="Warm-up duration in seconds before measurement")
    parser.add_argument(
        "--seed", type=int, default=None, help="Random seed for reproducibility (affects all random operations)"
    )
    parser.add_argument(
        "--inline",
        action="store_true",
        help="Run benchmarks inline (more accurate but requires imports)",
    )
    parser.add_argument(
        "--compare-profiling",
        action="store_true",
        help="Compare with and without profiling enabled",
    )
    parser.add_argument(
        "--output",
        choices=["table", "json"],
        default="table",
        help="Output format: table (human-readable) or json (for DoE)",
    )
    args = parser.parse_args()

    scenarios = ALL_SCENARIOS if args.scenario == "all" else [args.scenario]

    # Set global seed if provided
    if args.seed is not None:
        set_random_seed(args.seed)

    # Only print headers in table mode
    if args.output == "table":
        print("=" * 70)
        print("LOCK SCENARIO BENCHMARKS")
        print("=" * 70)
        print(f"Duration per scenario: {args.duration}s")
        print(f"Warm-up: {args.warmup}s")
        print(f"Mode: {'inline' if args.inline else 'subprocess'}")
        if args.seed is not None:
            print(f"Seed: {args.seed}")
        print()

    results = []

    for scenario in scenarios:
        if args.output == "table":
            print(f"\n{'=' * 40}")
            print(f"Scenario: {scenario}")
            print("=" * 40)

        if args.inline:
            # Run inline for accurate measurements
            lock_count, metrics = run_inline_benchmark(scenario, args.duration, warmup_sec=args.warmup, seed=args.seed)

            if args.output == "table":
                print(f"  Lock count: {lock_count}")
                print(f"  Elapsed: {metrics['elapsed_sec']:.2f}s")
                print(f"  Memory delta: {metrics['memory_delta_bytes'] / 1024:.1f} KB")
                print(f"  Metrics: {metrics['metrics']}")

            results.append({"scenario": scenario, "lock_count": lock_count, **metrics})
        else:
            # Run as subprocess
            result = run_scenario_and_measure(scenario, args.duration, with_profiling=False, seed=args.seed)

            if args.output == "table":
                print(f"  Lock count: {result['lock_count']}")
                print(f"  Elapsed: {result['duration_sec']:.2f}s")

            # Don't include stdout/stderr in results
            result_clean = {k: v for k, v in result.items() if k not in ("stdout", "stderr")}
            results.append(result_clean)

            if args.compare_profiling:
                if args.output == "table":
                    print("\n  With Lock Profiling:")

                result_profiled = run_scenario_and_measure(scenario, args.duration, with_profiling=True, seed=args.seed)

                if args.output == "table":
                    print(f"    Lock count: {result_profiled['lock_count']}")
                    print(f"    Elapsed: {result_profiled['duration_sec']:.2f}s")
                    overhead = (result_profiled["duration_sec"] / result["duration_sec"] - 1) * 100
                    print(f"    Overhead: {overhead:.1f}%")

                result_profiled_clean = {k: v for k, v in result_profiled.items() if k not in ("stdout", "stderr")}
                results.append(result_profiled_clean)

    # Output results
    if args.output == "json":
        print(format_results_json(results, args))
    else:
        print("\n")
        print(format_results_table(results))


if __name__ == "__main__":
    main()
