import azure.functions as func
import time
import json
import logging

# from azure.cosmos.aio import CosmosClient
import os
import ddtrace.auto
from ddtrace import patch
# Ensure critical integrations are patched even if auto-instrumentation ordering changes.
patch(
    requests=True,
    azure_functions=True,
    azure_cosmos=True,
)

from azure.cosmos import CosmosClient, exceptions, PartitionKey
import azure.cosmos as cosmos

app = func.FunctionApp()

client = CosmosClient(
    url="http://localhost:8081",
    credential=(
        "C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw=="
    ),
    connection_verify=False
)


# create/get database
DATABASE_NAME = "wheeee"
try:
    database = client.create_database(DATABASE_NAME)
except exceptions.CosmosResourceExistsError:
    database = client.get_database_client(DATABASE_NAME)

# create/get container
CONTAINER_NAME = "yippee"
try:
    container = database.create_container(
        id=CONTAINER_NAME, partition_key=PartitionKey(path="/productName")
    )
except exceptions.CosmosResourceExistsError:
    container = database.get_container_client(CONTAINER_NAME)



@app.route(route="HttpExample", auth_level=func.AuthLevel.FUNCTION)
async def HttpExample(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Python HTTP trigger function processed a request.")

    try:
        # create data
        for i in range(1, 20):
            container.create_item(
                {
                    "id": "item{0}".format(i),
                    "productName": "Widget",
                    "productModel": "Model {0}".format(i),
                }
            )

        # read data
        for i in range(1, 20):
            response = container.read_item(item="{0}".format(i), partition_key="Widget")
            print(response)

        # update data
        for i in range(10, 20):
            container.upsert_item(
                {
                    "id": "item{0}".format(i),
                    "productName": "Widget",
                    "productModel": "Model {0}".format(i + 30),
                }
            )

        # delete data
        for item in container.query_items(
            query='SELECT * FROM mycontainer p WHERE p.productModel = "Model 2"',
            enable_cross_partition_query=True,
        ):
            container.delete_item(item["id"], partition_key="Widget")


        response = "Success: "
        return func.HttpResponse(response, status_code=200)

    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return func.HttpResponse(f"Error: {str(e)}", status_code=500)


"""async def AsyncHelper(url, key):
    client = CosmosClient(
            url="http://localhost:8081",
            credential=(
                "C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw=="
            ),
            connection_verify=False
        )
    
    DATABASE_NAME = 'nyoom'
    CONTAINER_NAME = 'beep'
    try:
        database = await client.create_database(DATABASE_NAME)
    except exceptions.CosmosResourceExistsError:
        database = client.get_database_client(DATABASE_NAME)

    # create/get container
    try:
        container = await database.create_container(id=CONTAINER_NAME, partition_key=PartitionKey(path="/productName"))
    except exceptions.CosmosResourceExistsError:
        container = database.get_container_client(CONTAINER_NAME)
    except exceptions.CosmosHttpResponseError:
        raise

    print("hihi")
    for i in range(10):
        await container.upsert_item({
                'id': 'item{0}'.format(i),
                'productName': 'Widget',
                'productModel': 'Model {0}'.format(i)
            }
        )

    print("here")
    results = container.query_items(
            query='SELECT * FROM products p WHERE p.productModel = "Model 3"')

    async for item in container.query_items(
            query='SELECT * FROM mycontainer p WHERE p.productModel = "Model 2"'):
        await container.delete_item(item['id'], partition_key='Widget')

    # iterates on "results" iterator to asynchronously create a complete list of the actual query results
    print("there")
    item_list = []
    async for item in results:
        print(json.dumps(item, indent=True))
        item_list.append(item)
    print("where")
    # Asynchronously creates a complete list of the actual query results. This code performs the same action as the for-loop example above.
    item_list = [item async for item in results]

    await client.close()
"""
