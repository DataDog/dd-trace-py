#!/usr/bin/env python3
"""
Benchmark script to measure lock counts and overhead for each scenario.

This script runs each simulator and collects:
- Total lock counts
- Memory usage
- CPU overhead
- Throughput metrics

Can be run with or without the Lock Profiler to measure overhead.
"""

import argparse
import gc
import os
import subprocess
import sys
import time
import tracemalloc
from typing import Dict, List, Tuple

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def measure_baseline_locks() -> Dict[str, int]:
    """Measure baseline lock counts in Python runtime"""
    import threading
    
    # Count locks in threading module
    baseline = {
        "_active_limbo_lock": 1,  # threading._active_limbo_lock
        "_shutdown_locks": 1,     # threading._shutdown_locks
    }
    
    return baseline


def run_scenario_and_measure(
    scenario: str,
    duration: int,
    with_profiling: bool
) -> Dict:
    """Run a scenario and measure lock usage"""
    
    # Set up environment
    env = os.environ.copy()
    
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
        "dogweb": {
            "script": "dogweb_simulator.py",
            "args": ["--rps", "50", "--duration", str(duration)]
        },
        "trace": {
            "script": "trace_agent_simulator.py", 
            "args": ["--sps", "500", "--duration", str(duration)]
        },
        "profiler": {
            "script": "profiler_backend_simulator.py",
            "args": ["--pps", "25", "--duration", str(duration)]
        }
    }
    
    config = scenario_configs.get(scenario)
    if not config:
        raise ValueError(f"Unknown scenario: {scenario}")
    
    script_path = os.path.join(script_dir, config["script"])
    
    # Build command
    if with_profiling:
        cmd = ["ddtrace-run", "python", script_path] + config["args"]
    else:
        cmd = ["python", script_path] + config["args"]
    
    # Measure
    print(f"Running: {' '.join(cmd)}")
    start_time = time.time()
    
    result = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True
    )
    
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
        "duration": elapsed,
        "lock_count": lock_count,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr
    }


def run_inline_benchmark(scenario: str, duration: int) -> Tuple[int, Dict]:
    """Run benchmark inline (in-process) for more accurate measurements"""
    
    # Start memory tracking
    tracemalloc.start()
    gc.collect()
    
    start_memory = tracemalloc.get_traced_memory()[0]
    start_time = time.time()
    
    if scenario == "dogweb":
        from dogweb_simulator import DogwebSimulator, simulate_traffic
        
        simulator = DogwebSimulator(
            num_db_pools=3,
            pool_size=20,
            num_cache_clients=2,
            num_workers=4
        )
        
        try:
            simulate_traffic(simulator, rps=50, duration_seconds=duration)
            lock_count = simulator.count_locks()
            metrics = simulator.get_metrics()
        finally:
            simulator.shutdown()
    
    elif scenario == "trace":
        from trace_agent_simulator import TraceAgentSimulator, simulate_traffic
        
        agent = TraceAgentSimulator(
            num_buffers=10,
            buffer_capacity=10000,
            num_writers=2
        )
        agent.start_processors(4)
        
        try:
            simulate_traffic(agent, spans_per_second=500, duration=duration)
            lock_count = agent.count_locks()
            metrics = agent.get_metrics()
        finally:
            agent.shutdown()
    
    elif scenario == "profiler":
        from profiler_backend_simulator import ProfileProcessor, simulate_traffic
        
        processor = ProfileProcessor()
        processor.start_workers(4)
        
        try:
            simulate_traffic(processor, profiles_per_sec=25, duration=duration)
            time.sleep(1)  # Let processing complete
            lock_count = processor.count_locks()
            metrics = processor.get_metrics()
        finally:
            processor.shutdown()
    
    else:
        raise ValueError(f"Unknown scenario: {scenario}")
    
    elapsed = time.time() - start_time
    
    gc.collect()
    end_memory = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    
    return lock_count, {
        "elapsed_sec": elapsed,
        "memory_delta_bytes": end_memory - start_memory,
        "metrics": metrics
    }


def main():
    parser = argparse.ArgumentParser(description="Benchmark lock scenarios")
    parser.add_argument("--scenario", choices=["dogweb", "trace", "profiler", "all"],
                       default="all", help="Scenario to benchmark")
    parser.add_argument("--duration", type=int, default=30, help="Duration per scenario")
    parser.add_argument("--inline", action="store_true", 
                       help="Run benchmarks inline (more accurate but requires imports)")
    parser.add_argument("--compare-profiling", action="store_true",
                       help="Compare with and without profiling")
    args = parser.parse_args()
    
    scenarios = ["dogweb", "trace", "profiler"] if args.scenario == "all" else [args.scenario]
    
    print("=" * 70)
    print("LOCK SCENARIO BENCHMARKS")
    print("=" * 70)
    print(f"Duration per scenario: {args.duration}s")
    print(f"Mode: {'inline' if args.inline else 'subprocess'}")
    print()
    
    results = []
    
    for scenario in scenarios:
        print(f"\n{'=' * 40}")
        print(f"Scenario: {scenario}")
        print("=" * 40)
        
        if args.inline:
            # Run inline for accurate measurements
            lock_count, metrics = run_inline_benchmark(scenario, args.duration)
            
            print(f"  Lock count: {lock_count}")
            print(f"  Elapsed: {metrics['elapsed_sec']:.2f}s")
            print(f"  Memory delta: {metrics['memory_delta_bytes'] / 1024:.1f} KB")
            print(f"  Metrics: {metrics['metrics']}")
            
            results.append({
                "scenario": scenario,
                "lock_count": lock_count,
                **metrics
            })
        else:
            # Run as subprocess
            result = run_scenario_and_measure(scenario, args.duration, with_profiling=False)
            print(f"  Lock count: {result['lock_count']}")
            print(f"  Elapsed: {result['duration']:.2f}s")
            results.append(result)
            
            if args.compare_profiling:
                print("\n  With Lock Profiling:")
                result_profiled = run_scenario_and_measure(scenario, args.duration, with_profiling=True)
                print(f"    Lock count: {result_profiled['lock_count']}")
                print(f"    Elapsed: {result_profiled['duration']:.2f}s")
                print(f"    Overhead: {(result_profiled['duration'] / result['duration'] - 1) * 100:.1f}%")
                results.append(result_profiled)
    
    # Summary table
    print("\n")
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"{'Scenario':<20} {'Locks':<10} {'Duration':<12} {'Memory':<15}")
    print("-" * 70)
    
    for r in results:
        scenario = r.get("scenario", "?")
        locks = r.get("lock_count", 0)
        duration = r.get("elapsed_sec", r.get("duration", 0))
        memory = r.get("memory_delta_bytes", 0)
        
        print(f"{scenario:<20} {locks:<10} {duration:<12.2f} {memory / 1024:<15.1f} KB")


if __name__ == "__main__":
    main()

