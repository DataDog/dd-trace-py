import azure.cosmos as azure_cosmos
import azure.cosmos.aio as azure_cosmos_aio
import pytest


CONNECTION_STRING = "AccountEndpoint=http://localhost:8081/;AccountKey=C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw==;"
SYNC_DB_NAME = "db.azure_cosmos_sync"
SYNC_CONTAINER_NAME = "container.azure_cosmos_sync"
ASYNC_DB_NAME = "db.azure_cosmos_async"
ASYNC_CONTAINER_NAME = "container.azure_cosmos_async"


def run_test():
    cosmos_client = azure_cosmos.CosmosClient.from_connection_string(CONNECTION_STRING, connection_verify=False)

    database = cosmos_client.create_database(SYNC_DB_NAME)
    container = database.create_container(
        SYNC_CONTAINER_NAME, partition_key=azure_cosmos.PartitionKey(path="/productName")
    )

    container.create_item(
        {
            "id": "item1",
            "productName": "Widget",
            "productModel": "Model 1",
        }
    )

    container.read_item("item1", partition_key="Widget")

    container.upsert_item(
        {
            "id": "item1",
            "productName": "Widget",
            "productModel": "Model X",
        }
    )

    for item in container.query_items(
        query='SELECT * FROM mycontainer p WHERE p.productModel = "Model X"',
    ):
        container.delete_item(item["id"], partition_key="Widget")

    cosmos_client.delete_database(database)


@pytest.mark.asyncio
async def run_test_async():
    async with azure_cosmos_aio.CosmosClient.from_connection_string(
        CONNECTION_STRING, connection_verify=False
    ) as cosmos_client:
        database = await cosmos_client.create_database(ASYNC_DB_NAME)
        container = await database.create_container(
            ASYNC_CONTAINER_NAME, partition_key=azure_cosmos.PartitionKey(path="/productName")
        )

        await container.create_item(
            {
                "id": "item1",
                "productName": "Widget",
                "productModel": "Model 1",
            }
        )

        await container.read_item("item1", partition_key="Widget")

        await container.upsert_item(
            {
                "id": "item1",
                "productName": "Widget",
                "productModel": "Model X",
            }
        )

        async for item in container.query_items(
            query='SELECT * FROM mycontainer p WHERE p.productModel = "Model X"',
        ):
            await container.delete_item(item["id"], partition_key="Widget")

        await cosmos_client.delete_database(database)
