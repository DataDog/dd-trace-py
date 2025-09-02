#!/usr/bin/env python3
"""
Micro-benchmark Initializer vs ApplicationContext for context map ops.

Usage:
  PYTHONPATH=. python ddtrace/appsec/_iast/_taint_tracking/bin/bench_appctx.py

Make sure the native module is built first.
"""
import time
from statistics import mean

from ddtrace.appsec._iast._taint_tracking import _native as nn

# Short aliases
init = nn.initializer
ctx = nn.context

N = 1000
R = 10


def bench(fn, repeat=R):
    times = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return 1e6 * mean(times)  # microseconds


def bench_loop(fn, n=N, repeat=R):
    def run_once():
        for _ in range(n):
            fn()
    return bench(run_once, repeat=repeat)


def main():
    # Warmup
    init.clear_tainting_maps()
    ctx.clear_tainting_maps()

    res = {}

    # Initializer
    res["init.create_tainting_map"] = bench(init.create_tainting_map_bench)
    res["init.get_tainting_map*x1000"] = bench_loop(init.get_tainting_map_bench)
    res["init.reset_context"] = bench(init.reset_context)

    # ApplicationContext
    # Create a context once and reuse the returned context_id for lookups
    ctx.create_context()
    for i in range(1000):
        context_id = get_context_id()
        ctx.get_context_map_bench(context_id)
    ctx.reset_context()

    res["ctx.create_context_map"] = bench(ctx.create_context_map_bench)
    res["ctx.get_context_map*x1000"] = bench_loop(lambda: ctx.get_context_map_bench(context_id))
    res["ctx.reset_context"] = bench(ctx.reset_context)

    width = max(len(k) for k in res) + 2
    print("Operation".ljust(width), "Avg time (us)")
    print("-" * (width + 14))
    for k, v in res.items():
        print(k.ljust(width), f"{v:,.1f}")


if __name__ == "__main__":
    main()
