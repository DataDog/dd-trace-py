import os
import re
import subprocess
import time

from celery import Celery


# Ensure that when we call Celery chains, the root span has celery specific span tags
# The test_integration.py setup doesn't perfectly mimic the condition of a worker process running.
# This test runs the worker as a side so we can check the tracer logs afterwards to ensure expected span results.
# See https://github.com/DataDog/dd-trace-py/issues/11479
def test_task_chain_task_call_task():
    app = Celery("tasks")

    celery_worker_cmd = "ddtrace-run celery -A tasks worker -c 1 -l DEBUG -n uniquename1 -P solo"
    celery_task_runner_cmd = "ddtrace-run python run_tasks.py"

    # The commands need to run from the directory where this test file lives
    current_directory = str(os.path.dirname(__file__))

    worker_process = subprocess.Popen(
        celery_worker_cmd.split(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid,
        close_fds=True,
        cwd=current_directory,
    )

    max_wait_time = 10
    waited_so_far = 0
    # {app.control.inspect().active() returns {'celery@uniquename1': []} when the worker is running}
    while app.control.inspect().active() is None and waited_so_far < max_wait_time:
        time.sleep(1)
        waited_so_far += 1

    # The task should only run after the Celery worker has sufficient time to start up
    task_runner_process = subprocess.Popen(
        celery_task_runner_cmd.split(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid,
        close_fds=True,
        cwd=current_directory,
    )

    task_runner_process.wait()
    # Kill the process so it starts to send traces to the Trace Agent
    worker_process.kill()
    worker_logs = worker_process.stderr.read()

    # Check that the root span was created with one of the Celery specific tags, such as celery.correlation_id
    # Some versions of python seem to require escaping when using `re.search`:
    old_pattern_match = r"resource=\\'tests.contrib.celery.tasks.fn_a\\' type=\\'worker\\' .* tags=.*correlation_id.*"
    new_pattern_match = r"resource=\'tests.contrib.celery.tasks.fn_a\' type=\'worker\' .* tags=.*correlation_id.*"

    pattern_exists = (
        re.search(old_pattern_match, str(worker_logs)) is not None
        or re.search(new_pattern_match, str(worker_logs)) is not None
    )
    assert pattern_exists is not None
