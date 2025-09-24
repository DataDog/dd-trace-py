import logging
import os
import re
import subprocess
import time

import pytest

from tests.utils import TracerTestCase


def submit_ray_job(script_name, timeout=180):
    """
    Submit a Ray job

    Args:
        script_name (str): Name of the script file (without path)
        timeout (int): Timeout for job submission in seconds
    Returns:
        subprocess.CompletedProcess: Result of the job submission command
    """
    script_path = os.path.join(os.path.dirname(__file__), script_name)

    if not os.path.exists(script_path):
        raise FileNotFoundError(f"Script not found: {script_path}")

    # Use a minimal working directory to avoid packaging the entire repository when submitting jobs.
    # Packaging the repo can be extremely slow and cause timeouts in CI.
    jobs_dir = os.path.dirname(script_path)
    cmd = [
        "ray",
        "job",
        "submit",
        "--working-dir",
        jobs_dir,
        "--no-wait",
        "--",
        "python",
        os.path.basename(script_path),
    ]

    print(f"\n{' '.join(cmd)}\n")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    print(result.stdout)

    if result.returncode != 0:
        logging.error("Failed to submit Ray job. Error: %s", result.stderr)
        raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)

    # Parse submission id from output, e.g., "Job 'raysubmit_XXXX' submitted successfully"
    submission_id = None
    m = re.search(r"Job '([A-Za-z0-9_]+)' submitted successfully", result.stdout)
    if m:
        submission_id = m.group(1)
    else:
        m = re.search(r"(raysubmit_[A-Za-z0-9]+)", result.stdout)
        if m:
            submission_id = m.group(1)

    if not submission_id:
        raise RuntimeError(f"Could not parse Ray submission id from output: {result.stdout}\n{result.stderr}")

    # Poll for completion within the provided timeout
    deadline = time.time() + timeout
    status_cmd = ["ray", "job", "status", submission_id]
    while time.time() < deadline:
        status_res = subprocess.run(status_cmd, capture_output=True, text=True, timeout=30)
        status_text = (status_res.stdout or "").upper()
        if "SUCCEEDED" in status_text:
            time.sleep(10)
            return result
        if "FAILED" in status_text or "STOPPED" in status_text:
            # Print status output to help diagnose failures
            if status_res.stdout:
                print("\n[ray job status stdout]\n" + status_res.stdout)
            if status_res.stderr:
                print("\n[ray job status stderr]\n" + status_res.stderr)
            time.sleep(10)
            raise subprocess.CalledProcessError(1, status_cmd, status_res.stdout, status_res.stderr)
        time.sleep(1)

    # Timed out waiting for job completion
    raise subprocess.TimeoutExpired(status_cmd, timeout)


RAY_SNAPSHOT_IGNORES = [
    # Ray-specific dynamic values that change between runs
    "meta.ray.job_id",
    "meta.ray.node_id",
    "meta.ray.worker_id",
    "meta.ray.actor_id",
    "meta.ray.task_id",
    "meta.ray.submission_id",
    "meta.ray.pid",
    "meta.tracestate",
    "meta.traceparent",
    "meta.error.message",
    "meta.ray.job.message",
    "meta.error.stack",
    "meta._dd.base_service",
    "meta._dd.hostname",
    # Actor method empty arguments are encoded differently between ray versions
    "meta.ray.actor_method.args",
    # Base service sometimes gets set to a different value in CI than in the local environment,
    # ignore it to make the tests pass in both environments
    "meta._dd.base_service",
    "meta._dd.hostname",
    "metrics._dd.partial_version",
    "metrics._dd.was_long_running",
]


class TestRayIntegration(TracerTestCase):
    """Test Ray integration with actual cluster setup and job submission"""

    @classmethod
    def setUpClass(cls):
        super(TestRayIntegration, cls).setUpClass()
        try:
            # Start the ray cluster once for all tests
            subprocess.run(
                [
                    "RAY_memory_monitor_refresh_ms=0",
                    "ray",
                    "start",
                    "--head",
                    "--dashboard-host=127.0.0.1",
                    "--port=0",
                    "--tracing-startup-hook=ddtrace.contrib.ray:setup_tracing",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            pytest.skip(f"Failed to start Ray cluster: {e.stderr}")
        except Exception as e:
            pytest.skip(f"Failed to wait for Ray cluster readiness: {e}")

    @classmethod
    def tearDownClass(cls):
        try:
            # Stop the ray cluster once after all tests
            subprocess.run(["ray", "stop", "--force"], capture_output=True, check=False)
        except FileNotFoundError:
            pass

        super(TestRayIntegration, cls).tearDownClass()

    @pytest.mark.snapshot(token="tests.contrib.ray.test_ray.test_simple_task", ignores=RAY_SNAPSHOT_IGNORES)
    def test_simple_task(self):
        submit_ray_job("jobs/simple_task.py")

    @pytest.mark.snapshot(token="tests.contrib.ray.test_ray.test_nested_tasks", ignores=RAY_SNAPSHOT_IGNORES)
    def test_nested_tasks(self):
        submit_ray_job("jobs/nested_tasks.py")

    @pytest.mark.snapshot(token="tests.contrib.ray.test_ray.test_simple_actor", ignores=RAY_SNAPSHOT_IGNORES)
    def test_simple_actor(self):
        submit_ray_job("jobs/simple_actor.py")

    @pytest.mark.snapshot(token="tests.contrib.ray.test_ray.test_actor_and_task", ignores=RAY_SNAPSHOT_IGNORES)
    def test_actor_and_task(self):
        submit_ray_job("jobs/actor_and_task.py")

    @pytest.mark.snapshot(token="tests.contrib.ray.test_ray.test_actor_interactions", ignores=RAY_SNAPSHOT_IGNORES)
    def test_actor_interactions(self):
        submit_ray_job("jobs/actor_interactions.py")

    @pytest.mark.snapshot(token="tests.contrib.ray.test_ray.error_in_task", ignores=RAY_SNAPSHOT_IGNORES)
    def test_error_in_task(self):
        with pytest.raises(subprocess.CalledProcessError):
            submit_ray_job("jobs/error_in_task.py")

    @pytest.mark.snapshot(token="tests.contrib.ray.test_ray.test_simple_wait", ignores=RAY_SNAPSHOT_IGNORES)
    def test_simple_wait(self):
        submit_ray_job("jobs/simple_wait.py")
