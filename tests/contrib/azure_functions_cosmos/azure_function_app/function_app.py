import os
import traceback

import aiohttp  # noqa: F401
from azure.cosmos import CosmosClient
from azure.cosmos import PartitionKey
from azure.cosmos.aio import CosmosClient as CosmosClientAio
import azure.functions as func

import ddtrace.auto  # noqa: F401


CONNECTION_STRING = "AccountEndpoint=http://localhost:8081/;AccountKey=C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw==;"
SYNC_DB_NAME = "db.azure_functions_cosmos_sync"
SYNC_CONTAINER_NAME = "container.azure_functions_cosmos_sync"
ASYNC_DB_NAME = "db.azure_functions_cosmos_async"
ASYNC_CONTAINER_NAME = "container.azure_functions_cosmos_async"

app = func.FunctionApp()


@app.route(route="create_item", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.POST])
async def CreateItem(req: func.HttpRequest) -> func.HttpResponse:
    if os.getenv("IS_ASYNC") == "True":
        async with CosmosClientAio.from_connection_string(CONNECTION_STRING, connection_verify=False) as client:
            database = await client.create_database_if_not_exists(ASYNC_DB_NAME)
            container = await database.create_container_if_not_exists(
                id=ASYNC_CONTAINER_NAME, partition_key=PartitionKey(path="/productName")
            )

            await container.create_item(
                {
                    "id": "item1",
                    "productName": "Widget",
                    "productModel": "Model 1",
                }
            )

    else:
        client = CosmosClient.from_connection_string(CONNECTION_STRING, connection_verify=False)

        database = client.create_database_if_not_exists(SYNC_DB_NAME)
        container = database.create_container_if_not_exists(
            id=SYNC_CONTAINER_NAME, partition_key=PartitionKey(path="/productName")
        )

        container.create_item(
            {
                "id": "item1",
                "productName": "Widget",
                "productModel": "Model 1",
            }
        )

    return func.HttpResponse("Hello Datadog!")


@app.route(route="read_item", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.POST])
async def ReadItem(req: func.HttpRequest) -> func.HttpResponse:
    if os.getenv("IS_ASYNC") == "True":
        async with CosmosClientAio.from_connection_string(CONNECTION_STRING, connection_verify=False) as client:
            database = await client.create_database_if_not_exists(ASYNC_DB_NAME)
            container = await database.create_container_if_not_exists(
                id=ASYNC_CONTAINER_NAME, partition_key=PartitionKey(path="/productName")
            )

            await container.read_item(
                item="item1",
                partition_key="Widget",
            )

    else:
        client = CosmosClient.from_connection_string(CONNECTION_STRING, connection_verify=False)

        database = client.create_database_if_not_exists(SYNC_DB_NAME)
        container = database.create_container_if_not_exists(
            id=SYNC_CONTAINER_NAME, partition_key=PartitionKey(path="/productName")
        )

        container.read_item(
            item="item1",
            partition_key="Widget",
        )

    return func.HttpResponse("Hello Datadog!")


@app.route(route="upsert_item", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.POST])
async def UpsertItem(req: func.HttpRequest) -> func.HttpResponse:
    if os.getenv("IS_ASYNC") == "True":
        async with CosmosClientAio.from_connection_string(CONNECTION_STRING, connection_verify=False) as client:
            database = await client.create_database_if_not_exists(ASYNC_DB_NAME)
            container = await database.create_container_if_not_exists(
                id=ASYNC_CONTAINER_NAME, partition_key=PartitionKey(path="/productName")
            )

            await container.upsert_item(
                {
                    "id": "item1",
                    "productName": "Widget",
                    "productModel": "Model X",
                }
            )

    else:
        client = CosmosClient.from_connection_string(CONNECTION_STRING, connection_verify=False)

        database = client.create_database_if_not_exists(SYNC_DB_NAME)
        container = database.create_container_if_not_exists(
            id=SYNC_CONTAINER_NAME, partition_key=PartitionKey(path="/productName")
        )

        container.upsert_item(
            {
                "id": "item1",
                "productName": "Widget",
                "productModel": "Model X",
            }
        )

    return func.HttpResponse("Hello Datadog!")


@app.route(route="delete_item", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.POST])
async def DeleteItem(req: func.HttpRequest) -> func.HttpResponse:
    if os.getenv("IS_ASYNC") == "True":
        async with CosmosClientAio.from_connection_string(CONNECTION_STRING, connection_verify=False) as client:
            database = await client.create_database_if_not_exists(ASYNC_DB_NAME)
            container = await database.create_container_if_not_exists(
                id=ASYNC_CONTAINER_NAME, partition_key=PartitionKey(path="/productName")
            )

            async for item in container.query_items(
                query='SELECT * FROM mycontainer p WHERE p.productModel = "Model X"',
            ):
                await container.delete_item(item["id"], partition_key="Widget")

    else:
        client = CosmosClient.from_connection_string(CONNECTION_STRING, connection_verify=False)

        database = client.create_database_if_not_exists(SYNC_DB_NAME)
        container = database.create_container_if_not_exists(
            id=SYNC_CONTAINER_NAME, partition_key=PartitionKey(path="/productName")
        )

        for item in container.query_items(
            query='SELECT * FROM mycontainer p WHERE p.productModel = "Model X"',
        ):
            container.delete_item(item["id"], partition_key="Widget")

    return func.HttpResponse("Hello Datadog!")
