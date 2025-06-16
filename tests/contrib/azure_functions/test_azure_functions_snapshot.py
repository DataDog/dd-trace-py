import json
import os
import signal
import subprocess
import time

import pytest

from tests.webclient import Client


DEFAULT_HEADERS = {
    "User-Agent": "python-httpx/x.xx.x",
}

DISTRIBUTED_TRACING_DISABLED_PARAMS = {
    "DD_AZURE_FUNCTIONS_DISTRIBUTED_TRACING": "False",
}


@pytest.fixture
def azure_functions_client(request):
    env_vars = getattr(request, "param", {})

    # Copy the env to get the correct PYTHONPATH and such
    # from the virtualenv.
    env = os.environ.copy()
    env.update(env_vars)

    port = 7071
    env["AZURE_FUNCTIONS_TEST_PORT"] = str(port)

    # webservers might exec or fork into another process, so we need to os.setsid() to create a process group
    # (all of which will listen to signals sent to the parent) so that we can kill the whole application.
    proc = subprocess.Popen(
        ["func", "start", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        env=env,
        preexec_fn=os.setsid,
        cwd=os.path.join(os.path.dirname(__file__), "azure_function_app"),
    )
    try:
        client = Client(f"http://0.0.0.0:{port}")
        # Wait for the server to start up
        try:
            client.wait(delay=0.5)
            yield client
            client.get_ignored("/shutdown")
        except Exception:
            pass
        # At this point the traces have been sent to the test agent
        # but the test agent hasn't necessarily finished processing
        # the traces (race condition) so wait just a bit for that
        # processing to complete.
        time.sleep(1)
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


@pytest.mark.snapshot
def test_http_get_ok(azure_functions_client: Client) -> None:
    assert azure_functions_client.get("/api/httpgetok?key=val", headers=DEFAULT_HEADERS).status_code == 200


@pytest.mark.snapshot
def test_http_get_ok_async(azure_functions_client: Client) -> None:
    assert azure_functions_client.get("/api/httpgetokasync?key=val", headers=DEFAULT_HEADERS).status_code == 200


@pytest.mark.snapshot
def test_http_get_ok_obfuscated(azure_functions_client: Client) -> None:
    assert azure_functions_client.get("/api/httpgetok?secret=val", headers=DEFAULT_HEADERS).status_code == 200


@pytest.mark.snapshot
def test_http_get_ok_async_obfuscated(azure_functions_client: Client) -> None:
    assert azure_functions_client.get("/api/httpgetokasync?secret=val", headers=DEFAULT_HEADERS).status_code == 200


@pytest.mark.snapshot(ignores=["meta.error.stack"])
def test_http_get_error(azure_functions_client: Client) -> None:
    assert azure_functions_client.get("/api/httpgeterror", headers=DEFAULT_HEADERS).status_code == 500


@pytest.mark.snapshot
def test_http_post_ok(azure_functions_client: Client) -> None:
    assert (
        azure_functions_client.post("/api/httppostok", headers=DEFAULT_HEADERS, data={"key": "val"}).status_code == 200
    )


@pytest.mark.snapshot
def test_http_get_trigger_arg(azure_functions_client: Client) -> None:
    assert azure_functions_client.get("/api/httpgettriggerarg", headers=DEFAULT_HEADERS).status_code == 200


@pytest.mark.snapshot
def test_http_get_function_name_decorator(azure_functions_client: Client) -> None:
    assert azure_functions_client.get("/api/httpgetfunctionnamedecorator", headers=DEFAULT_HEADERS).status_code == 200


@pytest.mark.snapshot
def test_http_get_function_name_no_decorator(azure_functions_client: Client) -> None:
    assert azure_functions_client.get("/api/httpgetfunctionnamenodecorator", headers=DEFAULT_HEADERS).status_code == 200


@pytest.mark.snapshot
def test_http_get_function_name_decorator_order(azure_functions_client: Client) -> None:
    assert (
        azure_functions_client.get("/api/httpgetfunctionnamedecoratororder", headers=DEFAULT_HEADERS).status_code == 200
    )


@pytest.mark.parametrize(
    "azure_functions_client",
    [{}, DISTRIBUTED_TRACING_DISABLED_PARAMS],
    ids=["enabled", "disabled"],
    indirect=True,
)
@pytest.mark.snapshot
def test_http_get_distributed_tracing(azure_functions_client: Client) -> None:
    assert azure_functions_client.get("/api/httpgetroot", headers=DEFAULT_HEADERS).status_code == 200


@pytest.mark.snapshot
def test_service_bus_queue(azure_functions_client: Client) -> None:
    assert (
        azure_functions_client.post(
            "/admin/functions/servicebusqueue",
            headers={"User-Agent": "python-httpx/x.xx.x", "Content-Type": "application/json"},
            data=json.dumps({"input": '{"msg": "test message"}'}),
        ).status_code
        == 202
    )


@pytest.mark.snapshot
def test_service_bus_topic(azure_functions_client: Client) -> None:
    assert (
        azure_functions_client.post(
            "/admin/functions/servicebustopic",
            headers={"User-Agent": "python-httpx/x.xx.x", "Content-Type": "application/json"},
            data=json.dumps({"input": '{"msg": "test message"}'}),
        ).status_code
        == 202
    )


@pytest.mark.snapshot
def test_timer(azure_functions_client: Client) -> None:
    assert (
        azure_functions_client.post(
            "/admin/functions/timer",
            headers={"User-Agent": "python-httpx/x.xx.x", "Content-Type": "application/json"},
            data=json.dumps({"input": None}),
        ).status_code
        == 202
    )


@pytest.mark.snapshot
def test_timer_async(azure_functions_client: Client) -> None:
    assert (
        azure_functions_client.post(
            "/admin/functions/timer_async",
            headers={"User-Agent": "python-httpx/x.xx.x", "Content-Type": "application/json"},
            data=json.dumps({"input": None}),
        ).status_code
        == 202
    )
