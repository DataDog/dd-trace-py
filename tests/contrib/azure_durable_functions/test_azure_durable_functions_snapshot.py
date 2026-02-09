import os
import signal
import subprocess
import time

import pytest

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.constants import SPAN_KIND
import ddtrace.contrib  # noqa: F401
from ddtrace.contrib.internal.azure_functions.utils import wrap_durable_trigger
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.schema import schematize_cloud_faas_operation
from tests.utils import TracerSpanContainer
from tests.utils import scoped_tracer
from tests.webclient import Client


DEFAULT_HEADERS = {"User-Agent": "python-httpx/x.xx.x"}
SNAPSHOT_IGNORES = ["meta.http.url", "meta.test.deployment_verification"]


@pytest.fixture
def azure_functions_client(request):
    env_vars = getattr(request, "param", {})

    # Copy the env to get the correct PYTHONPATH and such
    # from the virtualenv.
    env = os.environ.copy()
    env.update(env_vars)

    port = 7072
    env["AZURE_FUNCTIONS_TEST_PORT"] = str(port)
    env["DD_TRACE_STATS_COMPUTATION_ENABLED"] = "False"  # disable stats computation to avoid potential flakes in tests

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


def _wait_for_durable_completion(client: Client, response) -> None:
    if response.status_code == 200:
        return

    assert response.status_code == 202
    payload = response.json()
    status_url = payload.get("statusQueryGetUri")
    assert status_url

    for _ in range(20):
        status_response = client.get(status_url, timeout=5)
        if status_response.status_code == 200:
            status_payload = status_response.json()
            if status_payload.get("runtimeStatus") == "Completed":
                return
        time.sleep(0.5)

    pytest.fail("Durable orchestration did not complete before timeout")


@pytest.mark.parametrize(
    "func_name, trigger_name, context_name",
    [
        ("sample_activity", "Activity", "azure.durable_functions.patched_activity"),
        ("sample_entity", "Entity", "azure.durable_functions.patched_entity"),
    ],
)
def test_trigger_wrapper(func_name, trigger_name, context_name):
    with scoped_tracer() as tracer:
        pin = Pin()

        def trigger_func():
            return "ok"

        wrapped = wrap_durable_trigger(
            pin,
            trigger_func,
            func_name,
            trigger_name,
            context_name,
        )
        assert wrapped() == "ok"

        spans = TracerSpanContainer(tracer).pop()
        assert len(spans) == 1
        span = spans[0]

        expected_name = schematize_cloud_faas_operation(
            "azure.functions.invoke", cloud_provider="azure", cloud_service="functions"
        )
        assert span.name == expected_name
        assert span.service == int_service(pin, config.azure_functions)
        assert span.resource == f"{trigger_name} {func_name}"
        assert span.span_type == SpanTypes.SERVERLESS
        assert span.get_tag("aas.function.name") == func_name
        assert span.get_tag("aas.function.trigger") == trigger_name
        assert span.get_tag(SPAN_KIND) == SpanKind.INTERNAL


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_activity_trigger_end_to_end(azure_functions_client: Client) -> None:
    response = azure_functions_client.get("/api/startactivity", headers=DEFAULT_HEADERS)
    _wait_for_durable_completion(azure_functions_client, response)
    assert response.status_code in (200, 202)


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_entity_trigger_end_to_end(azure_functions_client: Client) -> None:
    response = azure_functions_client.get("/api/startentity", headers=DEFAULT_HEADERS)
    _wait_for_durable_completion(azure_functions_client, response)
    assert response.status_code in (200, 202)
