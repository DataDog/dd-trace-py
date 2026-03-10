import os
import pytest

import azure.cosmos as azure_cosmos
import azure.cosmos.aio as azure_cosmos_aio

CONNECTION_STRING = "AccountEndpoint=https://localhost:8081/;AccountKey=C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw==;"
DB_NAME = "db.1"
CONTAINER_NAME = "container.1"

def run_test(container, method):
    match method:
        case "create_item":
            container.create_item(
                {
                    "id": "item1",
                    "productName": "Widget",
                    "productModel": "Model 1",
                }
            )
        case "read_item":
            container.read_item("item1", partition_key="Widget")
        case "upsert_item":
            container.upsert_item(
                    {
                        "id": "item1",
                        "productName": "Widget",
                        "productModel": "Model X",
                    }
                )
        case "delete_item":
            for item in container.query_items(
                query='SELECT * FROM mycontainer p WHERE p.productModel = "Model X"',
                enable_cross_partition_query=True,
            ):
                container.delete_item(item["id"], partition_key="Widget")


def run_test_async(container, method):
        match method:
            case "create_item":
                await container.create_item(
                    {
                        "id": "item1",
                        "productName": "Widget",
                        "productModel": "Model 1",
                    }
                )
            case "read_item":
                await container.read_item("item1", partition_key="Widget")
            case "upsert_item":
                await container.upsert_item(
                        {
                            "id": "item1",
                            "productName": "Widget",
                            "productModel": "Model X",
                        }
                    )
            case "delete_item":
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
        cosmos_client = azure_cosmos_aio.CosmosClient.from_connection_string(
            CONNECTION_STRING,
            connection_verify=False
        )

        database = await cosmos_client.create_database_if_not_exists(DB_NAME)
        container = await database.create_container_if_not_exists(CONTAINER_NAME, partition_key=PartitionKey(path="/productName"))

        try:
            await run_test_async(
                container,
                method,
            )
        finally:
            await cosmos_client.close()
    else:
        cosmos_client = azure_cosmos.CosmosClient.from_connection_string(
            CONNECTION_STRING,
            connection_verify=False
        )

        database = cosmos_client.create_database_if_not_exists(DB_NAME)
        container = database.create_container_if_not_exists(CONTAINER_NAME, partition_key=PartitionKey(path="/productName"))

        try:
            run_test(
                container,
                method,
            )
        finally:
            cosmos_client.close()


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main(["-x", __file__]))