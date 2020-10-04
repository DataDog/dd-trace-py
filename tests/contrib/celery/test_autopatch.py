#!/usr/bin/env python
import subprocess
import time

import pytest


_CELERY_PROJ = "tests.contrib.celery.tasks"


@pytest.fixture(params=["ddtrace-run celery -A", "ddtrace-run python -m celery -A"], ids=["celery", "runpy"])
def celery_worker(request):
    proc = subprocess.Popen(
        request.param.split() + [_CELERY_PROJ, "worker"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    yield proc
    try:
        proc.terminate()
    except OSError:
        pass


def _wait_proc_started(proc):
    def _try():
        time.sleep(0.05)
        return proc.poll()

    tries = 0
    result = _try()

    while tries < 200:
        if result:
            break
        tries += 1
        result = _try()

    return result


def test_autopatch():
    out = subprocess.check_output(["ddtrace-run", "python", "tests/contrib/celery/autopatch.py"])
    assert out.startswith(b"Test success")


def test_run_celery_worker(celery_worker):
    assert _wait_proc_started(celery_worker) is None, "Celery worker process failed to start"
    out = subprocess.check_output(["celery", "-A", _CELERY_PROJ, "inspect", "ping"])
    assert out.decode("utf-8").strip().endswith("pong")
