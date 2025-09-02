#!/usr/bin/env python3
"""
Concurrent visualization of global vs thread-local vs ContextVar counters
across processes, threads, and async coroutines.

Run:
  PYTHONPATH=. python ddtrace/appsec/_iast/_taint_tracking/bin/ctx_concurrency_viz.py

You'll be prompted to choose which counter type to test:
- global
- threadlocal
- contextvar

Controls:
- Press 'q' to quit early
"""
from __future__ import annotations

import asyncio
import contextvars
import curses
import multiprocessing as mp
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Tuple

# -----------------------------
# Counter implementations
# -----------------------------

# Global integer (per-process)
CONTEXT_ID: int = 0

# Thread-local integer
CONTEXT_ID_THREAD_LOCAL = threading.local()

# ContextVar integer
CONTEXT_ID_VAR: contextvars.ContextVar[int] = contextvars.ContextVar("iast_var", default=0)


def get_context_id_global() -> int:
    global CONTEXT_ID
    try:
        v = CONTEXT_ID
    except NameError:
        v = 0
    v += 1
    CONTEXT_ID = v
    return v


def get_context_id_thread() -> int:
    v = getattr(CONTEXT_ID_THREAD_LOCAL, "value", 0)
    v += 1
    CONTEXT_ID_THREAD_LOCAL.value = v
    return v


def get_context_id_contextvar() -> int:
    v = CONTEXT_ID_VAR.get()
    v += 1
    CONTEXT_ID_VAR.set(v)
    return v

# -----------------------------
# Worker model
# -----------------------------

@dataclass(frozen=True)
class Ident:
    process: int
    thread: int
    coro: int


def _select_func(kind: str):
    if kind == "global":
        return get_context_id_global
    if kind == "threadlocal":
        return get_context_id_thread
    if kind == "contextvar":
        return get_context_id_contextvar
    raise ValueError(f"Unknown kind: {kind}")


async def _coro_loop(kind: str, proc_idx: int, thread_idx: int, coro_idx: int, out_q: mp.Queue, period: float, stop_ts: float):
    func = _select_func(kind)
    ident = (proc_idx, thread_idx, coro_idx)
    while time.time() < stop_ts:
        val = func()
        try:
            out_q.put_nowait((ident, val, time.time()))
        except Exception:
            pass
        await asyncio.sleep(period)


async def _thread_ticker_loop(kind: str, proc_idx: int, thread_idx: int, out_q: mp.Queue, period: float, stop_ts: float):
    """Periodically emit thread-level counter values (coro_idx = -1)."""
    func = _select_func(kind)
    ident = (proc_idx, thread_idx, -1)
    while time.time() < stop_ts:
        val = func()
        try:
            out_q.put_nowait((ident, val, time.time()))
        except Exception:
            pass
        await asyncio.sleep(period)


def _proc_ticker(kind: str, proc_idx: int, out_q: mp.Queue, period: float, stop_ts: float):
    """Emit process-level counter values (thread_idx = -1, coro_idx = -1)."""
    func = _select_func(kind)
    ident = (proc_idx, -1, -1)
    while time.time() < stop_ts:
        val = func()
        try:
            out_q.put_nowait((ident, val, time.time()))
        except Exception:
            pass
        time.sleep(period)


def _thread_worker(kind: str, proc_idx: int, thread_idx: int, coros_per_thread: int, out_q: mp.Queue, period: float, stop_ts: float):
    try:
        # Dedicated event loop per thread
        asyncio.set_event_loop(asyncio.new_event_loop())
        loop = asyncio.get_event_loop()
        async def delayed_coro(i: int):
            # ensure thread ticker has a head start
            await asyncio.sleep(1.0)
            return await _coro_loop(kind, proc_idx, thread_idx, i, out_q, period, stop_ts)

        tasks = [loop.create_task(_thread_ticker_loop(kind, proc_idx, thread_idx, out_q, period, stop_ts))]
        tasks += [loop.create_task(delayed_coro(i)) for i in range(coros_per_thread)]

        loop.run_until_complete(asyncio.gather(*tasks))
    finally:
        try:
            loop.close()
        except Exception:
            pass


def worker_process(kind: str, proc_idx: int, threads_per_process: int, coros_per_thread: int, out_q: mp.Queue, period: float, duration: float):
    stop_ts = time.time() + duration
    # Start a process-level ticker thread
    proc_t = threading.Thread(target=_proc_ticker, args=(kind, proc_idx, out_q, period, stop_ts), daemon=True)
    proc_t.start()
    # allow process ticker to advance visibly
    time.sleep(1.0)
    threads: List[threading.Thread] = []
    for t in range(threads_per_process):
        th = threading.Thread(
            target=_thread_worker,
            name=f"P{proc_idx}-T{t}",
            args=(kind, proc_idx, t, coros_per_thread, out_q, period, stop_ts),
            daemon=True,
        )
        th.start()
        threads.append(th)

    for th in threads:
        th.join()
    proc_t.join(timeout=0)

    # Signal completion for this process
    try:
        out_q.put(("DONE", proc_idx))
    except Exception:
        pass

# -----------------------------
# Visualization (curses)
# -----------------------------

