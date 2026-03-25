from azure.cosmos import CosmosClient
from azure.cosmos import PartitionKey
import azure.functions as func

import ddtrace.auto  # noqa: F401


CONNECTION_STRING = "AccountEndpoint=http://localhost:8081/;AccountKey=C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw==;"
COSMOS_CONNECTION = "http://localhost:8081/"
COSMOS_DATABASE_NAME = "documents-db"
COSMOS_CONTAINER_NAME = "documents"

app = func.FunctionApp()


@app.route(route="upsert_item", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.POST])
async def UpsertItem(req: func.HttpRequest) -> func.HttpResponse:
    client = CosmosClient.from_connection_string(CONNECTION_STRING, connection_verify=False)

    database = client.create_database_if_not_exists(COSMOS_DATABASE_NAME)
    container = database.create_container_if_not_exists(
        id=COSMOS_CONTAINER_NAME, partition_key=PartitionKey(path="/title")
    )

    container.upsert_item(
        {
            "id": "doc1",
            "title": "Document 1",
            "context": "This is document 1.",
        }
    )

    return func.HttpResponse("Hello Datadog!")



@app.cosmos_db_trigger(
    arg_name="documents",
    container_name=COSMOS_CONTAINER_NAME,
    database_name=COSMOS_DATABASE_NAME,
    connection=COSMOS_CONNECTION,
    create_lease_container_if_not_exists=True,
)
def cosmos_trigger(documents: func.DocumentList):
    pass