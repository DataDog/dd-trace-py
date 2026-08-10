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
    "meta.http.useragent",
]
MULTI_APP_SERVE_DIR = Path(__file__).parent / "multi_app"
EXPECTED_MULTI_APP_DEPLOYMENTS = {
    "hello_app": "HelloApp",
    "goodbye_app": "GoodbyeApp",
}


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


def _status_value(status_details):
    if isinstance(status_details, dict):
        return status_details.get("status")
    return status_details


def _get_deployment_details(app_details, deployment_name):
    deployments = app_details.get("deployments", {})
    if isinstance(deployments, dict):
        return deployments.get(deployment_name, {})

    for deployment_details in deployments:
        if deployment_details.get("name") == deployment_name:
            return deployment_details
        if deployment_name in deployment_details:
            return deployment_details[deployment_name]

    return {}


def _is_deployment_ready(deployment_details):
    if _status_value(deployment_details) != "HEALTHY":
        return False

    replica_states = deployment_details.get("replica_states")
    if replica_states is not None:
        return replica_states.get("RUNNING", 0) > 0

    replicas = deployment_details.get("replicas")
    if replicas is not None:
        return any(replica.get("state") == "RUNNING" for replica in replicas)

    return True


def _are_proxies_ready(status):
    proxies = status.get("proxies")
    if not proxies:
        return True
    return all(_status_value(proxy_details) == "HEALTHY" for proxy_details in proxies.values())


def _wait_for_multi_app_deployments(dashboard_url):
    deadline = time.time() + 120
    last_status = ""

    while time.time() < deadline:
        try:
            response = requests.get(f"{dashboard_url}/api/serve/applications/", timeout=2)
            response.raise_for_status()
            status = response.json()
            applications = status.get("applications", {})
            deployments_ready = all(
                _status_value(applications.get(app_name, {})) == "RUNNING"
                and _is_deployment_ready(_get_deployment_details(applications[app_name], deployment_name))
                for app_name, deployment_name in EXPECTED_MULTI_APP_DEPLOYMENTS.items()
                if app_name in applications
            )
            if (
                deployments_ready
                and _are_proxies_ready(status)
                and EXPECTED_MULTI_APP_DEPLOYMENTS.keys() <= applications.keys()
            ):
                return
            last_status = status
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
        dashboard_url = "http://127.0.0.1:8265"
        try:
            _wait_for_multi_app_deployments(dashboard_url)
        except Exception as e:
            stdout, stderr = server_process.communicate(timeout=1) if server_process.poll() is not None else ("", "")
            raise AssertionError(
                "Ray Serve multi-app server failed.\n%s\n"
                "=== Captured STDOUT ===\n%s\n=== End of captured STDOUT ===\n"
                "=== Captured STDERR ===\n%s\n=== End of captured STDERR ===" % (e, stdout, stderr)
            )
        yield base_url
    finally:
        if server_process.poll() is None:
            os.killpg(server_process.pid, signal.SIGTERM)
            try:
                server_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(server_process.pid, signal.SIGKILL)
                server_process.wait()
        subprocess.run(["ray", "stop", "--force"], env=env, check=False, capture_output=True)


# Ray serve multi-app is supported locally but the tests are not working properly in CI.
# The test should stay in the code as it should be tested as soon as possible but it is skipped
# for now to not delay more the release of the ray serve integration.
# TODO(dubloom): unskip this tests
@pytest.mark.skip(reason="Temporarily disabled while Ray Serve multi-app coverage is unstable")
@pytest.mark.snapshot(ignores=RAY_MULTI_APP_SNAPSHOT_IGNORES)
def test_multi_app_deployment_routes(multi_app_serve_url):
    hello_resp = requests.get(f"{multi_app_serve_url}/hello", timeout=2)
    assert hello_resp.status_code == 200
    assert hello_resp.json() == {"message": "Hello from app1"}

    goodbye_resp = requests.get(f"{multi_app_serve_url}/goodbye", timeout=2)
    assert goodbye_resp.status_code == 200
    assert goodbye_resp.json() == {"message": "Goodbye from app2"}
