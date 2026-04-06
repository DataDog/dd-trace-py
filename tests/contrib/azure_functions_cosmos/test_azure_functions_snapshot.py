import os
import signal
import subprocess
import time

from azure.cosmos import CosmosClient
from azure.cosmos import PartitionKey
import pytest

from tests.webclient import Client


SNAPSHOT_IGNORES = ["meta.http.useragent", "meta.http.status_code"]
DEFAULT_HEADERS = {"User-Agent": "python-httpx/x.xx.x"}

CONNECTION_STRING = "AccountEndpoint=http://localhost:8081/;AccountKey=C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw==;"
COSMOS_DATABASE_NAME = "documents-db"
COSMOS_CONTAINER_NAME = "documents"


@pytest.fixture(autouse=True)
def setup_cosmos_resources():
    client = CosmosClient.from_connection_string(CONNECTION_STRING, connection_verify=False)
    try:
        client.delete_database(COSMOS_DATABASE_NAME)
    except Exception:
        pass
    database = client.create_database_if_not_exists(COSMOS_DATABASE_NAME)
    database.create_container_if_not_exists(id=COSMOS_CONTAINER_NAME, partition_key=PartitionKey(path="/title"))


@pytest.fixture()
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
        time.sleep(10)
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_cosmos_trigger(azure_functions_client: Client) -> None:
    assert azure_functions_client.post("/api/upsert_item", headers=DEFAULT_HEADERS).status_code == 200
