from collections import defaultdict
import os
import re

import pytest

from ddtrace.compat import httplib
from tests import snapshot
from tests.contrib.uwsgi import run_uwsgi


uwsgi_app = os.path.join(os.path.dirname(__file__), "uwsgi-app.py")

HTTP_PORT = 9091


@pytest.fixture
def uwsgi():
    cmd = ["uwsgi", "--need-app", "--die-on-term", "--http-socket", ":{}".format(HTTP_PORT), "--wsgi-file", uwsgi_app]
    yield run_uwsgi(cmd)


@snapshot(async_mode=False)
def test_uwsgi(uwsgi):
    proc = uwsgi("--enable-threads", "--processes", "2", "--master")
    try:
        _check_uwsgi_workers_spawned(proc, 2)
        conn = httplib.HTTPConnection("localhost", HTTP_PORT)
        conn.request("GET", "/")
        resp = conn.getresponse()
        assert resp.status == 200
    finally:
        proc.terminate()
        proc.wait()


def test_uwsgi_runtime_metrics(uwsgi, monkeypatch):
    monkeypatch.setenv("DD_RUNTIME_METRICS_ENABLED", "1")
    monkeypatch.setenv("DD_RUNTIME_METRICS_INTERVAL", "1")

    proc = uwsgi("--enable-threads", "--processes", "2", "--master")
    try:
        _check_uwsgi_workers_spawned(proc, 2)
        conn = httplib.HTTPConnection("localhost", HTTP_PORT)
        conn.request("GET", "/")
        resp = conn.getresponse()
        assert resp.status == 200
        _check_runtime_workers(proc, 2, 5)
    finally:
        proc.terminate()
        proc.wait()


def test_uwsgi_runtime_metrics_respawn(uwsgi, monkeypatch):
    monkeypatch.setenv("DD_RUNTIME_METRICS_ENABLED", "1")
    monkeypatch.setenv("DD_RUNTIME_METRICS_INTERVAL", "1")
    proc = uwsgi(
        "--enable-threads", "--processes", "2", "--master", "--min-worker-lifetime", "1", "--max-worker-lifetime", "2"
    )
    try:
        _check_uwsgi_workers_spawned(proc, 2)
        conn = httplib.HTTPConnection("localhost", HTTP_PORT)
        conn.request("GET", "/")
        resp = conn.getresponse()
        assert resp.status == 200
        _check_runtime_workers(proc, 2, 5, respawns=True)
    finally:
        proc.terminate()
        proc.wait()


def _check_uwsgi_workers_spawned(proc, num_workers):
    pattern = r"^spawned uWSGI worker (\d+)"
    spawned = set()
    while True:
        line = proc.stdout.readline()
        if line == b"":
            break
        else:
            m = re.match(pattern, line.decode())
            if m:
                spawned.add(m.group(1))
                if len(spawned) == num_workers:
                    break
    assert len(spawned) == num_workers


def _check_runtime_workers(proc, num_workers, max_count, respawns=False):
    runtime_start_pattern = r"^DEBUG:ddtrace._worker:Starting RuntimeWorker thread"
    metrics_output_pattern = r"^DEBUG:ddtrace.internal.runtime.runtime_metrics:Writing metric (.*):"
    respawns_pattern = r"^Respawned uWSGI worker (\d+)"
    runtime_start_count = 0
    # keep track of counts for either respawns or runtime metrics outputted
    counts = defaultdict(int)
    while True:
        line = proc.stdout.readline()
        if line == b"":
            break
        else:
            if re.match(runtime_start_pattern, line.decode()):
                runtime_start_count += 1
            if respawns:
                m = re.match(respawns_pattern, line.decode())
                if m:
                    counts[m.group(1)] += 1
                    if min(counts.values()) >= max_count:
                        break
            else:
                m = re.match(metrics_output_pattern, line.decode())
                if m:
                    counts[m.group(1)] += 1
                    if min(counts.values()) >= max_count:
                        break

    # TODO: runtime workers is recreated with _check_new_process so we will see
    # more started potentially. after the runtime worker refactoring, this can be changed to an equality
    # this is not a problem for respawns though
    assert runtime_start_count >= num_workers

    if respawns:
        assert min(counts.values()) == max_count
        assert max(counts.values()) == max_count
    else:
        assert min(counts.values()) == max_count
        assert max(counts.values()) >= max_count
