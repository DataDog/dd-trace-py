from collections import defaultdict
import os
import re

import pytest

from ddtrace.compat import httplib
from ddtrace.vendor import six
from tests import snapshot
from tests.contrib.uwsgi import run_uwsgi


uwsgi_app = os.path.join(os.path.dirname(__file__), "uwsgi-app.py")

HTTP_PORT = 9091


@pytest.fixture
def uwsgi():
    cmd = ["uwsgi", "--need-app", "--die-on-term", "--http-socket", ":{}".format(HTTP_PORT), "--wsgi-file", uwsgi_app]
    yield run_uwsgi(cmd)


@snapshot(async_mode=False)
def test_uwsgi_single_request(uwsgi):
    proc = uwsgi("--enable-threads", "--processes", "1", "--master")
    _wait_uwsgi_workers_spawned(proc, 1)
    try:
        conn = httplib.HTTPConnection("localhost", HTTP_PORT)
        conn.request("GET", "/")
        resp = conn.getresponse()
        assert resp.status == 200
    finally:
        proc.terminate()
        proc.wait()


@pytest.mark.skipif(six.PY2, reason="Does not work on Python2")
@snapshot(async_mode=False)
def test_uwsgi_multiple_requests(uwsgi):
    proc = uwsgi("--enable-threads", "--processes", "2", "--master")
    _wait_uwsgi_workers_spawned(proc, 2)
    try:
        for _ in range(10):
            conn = httplib.HTTPConnection("localhost", HTTP_PORT)
            conn.request("GET", "/")
            resp = conn.getresponse()
            assert resp.status == 200
    finally:
        proc.terminate()
        proc.wait()


@snapshot(async_mode=False)
def test_uwsgi_single_request_with_runtime_metrics(uwsgi, monkeypatch):
    monkeypatch.setenv("DD_RUNTIME_METRICS_ENABLED", "1")
    monkeypatch.setenv("DD_RUNTIME_METRICS_INTERVAL", "1")
    proc = uwsgi("--enable-threads", "--processes", "2", "--master")
    _wait_uwsgi_workers_spawned(proc, 2)
    try:
        conn = httplib.HTTPConnection("localhost", HTTP_PORT)
        conn.request("GET", "/")
        resp = conn.getresponse()
        assert resp.status == 200
        runtime_metrics = _get_counts_by_group(
            proc.stdout, r"^DEBUG:ddtrace.internal.runtime.runtime_metrics:Writing metric (.*):", 5
        )
        assert max(runtime_metrics.values()) == 5
    finally:
        proc.terminate()
        proc.wait()


@pytest.mark.skipif(six.PY2, reason="Does not work on Python2")
@snapshot(async_mode=False)
def test_uwsgi_respawning(uwsgi, monkeypatch):
    monkeypatch.setenv("DD_RUNTIME_METRICS_ENABLED", "1")
    monkeypatch.setenv("DD_RUNTIME_METRICS_INTERVAL", "1")
    proc = uwsgi(
        "--enable-threads", "--processes", "2", "--master", "--min-worker-lifetime", "1", "--max-worker-lifetime", "2"
    )
    _wait_uwsgi_workers_spawned(proc, 2)
    try:
        for _ in range(10):
            conn = httplib.HTTPConnection("localhost", HTTP_PORT)
            conn.request("GET", "/")
            resp = conn.getresponse()
            assert resp.status == 200
        respawns = _get_counts_by_group(proc.stdout, r"^Respawned uWSGI worker (\d+)", 5)
        assert len(respawns.keys()) == 2 and set(respawns.values()) == set([5])
    finally:
        proc.terminate()
        proc.wait()


def _get_counts_by_group(stdout, group_re, max_count):
    counts = defaultdict(int)
    while True:
        line = stdout.readline()
        if line == b"":
            break
        else:
            m = re.match(group_re, line.decode())
            if m:
                counts[m.group(1)] += 1
                if set(counts.values()) == set([max_count]):
                    break

    return counts


def _wait_uwsgi_workers_spawned(proc, num_workers):
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
