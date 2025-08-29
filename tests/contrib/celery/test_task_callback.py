import os
import subprocess
import time

from celery import Celery
import pytest


@pytest.mark.snapshot(ignores=["meta.celery.hostname", "meta.celery.id"])
def test_task_error_callback_subprocess():
    """
    This test suite is set up the same way as the test chained task because it
    is closer to what happens in a real scenario.
    """
    app = Celery("tasks_with_error")

    # Generate unique node name to avoid conflicts
    celery_worker_cmd = "ddtrace-run celery -A tasks_with_error worker -c 1 -l DEBUG -n uniquename2 -P solo"
    celery_task_runner_cmd = "ddtrace-run python run_error_tasks.py"

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
    time.sleep(5)
    worker_process.kill()
