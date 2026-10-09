#!/usr/bin/env python3

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from typing import Any
from typing import Optional

import yaml


SHOULD_PROFILE = os.environ.get("PROFILE_BENCHMARKS", "0") == "1"


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


def cpu_affinity_to_cpu_groups(
    cpu_affinity: str, cpus_per_run: int, exclude_cpus: Optional[list[int]] = None
) -> list[list[int]]:
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

    # EXPERIMENT (do not merge): BENCH_EXCLUDE_CPUS (T4b, PR #20052 /
    # APMSP-4059) drops the listed CPUs before groups are built, shifting every
    # later CPU up one slot -- excluding 24 and 36 moves config 0 from 24/36 to
    # 25/37.
    if exclude_cpus:
        excluded = set(exclude_cpus)
        cpu_ids = [cpu for cpu in cpu_ids if cpu not in excluded]

    if len(cpu_ids) % cpus_per_run != 0:
        raise ValueError(f"CPU count {len(cpu_ids)} not divisible by CPUS_PER_RUN={cpus_per_run}")
    cpu_groups = [cpu_ids[i : i + cpus_per_run] for i in range(0, len(cpu_ids), cpus_per_run)]
    return cpu_groups


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

    proc = subprocess.Popen(cmd)
    start = time.time()
    proc.wait()
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

    CPU_AFFINITY = os.environ.get("CPU_AFFINITY")

    # No CPU affinity specified, run sequentially
    if not CPU_AFFINITY:
        for cname, cvars in config.items():
            run("scenario.py", cname, cvars, output_dir)
        sys.exit(0)

    CPUS_PER_RUN = int(os.environ.get("CPUS_PER_RUN", "1"))
    # EXPERIMENT (do not merge): see cpu_affinity_to_cpu_groups above.
    exclude_env = os.environ.get("BENCH_EXCLUDE_CPUS", "")
    exclude_cpus = [int(cpu.strip()) for cpu in exclude_env.split(",") if cpu.strip()] or None

    cpu_groups = cpu_affinity_to_cpu_groups(CPU_AFFINITY, CPUS_PER_RUN, exclude_cpus)

    print(f"Running with CPU affinity: {CPU_AFFINITY}")
    if exclude_cpus:
        print(f"Excluding CPUs: {exclude_cpus}")
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
