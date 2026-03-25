import itertools
import os
import signal
import subprocess
import time

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobServiceClient
import pytest

from tests.webclient import Client


BLOB_CONNECTION_STRING = "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"

# Ignoring span link attributes until values are normalized: https://github.com/DataDog/dd-apm-test-agent/issues/154
SNAPSHOT_IGNORES = ["meta.messaging.message_id", "span_links.tracestate", "span_links.trace_id_high"]
DEFAULT_HEADERS = {"User-Agent": "python-httpx/x.xx.x"}
ASYNC_OPTIONS = [False, True]
CARDINALITY = ["one", "many"]
DISTRIBUTED_TRACING_ENABLED_OPTIONS = [None, False]

params = [
    (
        f"{'async_' if a else ''}consume_{c}_distributed_tracing_{'enabled' if d is None else 'disabled'}",
        (
            {
                "IS_ASYNC": str(a),
                "CARDINALITY": c,
                **({"DD_AZURE_EVENTHUBS_DISTRIBUTED_TRACING": str(d)} if d is not None else {}),
                **({"DD_AZURE_FUNCTIONS_DISTRIBUTED_TRACING": str(d)} if d is not None else {}),
            },
            "single" if c == "one" else "batch",
        ),
    )
    for a, c, d in itertools.product(ASYNC_OPTIONS, CARDINALITY, DISTRIBUTED_TRACING_ENABLED_OPTIONS)
]

param_ids, param_values = zip(*params)


@pytest.fixture
def azure_functions_client(request):
    env_vars = getattr(request, "param", {})

    # Copy the env to get the correct PYTHONPATH and such
    # from the virtualenv.
    env = os.environ.copy()
    env.update(env_vars)

    port = 7071
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


@pytest.fixture(autouse=True)
def cleanup_checkpoints():
    connection_string = BLOB_CONNECTION_STRING
    container_name = "azure-webjobs-eventhub"

    blob_service_client = BlobServiceClient.from_connection_string(connection_string, api_version="2025-05-05")
    container_client = blob_service_client.get_container_client(container_name)

    try:
        blobs = container_client.list_blobs()

        for blob in blobs:
            container_client.delete_blob(blob.name)
    except ResourceNotFoundError:
        pass


@pytest.mark.parametrize(
    "azure_functions_client, payload_type",
    param_values,
    ids=param_ids,
    indirect=["azure_functions_client"],
)
@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_event_hubs_trigger(azure_functions_client: Client, payload_type) -> None:
    assert azure_functions_client.post(f"/api/sendevent{payload_type}", headers=DEFAULT_HEADERS).status_code == 200
