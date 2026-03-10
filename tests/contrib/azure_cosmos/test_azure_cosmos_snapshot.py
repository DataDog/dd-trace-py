import os
from pathlib import Path

from azure.cosmos import CosmosClient
import pytest

from ddtrace.contrib.internal.azure_cosmos.patch import patch, _unpatch

CONNECTION_STRING = "AccountEndpoint=https://localhost:8081/;AccountKey=C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw==;"
DB_NAME = "db.1"
CONTAINER_NAME = "container.1"
SNAPSHOT_IGNORES = ["meta.messaging.message_id"]

DEFAULT_HEADERS = {"User-Agent": "python-httpx/x.xx.x"}
ASYNC_OPTIONS = [False, True]

params = [
    (
        f"{'async_' if a else ''}cosmos_tracing",
        (
            {
                "IS_ASYNC": str(a),
            },
        ),
    )
    for a in ASYNC_OPTIONS
]

param_ids, param_values = zip(*params)

@pytest.fixture(autouse=True)
def patch_azure_cosmos():
    patch()
    yield
    unpatch()

@pytest.mark.parametrize(
    "env_vars",
    param_values,
    ids=param_ids,
)
@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
async def test_cosmos(ddtrace_run_python_code_in_subprocess, env_vars):
    env = os.environ.copy()
    env.update(env_vars)

    helper_path = Path(__file__).resolve().parent.joinpath("common.py")
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(helper_path.read_text(), env=env)

    assert status == 0, (err.decode(), out.decode())
    assert err == b"", err.decode()


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_cosmos_error():
    cosmos_client = azure_cosmos.CosmosClient.from_connection_string(
        CONNECTION_STRING,
        connection_verify=False
    )

    try:
        database = client.create_database(DB_NAME)
    except exceptions.CosmosResourceExistsError:
        database = client.get_database_client(DB_NAME)

    try:
        container = database.create_container(
            id=CONTAINER_NAME, partition_key=PartitionKey(path="/productName")
        )
    except exceptions.CosmosResourceExistsError:
        container = database.get_container_client(CONTAINER_NAME)

    try:
        cosmos_client.send_batch(EventData(body='{"body":"test message"}'))
    except TypeError as e:
        assert str(e) == "'EventData' object is not iterable"
    finally:
        cosmos_client.close()