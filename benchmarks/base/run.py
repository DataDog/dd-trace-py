#!/usr/bin/env python3

import importlib.util
import json
import os
from pathlib import Path
import queue
import shutil
import struct
import subprocess
import sys
import threading
import time
from typing import Any
from typing import Optional

import yaml


SHOULD_PROFILE = os.environ.get("PROFILE_BENCHMARKS", "0") == "1"

# EXPERIMENT (do not merge): T6 of the CPU-asymmetry probe (PR #20052 /
# APMSP-4059). Each config's taskset'd process tree is counted (cycles,
# instructions, cache misses, task-clock, context switches, migrations, page
# faults) so the readout can compare CPU 24 against 25/36/37. Preferred tool:
# `perf stat -x,` wrapping the tree (inheritance is perf stat's default), one
# CSV per config and side next to the results. Events are user-only (:u): the
# CI containers run unprivileged under perf_event_paranoid=2, which forbids
# kernel-inclusive counts, and the benchmark workload itself is user space.
# Fallback when the perf binary is missing but perf_event_open works: a
# stdlib ctypes counting event per metric opened on the benchmark process
# with inherit=1 (hardware events ask for exclude_kernel for the same reason).
# Every failure is recorded and degrades to an unwrapped run; nothing here
# can fail the job.
PERF_STAT_EVENTS = (
    "task-clock,cycles:u,instructions:u,cache-references:u,cache-misses:u,context-switches,cpu-migrations,page-faults"
)

_watch_module = None
_watch_module_loaded = False
_watch_module_error = ""
_perf_probe_state: Optional[dict] = None


def append_placement_record(
    output_dir: str, cname: str, cpus: Optional[list[int]], start: float, end: float, pid: int
) -> None:
    # EXPERIMENT (do not merge): join key between a config's result and the CPU
    # watch's per-core samples -- which CPUs the config ran on and when. The
    # side (candidate/baseline) is the output dir's name; run-benchmarks.sh
    # passes "$ARTIFACTS_DIR/<side>" as output_dir. Written after the config
    # finishes so the timed path is unchanged. See PR #20052 / APMSP-4059.
    side = os.path.basename(os.path.normpath(output_dir))
    record = {
        "scenario": os.environ.get("SCENARIO"),
        "side": side,
        "config": cname,
        "cpus": cpus,
        "start": start,
        "end": end,
        "pid": pid,
    }
    with open(os.path.join(output_dir, "placement.jsonl"), "a") as fp:
        fp.write(json.dumps(record) + "\n")


def read_config(path):
    with open(path) as fp:
        return yaml.load(fp, Loader=yaml.FullLoader)


def effective_cpu_affinity(output_dir: str) -> Optional[str]:
    # EXPERIMENT (do not merge): per-side CPU override for the asymmetry probe
    # (T5, PR #20052 / APMSP-4059). run-benchmarks.sh exports CPU_AFFINITY=24-35
    # for the candidate and 36-47 for the baseline; when BENCH_CPUS_<SIDE> is set
    # for this side -- inferred from the output dir name, which run-benchmarks.sh
    # sets to "$ARTIFACTS_DIR/<side>" -- the override replaces that side's
    # affinity for this process. The container allows CPUs 24-47.
    side = os.path.basename(os.path.normpath(output_dir))
    override = os.environ.get("BENCH_CPUS_" + side.upper())
    if override:
        print(f"Side {side!r} CPU override: {override} (replaces CPU_AFFINITY)")
    return override or os.environ.get("CPU_AFFINITY")


def cpu_affinity_to_cpu_groups(cpu_affinity: str, cpus_per_run: int) -> list[list[int]]:
    # CPU_AFFINITY is a comma-separated list of CPU IDs and ranges
    #   6-11
    #   6-11,14,15
    #   6-11,13-15,16,18,20-21
    cpu_ids: list[int] = []
    for part in cpu_affinity.split(","):
        if "-" in part:
            start, end = part.split("-")
            cpu_ids.extend(range(int(start), int(end) + 1))
        else:
            cpu_ids.append(int(part))

    if len(cpu_ids) % cpus_per_run != 0:
        raise ValueError(f"CPU count {len(cpu_ids)} not divisible by CPUS_PER_RUN={cpus_per_run}")
    cpu_groups = [cpu_ids[i : i + cpus_per_run] for i in range(0, len(cpu_ids), cpus_per_run)]
    return cpu_groups


