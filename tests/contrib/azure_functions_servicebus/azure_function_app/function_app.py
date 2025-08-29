import os

import azure.servicebus as azure_servicebus

from ddtrace import patch


patch(azure_functions=True, azure_servicebus=True)

import azure.functions as func  # noqa: E402


app = func.FunctionApp()


@app.route(route="httppostrootservicebus", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.POST])
def http_post_root_servicebus(req: func.HttpRequest) -> func.HttpResponse:
    with azure_servicebus.ServiceBusClient.from_connection_string(
        conn_str=os.getenv("CONNECTION_STRING", "")
    ) as servicebus_client:
        with servicebus_client.get_queue_sender(queue_name="queue.1") as queue_sender:
            queue_sender.send_messages(azure_servicebus.ServiceBusMessage('{"body":"test message"}'))
        with servicebus_client.get_topic_sender(topic_name="topic.1") as topic_sender:
            topic_sender.send_messages([azure_servicebus.ServiceBusMessage('{"body":"test message"}')])
    return func.HttpResponse("Hello Datadog!")


@app.route(
    route="httppostrootservicebusmanysamecontext", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.POST]
)
def http_post_root_servicebus_many_same_context(req: func.HttpRequest) -> func.HttpResponse:
    with azure_servicebus.ServiceBusClient.from_connection_string(
        conn_str=os.getenv("CONNECTION_STRING", "")
    ) as servicebus_client:
        with servicebus_client.get_topic_sender(topic_name="topic.1") as topic_sender:
            topic_sender.send_messages(
                [
                    azure_servicebus.ServiceBusMessage('{"body":"test message 1"}'),
                    azure_servicebus.ServiceBusMessage('{"body":"test message 2"}'),
                ]
            )
    return func.HttpResponse("Hello Datadog!")


@app.route(
    route="httppostrootservicebusmanydiffcontext", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.POST]
)
def http_post_root_servicebus_many_diff_context(req: func.HttpRequest) -> func.HttpResponse:
    with azure_servicebus.ServiceBusClient.from_connection_string(
        conn_str=os.getenv("CONNECTION_STRING", "")
    ) as servicebus_client:
        with servicebus_client.get_topic_sender(topic_name="topic.1") as topic_sender:
            topic_sender.send_messages([azure_servicebus.ServiceBusMessage('{"body":"test message 1"}')])
            topic_sender.send_messages([azure_servicebus.ServiceBusMessage('{"body":"test message 2"}')])
    return func.HttpResponse("Hello Datadog!")


@app.function_name(name="servicebusqueue")
@app.service_bus_queue_trigger(arg_name="msg", queue_name="queue.1", connection="CONNECTION_STRING")
def service_bus_queue(msg: func.ServiceBusMessage):
    pass


@app.function_name(name="servicebustopic")
@app.service_bus_topic_trigger(
    arg_name="msg",
    topic_name="topic.1",
    connection="CONNECTION_STRING",
    subscription_name="subscription.3",
    cardinality=os.getenv("CARDINALITY", "one"),
)
def service_bus_topic(msg: func.ServiceBusMessage):
    pass
