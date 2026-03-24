import azure.cosmos as azure_cosmos
import pytest

from ddtrace.contrib.internal.azure_cosmos.patch import patch
from ddtrace.contrib.internal.azure_cosmos.patch import unpatch

from .common import run_test
from .common import run_test_async


CONNECTION_STRING = "AccountEndpoint=http://localhost:8081/;AccountKey=C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw==;"
ERR_DB_NAME = "db.azure_cosmos_error"
ERR_CONTAINER_NAME = "container.azure_cosmos_error"
SYNC_DB_NAME = "db.azure_cosmos_sync"
SYNC_CONTAINER_NAME = "container.azure_cosmos_sync"
ASYNC_DB_NAME = "db.azure_cosmos_async"
ASYNC_CONTAINER_NAME = "container.azure_cosmos_async"
SNAPSHOT_IGNORES = ["meta.http.useragent", "meta.error.stack"]

DEFAULT_HEADERS = {"User-Agent": "python-httpx/x.xx.x"}
ASYNC_OPTIONS = [False, True]

params = [
    (
        f"{'_async' if a else ''}",
        {
            "IS_ASYNC": str(a),
        },
    )
    for a in ASYNC_OPTIONS
]

param_ids, param_values = zip(*params)


@pytest.fixture(autouse=True)
def patch_azure_cosmos():
    patch()
    yield
    unpatch()


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_cosmos_sync_(tracer, test_spans):
    run_test()

    test_spans.assert_has_spans()
    query_spans = list(test_spans.filter_spans(name="cosmosdb.query"))
    for span in query_spans:
        assert "sdk-python-cosmos" in span._meta["http.useragent"]


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
async def test_cosmos_async(tracer, test_spans):
    await run_test_async()

    test_spans.assert_has_spans()
    query_spans = list(test_spans.filter_spans(name="cosmosdb.query"))
    for span in query_spans:
        print(span.resource)
        assert "sdk-python-cosmos-async" in span._meta["http.useragent"] 


"""
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
    assert err == b"", err.decode()"""


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_cosmos_error():
    cosmos_client = azure_cosmos.CosmosClient.from_connection_string(CONNECTION_STRING, connection_verify=False)

    database = cosmos_client.create_database(ERR_DB_NAME)

    container = database.create_container(
        id=ERR_CONTAINER_NAME, partition_key=azure_cosmos.PartitionKey(path="/productName")
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
    except azure_cosmos.exceptions.CosmosResourceExistsError as e:
        for item in container.query_items(
            query='SELECT * FROM mycontainer p WHERE p.productModel = "Model 1"',
        ):
            container.delete_item(item["id"], partition_key="Widget")

        cosmos_client.delete_database(database)

        assert "The document already exists in the collection" in str(e)

