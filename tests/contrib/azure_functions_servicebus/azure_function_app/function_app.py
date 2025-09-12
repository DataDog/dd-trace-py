import os

import azure.functions as func
import azure.servicebus as azure_servicebus

from ddtrace import patch


patch(azure_functions=True, azure_servicebus=True, requests=True)

app = func.FunctionApp()


@app.route(route="queuesendmessagesingle", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.POST])
def queue_send_single_message(req: func.HttpRequest) -> func.HttpResponse:
    with azure_servicebus.ServiceBusClient.from_connection_string(
        conn_str=os.getenv("CONNECTION_STRING", "")
    ) as servicebus_client:
        with servicebus_client.get_queue_sender(queue_name="queue.1") as queue_sender:
            queue_sender.send_messages(azure_servicebus.ServiceBusMessage('{"body":"test message"}'))
    return func.HttpResponse("Hello Datadog!")


@app.route(route="queuesendmessagebatch", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.POST])
def queue_send_message_batch(req: func.HttpRequest) -> func.HttpResponse:
    with azure_servicebus.ServiceBusClient.from_connection_string(
        conn_str=os.getenv("CONNECTION_STRING", "")
    ) as servicebus_client:
        with servicebus_client.get_queue_sender(queue_name="queue.1") as queue_sender:
            batch = queue_sender.create_message_batch()
            batch.add_message(azure_servicebus.ServiceBusMessage('{"body":"test message"}'))
            batch.add_message(azure_servicebus.ServiceBusMessage('{"body":"test message"}'))
            queue_sender.send_messages(batch)
    return func.HttpResponse("Hello Datadog!")


@app.route(route="topicsendmessagesingle", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.POST])
def topic_send_single_message(req: func.HttpRequest) -> func.HttpResponse:
    with azure_servicebus.ServiceBusClient.from_connection_string(
        conn_str=os.getenv("CONNECTION_STRING", "")
    ) as servicebus_client:
        with servicebus_client.get_topic_sender(topic_name="topic.1") as topic_sender:
            topic_sender.send_messages(azure_servicebus.ServiceBusMessage('{"body":"test message"}'))
    return func.HttpResponse("Hello Datadog!")


@app.route(route="topicsendmessagebatch", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.POST])
def topic_send_message_batch(req: func.HttpRequest) -> func.HttpResponse:
    with azure_servicebus.ServiceBusClient.from_connection_string(
        conn_str=os.getenv("CONNECTION_STRING", "")
    ) as servicebus_client:
        with servicebus_client.get_topic_sender(topic_name="topic.1") as topic_sender:
            batch = topic_sender.create_message_batch()
            batch.add_message(azure_servicebus.ServiceBusMessage('{"body":"test message"}'))
            batch.add_message(azure_servicebus.ServiceBusMessage('{"body":"test message"}'))
            topic_sender.send_messages(batch)
    return func.HttpResponse("Hello Datadog!")


if os.getenv("IS_ASYNC") == "True":

    @app.function_name(name="servicebusqueue")
    @app.service_bus_queue_trigger(
        arg_name="msg",
        queue_name="queue.1",
        connection="CONNECTION_STRING",
        cardinality=os.getenv("CARDINALITY", "one"),
    )
    async def service_bus_queue(msg: func.ServiceBusMessage):
        pass

    @app.function_name(name="servicebustopic")
    @app.service_bus_topic_trigger(
        arg_name="msg",
        topic_name="topic.1",
        subscription_name="subscription.3",
        connection="CONNECTION_STRING",
        cardinality=os.getenv("CARDINALITY", "one"),
    )
    async def service_bus_topic(msg: func.ServiceBusMessage):
        pass

else:

    @app.function_name(name="servicebusqueue")
    @app.service_bus_queue_trigger(
        arg_name="msg",
        queue_name="queue.1",
        connection="CONNECTION_STRING",
        cardinality=os.getenv("CARDINALITY", "one"),
    )
    def service_bus_queue(msg: func.ServiceBusMessage):
        pass

    @app.function_name(name="servicebustopic")
    @app.service_bus_topic_trigger(
        arg_name="msg",
        topic_name="topic.1",
        subscription_name="subscription.3",
        connection="CONNECTION_STRING",
        cardinality=os.getenv("CARDINALITY", "one"),
    )
    def service_bus_topic(msg: func.ServiceBusMessage):
        pass