def _load_watch_module():
    # EXPERIMENT (T6): the repo's watch.py already carries the ctypes
    # perf_event_open plumbing (attr struct, raw syscall) for its own passive
    # counters; load it instead of duplicating kernel-ABI code in the benchmark
    # harness. The harness copies this run.py next to the scenario it runs, so
    # the repo-relative path is only one of the candidates: CI_PROJECT_DIR (set
    # in the benchmark jobs) and the cwd cover the copied-layout cases. The
    # last error is kept so perf_probe can explain a missing fallback.
    global _watch_module, _watch_module_loaded, _watch_module_error
    if _watch_module_loaded:
        return _watch_module
    _watch_module_loaded = True
    bases = []
    project = os.environ.get("CI_PROJECT_DIR")
    if project:
        bases.append(Path(project))
    try:
        bases.append(Path(__file__).resolve().parents[2])
    except IndexError:
        pass
    bases.append(Path.cwd())
    for base in bases:
        path = base / ".gitlab" / "benchmarks" / "steps" / "watch.py"
        try:
            if not path.is_file():
                continue
            spec = importlib.util.spec_from_file_location("cpu_probe_watch", path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _watch_module = module
            return _watch_module
        except Exception as exc:
            _watch_module_error = "%s (tried %s)" % (exc, path)
    _watch_module_error = _watch_module_error or "no watch.py under any of %s" % (bases,)
    return _watch_module


def perf_probe() -> dict:
    """Decide once per process how to count the benchmark tree: perf stat if a
    functional trial succeeds, else the ctypes counter if perf_event_open
    accepts a per-process software event, else nothing. The decision (and its
    reason) is printed once and cached; never raises.
    """
    global _perf_probe_state
    if _perf_probe_state is not None:
        return _perf_probe_state
    state: dict[str, Any] = {"mode": "none", "reason": ""}
    if shutil.which("perf"):
        try:
            trial = subprocess.run(
                ["perf", "stat", "-x,", "-e", PERF_STAT_EVENTS, "-o", os.devnull, "--", "true"],
                capture_output=True,
                timeout=120,
            )
            if trial.returncode == 0:
                state = {"mode": "perf", "reason": ""}
            else:
                state["reason"] = "perf stat trial rc=%d: %s" % (
                    trial.returncode,
                    trial.stderr.decode("utf-8", "replace").strip()[:300],
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            state["reason"] = "perf stat trial: %s" % exc
    else:
        state["reason"] = "perf binary missing"
    if state["mode"] == "none" and state["reason"] == "perf binary missing":
        watch = _load_watch_module()
        if watch is None:
            state["reason"] = (
                "perf binary missing and watch.py not loadable for the ctypes fallback: %s" % _watch_module_error
            )
        else:
            try:
                fd = watch.perf_event_open(
                    watch.PerfEventAttr(watch.PERF_TYPE_SOFTWARE, watch.PERF_COUNT_SW_TASK_CLOCK), 0, -1
                )
                os.close(fd)
                state = {"mode": "ctypes", "reason": ""}
            except OSError as exc:
                state["reason"] = "perf binary missing and perf_event_open blocked: %s" % exc
    print("perf probe (T6): %s" % json.dumps(state))
    _perf_probe_state = state
    return state


class CtypesPerfCounter:
    """perf stat replacement for when the perf binary is missing but
    perf_event_open works: one inherit=1 counting event per metric, opened on
    the benchmark process right after spawn and read after exit. Values are
    stored raw with their enabled/running times so the readout can scale any
    kernel multiplexing; hardware events that need exclude_kernel (unprivileged
    perf_event_paranoid) retry with it and the constraint is recorded.
    """

    # (metric, type, config); ids from perf_event_open(2)
    EVENTS = (
        ("task-clock", 1, 1),
        ("cycles", 0, 0),
        ("instructions", 0, 1),
        ("cache-references", 0, 2),
        ("cache-misses", 0, 3),
        ("context-switches", 1, 3),
        ("cpu-migrations", 1, 4),
        ("page-faults", 1, 2),
    )

    def __init__(self, pid: int):
        watch = _load_watch_module()
        if watch is None:
            raise RuntimeError("watch module not loadable")
        self.watch = watch
        self.fds: dict[str, int] = {}
        self.exclude_kernel = True
        read_format = watch._READ_TIME_ENABLED | watch._READ_TIME_RUNNING
        for name, ptype, config in self.EVENTS:
            # user-only by default (perf_event_paranoid=2): hardware events
            # ask for exclude_kernel; only an ancient kernel rejecting the
            # bit (EINVAL) retries kernel-inclusive
            attr = watch.PerfEventAttr(
                ptype,
                config,
                inherit=True,
                exclude_kernel=ptype == watch.PERF_TYPE_HARDWARE,
                read_format=read_format,
            )
            try:
                self.fds[name] = watch.perf_event_open(attr, pid, -1)
            except OSError:
                if ptype != watch.PERF_TYPE_HARDWARE or not attr.flags & watch._BIT_EXCLUDE_KERNEL:
                    self.close()
                    raise
                attr = watch.PerfEventAttr(ptype, config, inherit=True, read_format=read_format)
                try:
                    self.fds[name] = watch.perf_event_open(attr, pid, -1)
                    self.exclude_kernel = False
                except OSError:
                    self.close()
                    raise

    def close(self) -> None:
        for fd in self.fds.values():
            try:
                os.close(fd)
            except OSError:
                pass
        self.fds = {}

    def write_output(self, output_dir: str, cname: str) -> None:
        # PERF_FORMAT_TOTAL_TIME_ENABLED|TOTAL_TIME_RUNNING layout: the counter
        # value then its enabled and running times (ns); the readout scales
        # value by enabled/running when the kernel multiplexed the event.
        metrics = {}
        for name, fd in self.fds.items():
            try:
                data = os.read(fd, 24)
                if len(data) == 24:
                    value, enabled, running = struct.unpack("<QQQ", data)
                    metrics[name] = {"value": value, "enabled": enabled, "running": running}
            except OSError:
                continue
        out = {"mode": "ctypes", "exclude_kernel": self.exclude_kernel, "metrics": metrics}
        (Path(output_dir) / ("perf.%s.json" % cname)).write_text(json.dumps(out, indent=1) + "\n")
        self.close()


def run(scenario_py: str, cname: str, cvars: dict[str, Any], output_dir: str, cpus: Optional[list[int]] = None):
    cmd: list[str] = []

    if cpus:
        # Use taskset to set CPU affinity
        cpu_list_str = ",".join(str(cpu) for cpu in cpus)
        cmd += ["taskset", "-c", cpu_list_str]

    if SHOULD_PROFILE:
        # viztracer won't create the missing directory itself
        viztracer_output_dir = Path(output_dir) / "viztracer"
        viztracer_output_dir.mkdir(parents=True, exist_ok=True)

        cmd += [
            "viztracer",
            "--minimize_memory",
            "--min_duration",
            "5",
            "--max_stack_depth",
            "200",
            "--output_file",
            str(viztracer_output_dir / f"{cname}.json"),
            "--",
        ]
    else:
        cmd += ["python"]

    cmd += [
        scenario_py,
        # necessary to copy PYTHONPATH for venvs
        "--copy-env",
        "--output",
        str(Path(output_dir) / f"results.{cname}.json"),
        "--name",
        cname,
    ]
    for cvarname, cvarval in cvars.items():
        cmd.append(f"--{cvarname}")
        if isinstance(cvarval, (dict, list)):
            # convert dicts and lists to JSON strings
            cmd.append(json.dumps(cvarval))
        else:
            cmd.append(str(cvarval))

    # EXPERIMENT (T6): count the whole taskset'd tree. perf stat is outermost
    # (taskset execs the benchmark in place, so the measured pid stays the
    # spawned one); the ctypes counter attaches to the spawned pid after
    # Popen with inherit=1. Either way the timed path is unchanged and any
    # failure degrades to an unwrapped run.
    probe = perf_probe()
    counter = None
    if probe["mode"] == "perf":
        cmd = [
            "perf",
            "stat",
            "-x,",
            "-e",
            PERF_STAT_EVENTS,
            "-o",
            str(Path(output_dir) / ("perf.%s.txt" % cname)),
            "--",
        ] + cmd

    proc = subprocess.Popen(cmd)
    if probe["mode"] == "ctypes":
        try:
            counter = CtypesPerfCounter(proc.pid)
        except (OSError, RuntimeError):
            counter = None
    start = time.time()
    proc.wait()
    if counter is not None:
        try:
            counter.write_output(output_dir, cname)
        except OSError:
            counter.close()
    append_placement_record(output_dir, cname, cpus, start, time.time(), proc.pid)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <output dir>")
        sys.exit(1)

    output_dir = sys.argv[1]
    print(f"Saving results to {output_dir}")
    config = read_config("config.yaml")

    # Filter configs if BENCHMARK_CONFIGS is set
    benchmark_configs = os.environ.get("BENCHMARK_CONFIGS")
    if benchmark_configs:
        allowed_configs = set(c.strip() for c in benchmark_configs.split(","))
        config = {k: v for k, v in config.items() if k in allowed_configs}
        print("Filtering to configs: {}".format(", ".join(sorted(config.keys()))))

    CPU_AFFINITY = effective_cpu_affinity(output_dir)

    # No CPU affinity specified, run sequentially
    if not CPU_AFFINITY:
        for cname, cvars in config.items():
            run("scenario.py", cname, cvars, output_dir)
        sys.exit(0)

    CPUS_PER_RUN = int(os.environ.get("CPUS_PER_RUN", "1"))
    cpu_groups = cpu_affinity_to_cpu_groups(CPU_AFFINITY, CPUS_PER_RUN)

    print(f"Running with CPU affinity: {CPU_AFFINITY}")
    print(f"CPUs per run: {CPUS_PER_RUN}")
    print(f"CPU groups: {list(cpu_groups)}")

    job_queue = queue.Queue()
    cpu_queue = queue.Queue()

    def worker(cpu_queue: queue.Queue, job_queue: queue.Queue):
        while job_queue.qsize() > 0:
            cname, cvars = job_queue.get(timeout=1)

            cpus = cpu_queue.get()
            print(f"Starting run {cname} on CPUs {cpus}")
            run("scenario.py", cname, cvars, output_dir, cpus=cpus)
            print(f"Finished run {cname}")
            cpu_queue.put(cpus)

    for cname, cvars in config.items():
        job_queue.put((cname, cvars))

    workers = []
    print(f"Starting {len(cpu_groups)} worker threads")
    for cpus in cpu_groups:
        cpu_queue.put(cpus)
        t = threading.Thread(target=worker, args=(cpu_queue, job_queue))
        t.start()
        workers.append(t)

    for t in workers:
        t.join()
    print("All runs completed.")
