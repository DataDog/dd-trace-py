from itertools import product
from contextlib import contextmanager
import re
import os
import shlex
import subprocess
import urllib.request

import pyperf
import requests
import tenacity


SERVER_URL = "http://0.0.0.0:8000/"
VARIANTS = [
    dict(PERF_ENABLE_TRACER="0", PERF_ENABLE_PROFILER="0", PERF_THREADS="0"),
    dict(PERF_ENABLE_TRACER="1", PERF_ENABLE_PROFILER="0", PERF_THREADS="0"),
    dict(PERF_ENABLE_TRACER="0", PERF_ENABLE_PROFILER="1", PERF_THREADS="0"),
    dict(PERF_ENABLE_TRACER="1", PERF_ENABLE_PROFILER="1", PERF_THREADS="0"),
    dict(PERF_ENABLE_TRACER="0", PERF_ENABLE_PROFILER="0", PERF_THREADS="1"),
    dict(PERF_ENABLE_TRACER="1", PERF_ENABLE_PROFILER="0", PERF_THREADS="1"),
    dict(PERF_ENABLE_TRACER="0", PERF_ENABLE_PROFILER="1", PERF_THREADS="1"),
    dict(PERF_ENABLE_TRACER="1", PERF_ENABLE_PROFILER="1", PERF_THREADS="1"),
]


@tenacity.retry(
    wait=tenacity.wait_fixed(1),
    stop=tenacity.stop_after_attempt(30),
)
def wait():
    r = requests.get(SERVER_URL)
    r.raise_for_status()


@contextmanager
def server(env):
    # copy over current environ
    env.update(os.environ)
    cmd = "gunicorn -c gunicorn.conf.py"
    proc = subprocess.Popen(
        shlex.split(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        env=env,
    )
    # make sure process has been started
    assert proc.poll() is None
    try:
        wait()
        yield
    finally:
        proc.terminate()
        proc.wait()


def time_sync_requests(loops, env):
    with server(env):
        range_it = range(loops)
        t0 = pyperf.perf_counter()
        for _ in range_it:
            r = requests.get(SERVER_URL)
            r.raise_for_status()
        dt = pyperf.perf_counter() - t0
        return dt


def time_concurrent_requests(loops, env, concurrency=20):
    # Use ab to time for number of loops with a given number of concurrent workers
    pattern = re.compile("Time taken for tests:\s*(\d+\.\d+) seconds")
    with server(env):
        cmd = "ab -n {} -c {} {}".format(loops * concurrency, concurrency, SERVER_URL)
        proc = subprocess.Popen(
            shlex.split(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, _ = proc.communicate()
        proc.wait()
        m = pattern.search(stdout.decode())

        if not m:
            print("Did not find timing in ab output: {}".format(stdout.decode()))
            return

        dt = float(m.groups()[0])
        return dt


if __name__ == "__main__":
    runner = pyperf.Runner()
    for variant in VARIANTS:
        name = "|".join(f"{k}:{v}" for (k, v) in variant.items()).lower()
        runner.bench_time_func("func:time_sync_requests|" + name, time_sync_requests, variant)
        runner.bench_time_func("func:time_concurrent_requests|" + name, time_concurrent_requests, variant)
