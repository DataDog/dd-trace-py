import os

import azure.cosmos as azure_cosmos
import azure.cosmos.aio as azure_cosmos_aio
import pytest


CONNECTION_STRING = "AccountEndpoint=http://localhost:8081/;AccountKey=C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw==;"
SYNC_DB_NAME = "db.azure_cosmos_sync"
SYNC_CONTAINER_NAME = "container.azure_cosmos_sync"
ASYNC_DB_NAME = "db.azure_cosmos_async"
ASYNC_CONTAINER_NAME = "container.azure_cosmos_async"


def run_test(container, method):
    if method == "create_item":
        container.create_item(
            {
                "id": "item1",
                "productName": "Widget",
                "productModel": "Model 1",
            }
        )
    elif method == "read_item":
        container.read_item("item1", partition_key="Widget")
    elif method == "upsert_item":
        container.upsert_item(
            {
                "id": "item1",
                "productName": "Widget",
                "productModel": "Model X",
            }
        )
    elif method == "delete_item":
        for item in container.query_items(
            query='SELECT * FROM mycontainer p WHERE p.productModel = "Model X"',
            enable_cross_partition_query=True,
        ):
            container.delete_item(item["id"], partition_key="Widget")


async def run_test_async(container, method):
    if method == "create_item":
        await container.create_item(
            {
                "id": "item1",
                "productName": "Widget",
                "productModel": "Model 1",
            }
        )
    elif method == "read_item":
        await container.read_item("item1", partition_key="Widget")
    elif method == "upsert_item":
        await container.upsert_item(
            {
                "id": "item1",
                "productName": "Widget",
                "productModel": "Model X",
            }
        )
    elif method == "delete_item":
        async for item in container.query_items(
            query='SELECT * FROM mycontainer p WHERE p.productModel = "Model X"',
            enable_cross_partition_query=True,
        ):
            await container.delete_item(item["id"], partition_key="Widget")


@pytest.mark.asyncio
async def test_common():
    is_async = os.environ.get("IS_ASYNC") == "True"
    method = os.environ.get("METHOD")

    if is_async:
        async with azure_cosmos_aio.CosmosClient.from_connection_string(
            CONNECTION_STRING, connection_verify=False
        ) as cosmos_client:
            database = await cosmos_client.create_database_if_not_exists(ASYNC_DB_NAME)
            container = await database.create_container_if_not_exists(
                ASYNC_CONTAINER_NAME, partition_key=azure_cosmos.PartitionKey(path="/productName")
            )

            await run_test_async(
                container,
                method,
            )
    else:
        cosmos_client = azure_cosmos.CosmosClient.from_connection_string(CONNECTION_STRING, connection_verify=False)

        database = cosmos_client.create_database_if_not_exists(SYNC_DB_NAME)
        container = database.create_container_if_not_exists(
            SYNC_CONTAINER_NAME, partition_key=azure_cosmos.PartitionKey(path="/productName")
        )

        run_test(
            container,
            method,
        )


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main(["-x", __file__]))
