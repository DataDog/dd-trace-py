#!/usr/bin/env python3

import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Any
from typing import Optional

import pyperf
import yaml


SHOULD_PROFILE = os.environ.get("PROFILE_BENCHMARKS", "0") == "1"
# pyperf's default number of worker processes per benchmark
PYPERF_PROCESSES = 20
HAS_TASKSET = shutil.which("taskset") is not None


def read_config(path):
    with open(path) as fp:
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


def split_processes(total: int, chunks: int) -> list[int]:
    """Split total pyperf worker processes into at most chunks near-equal, non-empty parts."""
    chunks = max(1, min(chunks, total))
    base, extra = divmod(total, chunks)
    return [base + 1 if i < extra else base for i in range(chunks)]


def merge_results(chunk_files: list[Path], output_file: Path) -> None:
    suite = pyperf.BenchmarkSuite.load(str(chunk_files[0]))
    for chunk_file in chunk_files[1:]:
        suite.add_runs(pyperf.BenchmarkSuite.load(str(chunk_file)))
    suite.dump(str(output_file))


def run(
    scenario_py: str,
    cname: str,
    cvars: dict[str, Any],
    output_dir: str,
    cpus: Optional[list[int]] = None,
    output_file: Optional[Path] = None,
    extra_args: Optional[list[str]] = None,
) -> int:
    cmd: list[str] = []

    if cpus and HAS_TASKSET:
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
        str(output_file or Path(output_dir) / f"results.{cname}.json"),
        "--name",
        cname,
    ]
    cmd += extra_args or []
    for cvarname, cvarval in cvars.items():
        cmd.append(f"--{cvarname}")
        if isinstance(cvarval, (dict, list)):
            # convert dicts and lists to JSON strings
            cmd.append(json.dumps(cvarval))
        else:
            cmd.append(str(cvarval))

    proc = subprocess.Popen(cmd)
    return proc.wait()


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
    cpu_groups = cpu_affinity_to_cpu_groups(CPU_AFFINITY, CPUS_PER_RUN)

    # Each config's pyperf worker processes are split into chunks that run as separate jobs, so they land
    # on different CPUs and at different times. A noisy CPU then only skews part of a config's samples
    # instead of shifting the whole result.
    CHUNKS_PER_RUN = 1 if SHOULD_PROFILE else int(os.environ.get("CHUNKS_PER_RUN", "4"))
    process_chunks = split_processes(PYPERF_PROCESSES, CHUNKS_PER_RUN)

    print(f"Running with CPU affinity: {CPU_AFFINITY}")
    print(f"CPUs per run: {CPUS_PER_RUN}")
    print(f"CPU groups: {list(cpu_groups)}")
    print(f"pyperf processes per chunk: {process_chunks}")
    if not HAS_TASKSET:
        print("WARNING: taskset not found, runs will not be pinned to CPUs")

    chunk_dir = Path(tempfile.mkdtemp(prefix="benchmark-chunks-"))
    job_queue: queue.Queue = queue.Queue()
    results_lock = threading.Lock()
    chunk_results: dict[str, list[tuple[int, list[int], Optional[Path]]]] = {cname: [] for cname in config}

    def run_chunk(cname: str, cvars: dict[str, Any], index: int, loops: Optional[int], cpus: list[int]) -> None:
        nchunks = len(process_chunks)
        label = f"{cname} chunk {index + 1}/{nchunks}"
        chunk_file = chunk_dir / f"results.{cname}.{index}.json"
        extra_args = ["--processes", str(process_chunks[index])]
        # Only the first chunk calibrates; the rest reuse its loop count so all samples are comparable
        if loops is not None:
            extra_args += ["--loops", str(loops)]

        print(f"Starting run {label} ({process_chunks[index]} processes) on CPUs {cpus}")
        returncode = run(
            "scenario.py", cname, cvars, output_dir, cpus=cpus, output_file=chunk_file, extra_args=extra_args
        )
        succeeded = returncode == 0 and chunk_file.exists()
        print(f"Finished run {label} on CPUs {cpus}" + ("" if succeeded else f" (failed, exit code {returncode})"))

        if index == 0:
            if not succeeded:
                return
            loops = pyperf.Benchmark.load(str(chunk_file)).get_runs()[-1].get_loops()
            for i in range(1, nchunks):
                job_queue.put((cname, cvars, i, loops))

        with results_lock:
            chunk_results[cname].append((index, cpus, chunk_file if succeeded else None))
            if len(chunk_results[cname]) < nchunks:
                return
            chunks = sorted(chunk_results[cname])

        chunk_files = [f for _, _, f in chunks if f is not None]
        merge_results(chunk_files, Path(output_dir) / f"results.{cname}.json")
        print(f"Merged {len(chunk_files)}/{nchunks} chunks of {cname}, CPUs per chunk: {[c for _, c, _ in chunks]}")

    def worker(cpus: list[int]) -> None:
        while True:
            job = job_queue.get()
            if job is None:
                return
            try:
                run_chunk(*job, cpus=cpus)
            except Exception as e:
                print(f"Run {job[0]} chunk {job[2] + 1} failed: {e!r}")
            finally:
                job_queue.task_done()

    for cname, cvars in config.items():
        job_queue.put((cname, cvars, 0, None))

    workers = []
    print(f"Starting {len(cpu_groups)} worker threads")
    for cpus in cpu_groups:
        t = threading.Thread(target=worker, args=(cpus,))
        t.start()
        workers.append(t)

    # Chunks after the first are queued before the first one is marked done, so join() can't return early
    job_queue.join()
    for _ in workers:
        job_queue.put(None)
    for t in workers:
        t.join()
    shutil.rmtree(chunk_dir, ignore_errors=True)
    print("All runs completed.")
