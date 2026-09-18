#!/usr/bin/env python3

import json
import os
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


def assign_configs_to_cpu_groups(
    config: dict[str, Any], cpu_groups: list[list[int]]
) -> list[tuple[str, Any, list[int]]]:
    # The candidate and the baseline are separate run.py invocations. Cores 36-47
    # measure slower than 24-35, so a config only cancels that per-core offset if it
    # lands on the same cores in both runs. This mapping is therefore a pure function
    # of the config names and the core list: sort by name and assign by index, never
    # by whichever worker happens to free up first, and never by config.yaml's key
    # order. See https://github.com/DataDog/dd-trace-py/pull/20052.
    return [(cname, config[cname], cpu_groups[i % len(cpu_groups)]) for i, cname in enumerate(sorted(config))]


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
            "--",
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

    assignments = assign_configs_to_cpu_groups(config, cpu_groups)

    # One worker per core group, each draining only the configs assigned to that group.
    # Still len(cpu_groups) configs at a time, but a config can no longer migrate to a
    # different group between the candidate and the baseline runs. The cost is the loss
    # of load balancing: the slowest group now gates the run.
    jobs_by_group: dict[int, list[tuple[str, Any, list[int]]]] = {i: [] for i in range(len(cpu_groups))}
    for i, assignment in enumerate(assignments):
        jobs_by_group[i % len(cpu_groups)].append(assignment)

    def worker(jobs: list[tuple[str, Any, list[int]]]):
        for cname, cvars, cpus in jobs:
            print(f"Starting run {cname} on CPUs {cpus}")
            run("scenario.py", cname, cvars, output_dir, cpus=cpus)
            print(f"Finished run {cname}")

    workers = []
    print(f"Starting {len(cpu_groups)} worker threads")
    for jobs in jobs_by_group.values():
        t = threading.Thread(target=worker, args=(jobs,))
        t.start()
        workers.append(t)

    for t in workers:
        t.join()
    print("All runs completed.")
