import itertools
import os
import signal
import subprocess
import time

import pytest

from tests.webclient import Client


# Ignoring span link attributes until values are normalized: https://github.com/DataDog/dd-apm-test-agent/issues/154
SNAPSHOT_IGNORES = ["meta.messaging.message_id", "span_links.tracestate", "span_links.trace_id_high"]
DEFAULT_HEADERS = {"User-Agent": "python-httpx/x.xx.x"}
ENTITY_TYPES = ["queue", "topic"]
ASYNC_OPTIONS = [False, True]
CARDINALITY = ["one", "many"]
DISTRIBUTED_TRACING_ENABLED_OPTIONS = [None, False]

param_values = [
    (
        {
            **{"IS_ASYNC": str(a)},
            **{"CARDINALITY": c},
            **({} if d is None else {"DD_AZURE_FUNCTIONS_DISTRIBUTED_TRACING": str(d)}),
        },
        e,
        "single" if c == "one" else "batch",
    )
    for e, a, c, d in itertools.product(ENTITY_TYPES, ASYNC_OPTIONS, CARDINALITY, DISTRIBUTED_TRACING_ENABLED_OPTIONS)
]

param_ids = [
    f"{e}{'_async' if a else ''}_consume_{c}" f"_distributed_tracing_{'enabled' if d is None else 'disabled'}"
    for e, a, c, d in itertools.product(ENTITY_TYPES, ASYNC_OPTIONS, CARDINALITY, DISTRIBUTED_TRACING_ENABLED_OPTIONS)
]


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


@pytest.mark.parametrize(
    "azure_functions_client, entity, payload_type",
    param_values,
    ids=param_ids,
    indirect=["azure_functions_client"],
)
@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_service_bus_trigger(azure_functions_client: Client, entity, payload_type) -> None:
    assert (
        azure_functions_client.post(f"/api/{entity}sendmessage{payload_type}", headers=DEFAULT_HEADERS).status_code
        == 200
    )
