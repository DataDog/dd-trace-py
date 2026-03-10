import os
import pytest

import azure.cosmos as azure_cosmos
import azure.cosmos.aio as azure_cosmos_aio

CONNECTION_STRING = "AccountEndpoint=https://localhost:8081/;AccountKey=C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw==;"
DB_NAME = "db.1"
CONTAINER_NAME = "container.1"

def run_test():
    return


@pytest.mark.asyncio
async def test_common():
    is_async = os.environ.get("IS_ASYNC") == "True"

    if is_async:
        cosmos_client = azure_cosmos.CosmosClient.from_connection_string(
            CONNECTION_STRING,
            connection_verify=False
        )
        (consumer_client, event_handler, receive_task) = await create_event_handler_async()
        try:
            await run_test_async(
                producer_client,
                event_handler,
                method,
                message_payload_type,
                distributed_tracing_enabled,
                batch_links_enabled,
            )
        finally:
            await producer_client.close()
            await close_event_handler_async(consumer_client, receive_task)
    else:
        producer_client = EventHubProducerClient.from_connection_string(
            conn_str=CONNECTION_STRING,
            eventhub_name=EVENTHUB_NAME,
            buffered_mode=buffered_mode,
            on_error=on_error,
            on_success=on_success,
        )
        (consumer_client, event_handler, thread) = create_event_handler()
        try:
            run_test(
                producer_client,
                event_handler,
                method,
                message_payload_type,
                distributed_tracing_enabled,
                batch_links_enabled,
            )
        finally:
            producer_client.close()
            close_event_handler(consumer_client, thread)


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main(["-x", __file__]))