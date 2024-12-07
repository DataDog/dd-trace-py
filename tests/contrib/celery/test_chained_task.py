import os
import signal
import subprocess
import time

import celery


# Ensure that when we call Celery chains, the root span has celery specific span tags
# The test_integration.py setup doesn't perfectly mimic the condition of a worker process running.
# This test runs the worker as a side so we can check the tracer logs afterwards to ensure expected span results.
# See https://github.com/DataDog/dd-trace-py/issues/11479
def test_task_chain_task_call_task():
    celery_worker_cmd = "ddtrace-run celery -A tests.contrib.celery.tasks worker -c 1 -l DEBUG -n uniquename1 -P solo"
    celery_task_runner_cmd = "ddtrace-run python3.11 tests/contrib/celery/run_tasks.py"
    worker_process = subprocess.Popen(
        celery_worker_cmd.split(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid,
        close_fds=True,
        cwd=str(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))),
    )

    # Wait a few seconds for the celery worker to start up before trying to run tasks
    time.sleep(5)
    task_runner_process = subprocess.Popen(
        celery_task_runner_cmd.split(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid,
        close_fds=True,
        cwd=str(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))),
    )

    task_runner_process.wait()

    # Kill the process so it starts to send traces to the Trace Agent
    os.killpg(worker_process.pid, signal.SIGKILL)
    time.sleep(5)
    worker_process.wait()
    worker_logs = worker_process.stderr.read()

    # TODO FIX THIS ASSERTION (since this log will be removed)
    # At the moment, this specific will fail on the `main` branch, which is exactly what we want
    assert "The task_postrun signal was not called" not in str(worker_logs)
