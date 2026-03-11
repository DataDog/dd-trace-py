import itertools
import os
from pathlib import Path

import azure.cosmos as azure_cosmos
import pytest


CONNECTION_STRING = "AccountEndpoint=http://localhost:8081/;AccountKey=C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw==;"
DB_NAME = "db.azure_cosmos_error"
CONTAINER_NAME = "container.azure_cosmos_error"
SNAPSHOT_IGNORES = ["meta.messaging.message_id"]

DEFAULT_HEADERS = {"User-Agent": "python-httpx/x.xx.x"}
ASYNC_OPTIONS = [False, True]
METHODS = ["create_item", "read_item", "upsert_item", "delete_item"]

params = [
    (
        f"{m}{'_async' if a else ''}",
        {
            "METHOD": m,
            "IS_ASYNC": str(a),
        },
    )
    for m, a in itertools.product(
        METHODS,
        ASYNC_OPTIONS,
    )
]

param_ids, param_values = zip(*params)

"""@pytest.fixture(autouse=True)
def patch_azure_cosmos():
    patch()
    yield
    unpatch()"""


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

'''
@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_cosmos_error():
    cosmos_client = azure_cosmos.CosmosClient.from_connection_string(CONNECTION_STRING, connection_verify=False)

    database = cosmos_client.create_database_if_not_exists(DB_NAME)

    container = database.create_container_if_not_exists(
        id=CONTAINER_NAME, partition_key=azure_cosmos.PartitionKey(path="/productName")
    )

    container.upsert_item(
        {
            "id": "item1",
            "productName": "Widget",
            "productModel": "Model 1",
        }
    )

    try:
        container.create_item(
            {
                "id": "item1",
                "productName": "Widget",
                "productModel": "Model 1",
            }
        )
    except azure_cosmos.exceptions.CosmosResourceExistsError:
        pass
        # assert str(e) == "(None) The document already exists in the collection.
        # \n Code: None \n Message: The document already exists in the collection."
'''