class Viz:
    def __init__(self, stdscr, total_expected_done: int):
        self.stdscr = stdscr
        self.total_expected_done = total_expected_done
        self.latest: Dict[Ident, int] = {}
        self.history: Dict[Ident, List[int]] = {}
        curses.curs_set(0)
        self.stdscr.nodelay(True)

    def update(self, ident: Tuple[int, int, int], val: int):
        key = Ident(*ident)
        self.latest[key] = val
        h = self.history.setdefault(key, [])
        h.append(val)
        if len(h) > 8:
            del h[:-8]

    def render(self, status: str):
        self.stdscr.erase()
        maxy, maxx = self.stdscr.getmaxyx()
        self.stdscr.addstr(0, 0, status[: maxx - 1])
        self.stdscr.addstr(1, 0, "Lvl  Process  Thread  Coro   Last  History")
        self.stdscr.addstr(2, 0, "-" * min(maxx - 1, 80))
        row = 3
        # Sort for stable layout
        def _lvl(i: Ident) -> int:
            # P (-1,-1) < T (>=0,-1) < C (>=0,>=0)
            if i.thread == -1 and i.coro == -1:
                return 0
            if i.coro == -1:
                return 1
            return 2

        def _lvl_char(i: Ident) -> str:
            return "P" if _lvl(i) == 0 else ("T" if _lvl(i) == 1 else "C")

        for ident in sorted(self.latest.keys(), key=lambda i: (_lvl(i), i.process, i.thread, i.coro)):
            last = self.latest[ident]
            hist = " ".join(map(str, self.history.get(ident, [])[-8:]))
            line = f"{_lvl_char(ident):^3}  {ident.process:^7}  {ident.thread:^6}  {ident.coro:^4}  {last:^5}  {hist}"
            if row < maxy - 1:
                self.stdscr.addstr(row, 0, line[: maxx - 1])
                row += 1
        self.stdscr.addstr(maxy - 1, 0, "Press 'q' to quit")
        self.stdscr.refresh()


def _input_choice() -> str:
    print("Select counter kind to test:")
    print("  1) global")
    print("  2) threadlocal")
    print("  3) contextvar")
    choice = input("Enter 1/2/3 [3]: ").strip() or "3"
    mapping = {"1": "global", "2": "threadlocal", "3": "contextvar"}
    return mapping.get(choice, "contextvar")


def main():
    kind = _input_choice()
    # Defaults
    processes = int(os.environ.get("CTX_PROC", "2"))
    threads_per_process = int(os.environ.get("CTX_THREADS", "2"))
    coros_per_thread = int(os.environ.get("CTX_COROS", "2"))
    duration = float(os.environ.get("CTX_DURATION", "8"))
    period = float(os.environ.get("CTX_PERIOD", "0.05"))

    ctx = mp.get_context("spawn")
    out_q: mp.Queue = ctx.Queue(maxsize=10000)

    procs: List[mp.Process] = []
    for p in range(processes):
        pr = ctx.Process(
            target=worker_process,
            args=(kind, p, threads_per_process, coros_per_thread, out_q, period, duration),
            daemon=True,
        )
        pr.start()
        procs.append(pr)

    expected_done = processes

    def _curses_main(stdscr):
        viz = Viz(stdscr, expected_done)
        done_count = 0
        t0 = time.time()
        t_end = t0 + duration

        # Controller-side dynamic spawners (proc id 99 to distinguish)
        controller_next_thread = 0
        controller_next_coro = 0

        def spawn_controller_thread():
            nonlocal controller_next_thread
            th_idx = controller_next_thread
            controller_next_thread += 1
            stop_ts = t_end
            th = threading.Thread(
                target=_thread_worker,
                name=f"CTRL-T{th_idx}",
                args=(kind, 99, th_idx, 0, out_q, period, stop_ts),
                daemon=True,
            )
            th.start()

        def spawn_controller_coro():
            nonlocal controller_next_coro
            coro_idx = controller_next_coro
            controller_next_coro += 1
            stop_ts = t_end

            def _run_one_coro():
                asyncio.set_event_loop(asyncio.new_event_loop())
                loop = asyncio.get_event_loop()
                loop.run_until_complete(
                    _coro_loop(kind, 99, -1, coro_idx, out_q, period, stop_ts)
                )
                try:
                    loop.close()
                except Exception:
                    pass

            th = threading.Thread(target=_run_one_coro, name=f"CTRL-C{coro_idx}", daemon=True)
            th.start()

        def reset_main_counter():
            # Reset only within the controller/main process context
            global CONTEXT_ID
            if kind == "global":
                CONTEXT_ID = 0
            elif kind == "threadlocal":
                # Reset value for current (main) thread only
                try:
                    del CONTEXT_ID_THREAD_LOCAL.value
                except Exception:
                    pass
            elif kind == "contextvar":
                CONTEXT_ID_VAR.set(0)

        while done_count < expected_done:
            try:
                msg = out_q.get_nowait()
                if msg == None:
                    pass
                elif isinstance(msg, tuple) and len(msg) == 2 and msg[0] == "DONE":
                    done_count += 1
                else:
                    ident, val, ts = msg
                    viz.update(ident, val)
            except queue.Empty:
                pass

            elapsed = time.time() - t0
            viz.render(
                f"Mode={kind}  Procs={processes} Threads={threads_per_process} Coros={coros_per_thread}  Elapsed={elapsed:0.1f}s  Done={done_count}/{expected_done}"
            )
            # Key handling
            try:
                ch = stdscr.getch()
                if ch in (ord("q"), ord("Q")):
                    break
                elif ch in (ord("p"), ord("P")):
                    spawn_controller_thread()
                elif ch in (ord("c"), ord("C")):
                    spawn_controller_coro()
                elif ch == ord("0"):
                    reset_main_counter()
            except Exception:
                pass
            time.sleep(0.05)

    curses.wrapper(_curses_main)

    # Clean up
    for pr in procs:
        pr.join(timeout=1)


if __name__ == "__main__":
    try:
        mp.set_start_method("spawn", force=True)
    except RuntimeError:
        pass
    main()
