#!/usr/bin/env python3
"""
Async and concurrency validation for ApplicationContext behavior.

Usage:
  PYTHONPATH=. python ddtrace/appsec/_iast/_taint_tracking/bin/async_ctx_check.py [--processes N] [--threads M]

It will:
- create a context
- run multiple async coroutines that observe context id and slot addr across awaits
- optionally reset the context mid-flight
- optionally run in multiple threads and processes
"""
import argparse
import asyncio
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from ddtrace.appsec._iast._taint_tracking import _native as nn

ctx = nn.context


def fmt_id(val):
    return "None" if val is None else str(val)


def print_ctx(where: str) -> None:
    pid = os.getpid()
    tid = threading.get_ident()
    cid = ctx.get_context_id()
    addr = ctx.get_context_slot_addr()
    print(f"[pid={pid} tid={tid}] {where}: context_id={fmt_id(cid)} slot_addr=0x{addr:x}")


async def leaf(name: str, delay: float) -> None:
    print_ctx(f"{name}:start")
    await asyncio.sleep(delay)
    print_ctx(f"{name}:after_sleep_{delay}")


async def reset_midflight(delay: float) -> None:
    await asyncio.sleep(delay)
    print_ctx(f"reset_context:before")
    ctx.reset_context()
    print_ctx(f"reset_context:after")


async def async_workflow(num_tasks: int = 5, do_reset: bool = True) -> None:
    # ensure context exists
    created = ctx.create_context_map()
    print_ctx(f"create_context_map() -> {created}")

    tasks = []
    for i in range(num_tasks):
        tasks.append(asyncio.create_task(leaf(f"task-{i}", delay=0.02 * (i + 1))))

    if do_reset:
        # reset after some tasks have progressed
        tasks.append(asyncio.create_task(reset_midflight(0.05)))

    await asyncio.gather(*tasks)
    print_ctx("workflow:done")


def thread_entry(idx: int, num_tasks: int, do_reset: bool) -> None:
    print_ctx(f"thread-{idx}:entry")
    asyncio.run(async_workflow(num_tasks=num_tasks, do_reset=do_reset))
    print_ctx(f"thread-{idx}:exit")


def process_entry(proc_idx: int, threads: int, num_tasks: int, do_reset: bool) -> None:
    print_ctx(f"process-{proc_idx}:entry")
    # In each process, run multiple threads that each run their own event loop
    with ThreadPoolExecutor(max_workers=threads, thread_name_prefix=f"p{proc_idx}-t") as tp:
        futs = [tp.submit(thread_entry, i, num_tasks, do_reset) for i in range(threads)]
        for f in futs:
            f.result()
    print_ctx(f"process-{proc_idx}:exit")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--processes", type=int, default=1, help="number of processes to spawn")
    parser.add_argument("--threads", type=int, default=2, help="threads per process")
    parser.add_argument("--tasks", type=int, default=5, help="async tasks per thread")
    parser.add_argument("--no-reset", action="store_true", help="disable mid-flight reset_context()")
    args = parser.parse_args(argv)

    do_reset = not args.no_reset

    if args.processes <= 1:
        # single-process path
        process_entry(0, threads=args.threads, num_tasks=args.tasks, do_reset=do_reset)
    else:
        # multi-process path
        from multiprocessing import get_context

        mp = get_context("spawn")
        procs = []
        for pidx in range(args.processes):
            p = mp.Process(target=process_entry, args=(pidx, args.threads, args.tasks, do_reset))
            p.start()
            procs.append(p)
        rc = 0
        for p in procs:
            p.join()
            if p.exitcode != 0:
                rc = p.exitcode
        return rc

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
