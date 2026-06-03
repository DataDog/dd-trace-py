import os
from uuid import uuid4

import azure.eventhub as azure_eventhub
import azure.functions as func

import ddtrace.auto  # noqa: F401


app = func.FunctionApp()


@app.route(route="sendeventsingle", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.POST])
def send_event(req: func.HttpRequest) -> func.HttpResponse:
    with azure_eventhub.EventHubProducerClient.from_connection_string(
        conn_str=os.getenv("CONNECTION_STRING", ""),
        eventhub_name="eh1",
    ) as eventhub_producer_client:
        event = azure_eventhub.EventData('{"body":"test message"}')
        event.message_id = str(uuid4())
        eventhub_producer_client.send_event(event)
    return func.HttpResponse("Hello Datadog!")


@app.route(route="sendeventbatch", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.POST])
def send_batch(req: func.HttpRequest) -> func.HttpResponse:
    with azure_eventhub.EventHubProducerClient.from_connection_string(
        conn_str=os.getenv("CONNECTION_STRING", ""),
        eventhub_name="eh1",
    ) as eventhub_producer_client:
        batch = eventhub_producer_client.create_batch()
        batch.add(azure_eventhub.EventData('{"body":"test message"}'))
        batch.add(azure_eventhub.EventData('{"body":"test message"}'))
        eventhub_producer_client.send_batch(batch)
    return func.HttpResponse("Hello Datadog!")


if os.getenv("IS_ASYNC") == "True":

    @app.function_name(name="eventhub")
    @app.event_hub_message_trigger(
        arg_name="event",
        event_hub_name="eh1",
        consumer_group="cg1",
        connection="CONNECTION_STRING",
        cardinality=os.getenv("CARDINALITY", "one"),
    )
    async def event_hub_trigger(event: func.EventHubEvent):
        pass

else:

    @app.function_name(name="eventhub")
    @app.event_hub_message_trigger(
        arg_name="event",
        event_hub_name="eh1",
        consumer_group="cg1",
        connection="CONNECTION_STRING",
        cardinality=os.getenv("CARDINALITY", "one"),
    )
    def event_hub_trigger(event: func.EventHubEvent):
        pass
