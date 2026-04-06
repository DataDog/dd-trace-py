from azure.cosmos import CosmosClient
import azure.functions as func

import ddtrace.auto  # noqa: F401


CONNECTION_STRING = (
    "AccountEndpoint=http://localhost:8081/;"
    "AccountKey=C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw==;"
)
COSMOS_DATABASE_NAME = "documents-db"
COSMOS_CONTAINER_NAME = "documents"

app = func.FunctionApp()


@app.route(route="upsert_item", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.POST])
async def UpsertItem(req: func.HttpRequest) -> func.HttpResponse:
    with CosmosClient.from_connection_string(CONNECTION_STRING, connection_verify=False) as client:
        database = client.get_database_client(COSMOS_DATABASE_NAME)
        container = database.get_container_client(COSMOS_CONTAINER_NAME)

        container.upsert_item(
            {
                "id": "doc1",
                "title": "Document 1",
                "context": "This is document 1.",
            }
        )

        return func.HttpResponse("Hello Datadog!")


@app.function_name(name="cosmosdbtrigger")
@app.cosmos_db_trigger(
    arg_name="documents",
    container_name=COSMOS_CONTAINER_NAME,
    database_name=COSMOS_DATABASE_NAME,
    connection="COSMOS_CONNECTION",
    create_lease_container_if_not_exists=True,
)
def cosmos_trigger(documents: func.DocumentList):
    pass
