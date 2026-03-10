import azure.functions as func
import time
import json
import logging

import os
import ddtrace.auto


from azure.cosmos import CosmosClient, exceptions, PartitionKey
from azure.cosmos.aio import CosmosClient as CosmosClientAio
import azure.cosmos as cosmos


CONNECTION_STRING = "AccountEndpoint=https://localhost:8081/;AccountKey=C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw==;"
DB_NAME = "db.1"
CONTAINER_NAME = "container.1"

app = func.FunctionApp()


@app.route(route="create_item", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.POST])
def CreateItem(req: func.HttpRequest) -> func.HttpResponse:
    if os.getenv("IS_ASYNC") == "True":
        client = CosmosClientAio.from_connection_string(
            CONNECTION_STRING,
            connection_verify=False
        )

        database = client.create_database_if_not_exists(DATABASE_NAME)
        container = database.create_container_if_not_exists(
                id=CONTAINER_NAME, partition_key=PartitionKey(path="/productName")
            )

        async for i in range(1, 5):
            await container.create_item(
                {
                    "id": "item{0}".format(i),
                    "productName": "Widget",
                    "productModel": "Model {0}".format(i),
                }
            )

    else:
        client = CosmosClient.from_connection_string(
            CONNECTION_STRING,
            connection_verify=False
        )

        database = client.create_database_if_not_exists(DATABASE_NAME)
        container = database.create_container_if_not_exists(
                id=CONTAINER_NAME, partition_key=PartitionKey(path="/productName")
            )

        for i in range(1, 5):
            container.create_item(
                {
                    "id": "item{0}".format(i),
                    "productName": "Widget",
                    "productModel": "Model {0}".format(i),
                }
            )
    
    return func.HttpResponse("Hello Datadog!")

@app.route(route="read_item", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.POST])
def ReadItem(req: func.HttpRequest) -> func.HttpResponse:
    if os.getenv("IS_ASYNC") == "True":
        client = CosmosClientAio.from_connection_string(
            CONNECTION_STRING,
            connection_verify=False
        )

        database = client.create_database_if_not_exists(DATABASE_NAME)
        container = database.create_container_if_not_exists(
                id=CONTAINER_NAME, partition_key=PartitionKey(path="/productName")
            )

        async for i in range(1, 5):
            await container.read_item(
                {
                    "id": "item{0}".format(i),
                    , partition_key="Widget"
                }
            )

    else:
        client = CosmosClient.from_connection_string(
            CONNECTION_STRING,
            connection_verify=False
        )

        database = client.create_database_if_not_exists(DATABASE_NAME)
        container = database.create_container_if_not_exists(
                id=CONTAINER_NAME, partition_key=PartitionKey(path="/productName")
            )

        for i in range(1, 5):
            container.read_item(
                {
                    "id": "item{0}".format(i),
                    , partition_key="Widget"
                }
            )
    
    return func.HttpResponse("Hello Datadog!")


@app.route(route="upsert_item", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.POST])
def UpsertItem(req: func.HttpRequest) -> func.HttpResponse:
    if os.getenv("IS_ASYNC") == "True":
        client = CosmosClientAio.from_connection_string(
            CONNECTION_STRING,
            connection_verify=False
        )

        database = client.create_database_if_not_exists(DATABASE_NAME)
        container = database.create_container_if_not_exists(
                id=CONTAINER_NAME, partition_key=PartitionKey(path="/productName")
            )

        async for i in range(1, 5):
             await container.upsert_item(
                        {
                            "id": "item{0}".format(i),
                            "productName": "Widget",
                            "productModel": "Model {0}".format(i + 1),
                        }
                    )

    else:
        client = CosmosClient.from_connection_string(
            CONNECTION_STRING,
            connection_verify=False
        )

        database = client.create_database_if_not_exists(DATABASE_NAME)
        container = database.create_container_if_not_exists(
                id=CONTAINER_NAME, partition_key=PartitionKey(path="/productName")
            )

        for i in range(1, 5):
            container.upsert_item(
                        {
                            "id": "item{0}".format(i),
                            "productName": "Widget",
                            "productModel": "Model {0}".format(i + 1),
                        }
                    )
    
    return func.HttpResponse("Hello Datadog!")

@app.route(route="delete_item", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.POST])
def DeleteItem(req: func.HttpRequest) -> func.HttpResponse:
    if os.getenv("IS_ASYNC") == "True":
        client = CosmosClientAio.from_connection_string(
            CONNECTION_STRING,
            connection_verify=False
        )

        database = client.create_database_if_not_exists(DATABASE_NAME)
        container = database.create_container_if_not_exists(
                id=CONTAINER_NAME, partition_key=PartitionKey(path="/productName")
            )

        async for item in container.query_items(
                    query='SELECT * FROM mycontainer p WHERE p.productModel = "Model 2"',
                    enable_cross_partition_query=True,
                ):
                    await container.delete_item(item["id"], partition_key="Widget")

    else:
        client = CosmosClient.from_connection_string(
            CONNECTION_STRING,
            connection_verify=False
        )

        database = client.create_database_if_not_exists(DATABASE_NAME)
        container = database.create_container_if_not_exists(
                id=CONTAINER_NAME, partition_key=PartitionKey(path="/productName")
            )

        for item in container.query_items(
                    query='SELECT * FROM mycontainer p WHERE p.productModel = "Model 2"',
                    enable_cross_partition_query=True,
                ):
                    container.delete_item(item["id"], partition_key="Widget")
    
    return func.HttpResponse("Hello Datadog!")