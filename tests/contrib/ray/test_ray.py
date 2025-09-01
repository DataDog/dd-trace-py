import os
import subprocess

import pytest

from tests.utils import TracerTestCase


def submit_ray_job(script_name, timeout=60):
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

    cmd = ["ray", "job", "submit", "--", "python", script_path]

    print(f"\n{' '.join(cmd)}\n")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    print(result.stdout)

    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)

    return result


RAY_SNAPSHOT_IGNORES = [
    # Ray-specific dynamic values that change between runs
    "meta.ray.job_id",
    "meta.ray.node_id",
    "meta.ray.worker_id",
    "meta.ray.actor_id",
    "meta.ray.task_id",
    "meta.ray.submission_id",
    "meta.tracestate",
    "meta.traceparent",
    "meta.error.message",
    "meta.ray.job.message",
    "meta.error.stack",
    # Service names that include dynamic submission IDs
    "service",
]


class TestRayIntegration(TracerTestCase):
    """Test Ray integration with actual cluster setup and job submission"""

    @classmethod
    def setUpClass(cls):
        super(TestRayIntegration, cls).setUpClass()

        try:
            subprocess.run(["ray", "stop", "--force"], capture_output=True, check=False)
        except FileNotFoundError:
            pytest.skip("Ray CLI not available")

        try:
            # Start the ray cluster once for all tests
            subprocess.run(
                ["ddtrace-run", "ray", "start", "--head", "--dashboard-host=127.0.0.1", "--port=0"],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            pytest.skip(f"Failed to start Ray cluster: {e.stderr}")

    @classmethod
    def tearDownClass(cls):
        try:
            # Stop the ray cluster once after all tests
            subprocess.run(["ray", "stop"], capture_output=True, check=False)
        except FileNotFoundError:
            pass

        super(TestRayIntegration, cls).tearDownClass()

    @pytest.mark.snapshot(token="tests.contrib.ray.test_ray.test_simple_task", ignores=RAY_SNAPSHOT_IGNORES)
    def test_simple_task(self):
        submit_ray_job("jobs/simple_task.py")

    @pytest.mark.snapshot(token="tests.contrib.ray.test_ray.test_nested_tasks", ignores=RAY_SNAPSHOT_IGNORES)
    def test_nested_taskx(self):
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
