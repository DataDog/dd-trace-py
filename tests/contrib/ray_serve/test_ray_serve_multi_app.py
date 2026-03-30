import os
from pathlib import Path
import signal
import subprocess
import time

import pytest
import requests


RAY_MULTI_APP_SNAPSHOT_IGNORES = [
    "meta.tracestate",
    "meta.ray.serve.handle_id",
    "meta.ray.serve.request_id",
    "meta.ray.serve.replica_id",
    "meta.ray.serve.deployment_id",
    "meta.ray.serve.handle_source",
    "meta.error.message",
    "meta.error.stack",
]
MULTI_APP_SERVE_DIR = Path(__file__).parent / "multi_app"


def _start_ray_cluster(env):
    # AIDEV-NOTE: Keep this explicit cluster small; `serve run` auto-starts Ray
    # with default object-store sizing, which is too large for constrained CI pods.
    subprocess.run(["ray", "stop", "--force"], env=env, check=False, capture_output=True)
    return subprocess.run(
        [
            "ddtrace-run",
            "ray",
            "start",
            "--head",
            "--num-cpus=3",
            "--num-gpus=0",
            "--object-store-memory=78643200",
            "--dashboard-host=127.0.0.1",
            "--dashboard-port=8265",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _wait_for_multi_app_deployments(base_url):
    deadline = time.time() + 120
    last_status = ""

    while time.time() < deadline:
        try:
            hello_resp = requests.get(f"{base_url}/hello", timeout=2)
            goodbye_resp = requests.get(f"{base_url}/goodbye", timeout=2)
            if hello_resp.status_code == 200 and goodbye_resp.status_code == 200:
                return
            last_status = "hello=%s goodbye=%s" % (hello_resp.status_code, goodbye_resp.status_code)
        except requests.RequestException as e:
            last_status = repr(e)
        time.sleep(0.5)

    raise AssertionError("Ray Serve multi-app deployments were not ready.\n%s" % last_status)


@pytest.fixture
def multi_app_serve_url(snapshot):
    env = os.environ.copy()
    env.update(
        {
            "DD_ENV": "test",
            "DD_PATCH_MODULES": "ray:true,aiohttp:false,grpc:false,requests:false",
        }
    )

    ray_start = _start_ray_cluster(env)
    if ray_start.returncode != 0:
        raise AssertionError(
            "Ray cluster failed to start.\n"
            "=== Captured STDOUT ===\n%s\n=== End of captured STDOUT ===\n"
            "=== Captured STDERR ===\n%s\n=== End of captured STDERR ===" % (ray_start.stdout, ray_start.stderr)
        )

    server_process = subprocess.Popen(
        ["ddtrace-run", "serve", "run", "serve_config.yaml"],
        cwd=MULTI_APP_SERVE_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        close_fds=True,
        start_new_session=True,
    )

    try:
        base_url = "http://127.0.0.1:8000"
        try:
            _wait_for_multi_app_deployments(base_url)
        except Exception as e:
            stdout, stderr = server_process.communicate(timeout=1) if server_process.poll() is not None else ("", "")
            raise AssertionError(
                "Ray Serve multi-app server failed.\n%s\n"
                "=== Captured STDOUT ===\n%s\n=== End of captured STDOUT ===\n"
                "=== Captured STDERR ===\n%s\n=== End of captured STDERR ===" % (e, stdout, stderr)
            )
        snapshot.clear()
        yield base_url
        time.sleep(5)
    finally:
        if server_process.poll() is None:
            os.killpg(server_process.pid, signal.SIGTERM)
            try:
                server_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(server_process.pid, signal.SIGKILL)
                server_process.wait()
        subprocess.run(["ray", "stop", "--force"], env=env, check=False, capture_output=True)


@pytest.mark.snapshot(ignores=RAY_MULTI_APP_SNAPSHOT_IGNORES)
def test_multi_app_deployment_routes(multi_app_serve_url):
    hello_resp = requests.get(f"{multi_app_serve_url}/hello", timeout=2)
    assert hello_resp.status_code == 200
    assert hello_resp.json() == {"message": "Hello from app1"}

    goodbye_resp = requests.get(f"{multi_app_serve_url}/goodbye", timeout=2)
    assert goodbye_resp.status_code == 200
    assert goodbye_resp.json() == {"message": "Goodbye from app2"}
