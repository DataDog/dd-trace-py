from azure.cosmos import CosmosClient
from azure.cosmos import PartitionKey
import pytest

from tests.webclient import Client


SNAPSHOT_IGNORES = ["meta.http.useragent", "meta.http.status_code", "meta._dd.base_service"]
DEFAULT_HEADERS = {"User-Agent": "python-httpx/x.xx.x"}

CONNECTION_STRING = "AccountEndpoint=http://localhost:8081/;AccountKey=C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw==;"
COSMOS_DATABASE_NAME = "documents-db"
COSMOS_CONTAINER_NAME = "documents"


@pytest.fixture(autouse=True)
def setup_cosmos_resources() -> None:
    client = CosmosClient.from_connection_string(CONNECTION_STRING, connection_verify=False)
    try:
        client.delete_database(COSMOS_DATABASE_NAME)
    except Exception:
        pass
    database = client.create_database_if_not_exists(COSMOS_DATABASE_NAME)
    database.create_container_if_not_exists(id=COSMOS_CONTAINER_NAME, partition_key=PartitionKey(path="/title"))


@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
def test_cosmos_trigger(azure_functions_client: Client) -> None:
    assert azure_functions_client.post("/api/upsert_item", headers=DEFAULT_HEADERS).status_code == 200
