import os
import signal
import subprocess
import sys
import time

from azure.storage.blob import BlobServiceClient
import pytest

from tests.webclient import Client


BLOB_CONNECTION_STRING = "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"

CARDINALITY_MANY_PARAMS = {
    "CARDINALITY": "many",
}

DEFAULT_HEADERS = {
    "User-Agent": "python-httpx/x.xx.x",
}

DISTRIBUTED_TRACING_DISABLED_PARAMS = {"DD_AZURE_FUNCTIONS_DISTRIBUTED_TRACING": "False"}

SNAPSHOT_IGNORES = ["meta.tracestate"]


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
        ["func", "start", "--verbose", "--port", str(port)],
        stdout=sys.stdout,
        stderr=sys.stderr,
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

    blob_service_client = BlobServiceClient.from_connection_string(connection_string, api_version="2021-04-10")
    container_client = blob_service_client.get_container_client(container_name)

    blobs = container_client.list_blobs()

    for blob in blobs:
        container_client.delete_blob(blob.name)


@pytest.mark.parametrize(
    "azure_functions_client",
    [{}, DISTRIBUTED_TRACING_DISABLED_PARAMS],
    ids=["enabled", "disabled"],
    indirect=True,
)
@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_event_hub_distributed_tracing(azure_functions_client: Client) -> None:
    assert azure_functions_client.post("/api/httppostrooteventhub", headers=DEFAULT_HEADERS).status_code == 200
