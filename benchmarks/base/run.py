#!/usr/bin/env python3

import json
import os
import queue
import subprocess
import sys
import threading
from typing import Any
from typing import Optional

import yaml


SHOULD_PROFILE = os.environ.get("PROFILE_BENCHMARKS", "0") == "1"


def read_config(path):
    with open(path, "r") as fp:
        return yaml.load(fp, Loader=yaml.FullLoader)


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


def run(scenario_py: str, cname: str, cvars: dict[str, Any], output_dir: str, cpus: Optional[list[int]] = None):
    cmd: list[str] = []

    if cpus:
        # Use taskset to set CPU affinity
        cpu_list_str = ",".join(str(cpu) for cpu in cpus)
        cmd += ["taskset", "-c", cpu_list_str]

    if SHOULD_PROFILE:
        # viztracer won't create the missing directory itself
        viztracer_output_dir = os.path.join(output_dir, "viztracer")
        os.makedirs(viztracer_output_dir, exist_ok=True)

        cmd += [
            "viztracer",
            "--minimize_memory",
            "--min_duration",
            "5",
            "--max_stack_depth",
            "200",
            "--output_file",
            os.path.join(output_dir, "viztracer", "{}.json".format(cname)),
        ]
    else:
        cmd += ["python"]

    cmd += [
        scenario_py,
        # necessary to copy PYTHONPATH for venvs
        "--copy-env",
        "--output",
        os.path.join(output_dir, f"results.{cname}.json"),
        "--name",
        cname,
    ]
    for cvarname, cvarval in cvars.items():
        cmd.append("--{}".format(cvarname))
        if isinstance(cvarval, (dict, list)):
            # convert dicts and lists to JSON strings
            cmd.append(json.dumps(cvarval))
        else:
            cmd.append(str(cvarval))

    proc = subprocess.Popen(cmd)
    proc.wait()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: {} <output dir>".format(sys.argv[0]))
        sys.exit(1)

    output_dir = sys.argv[1]
    print("Saving results to {}".format(output_dir))
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
