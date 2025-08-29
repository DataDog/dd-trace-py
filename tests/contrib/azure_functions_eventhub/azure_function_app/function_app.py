import os

import azure.eventhub as azure_eventhub
import azure.functions as func

from ddtrace import patch


patch(azure_eventhub=True, azure_functions=True)


app = func.FunctionApp()


@app.route(route="httppostrooteventhub", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.POST])
def http_post_root_eventhub(req: func.HttpRequest) -> func.HttpResponse:
    with azure_eventhub.EventHubProducerClient.from_connection_string(
        conn_str=os.getenv("CONNECTION_STRING", ""),
        eventhub_name="eh1",
    ) as eventhub_producer_client:
        eventhub_producer_client.send_event(azure_eventhub.EventData('{"body":"test message"}'))
    return func.HttpResponse("Hello Datadog!")


@app.function_name(name="eventhub")
@app.event_hub_message_trigger(
    arg_name="event",
    event_hub_name="eh1",
    consumer_group="cg1",
    connection="CONNECTION_STRING",
)
def event_hub_trigger(event: func.EventHubEvent):
    pass
