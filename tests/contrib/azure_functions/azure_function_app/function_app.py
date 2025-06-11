import os

import azure.servicebus as azure_servicebus
import azure.servicebus.aio as azure_servicebus_async

from ddtrace import patch


patch(azure_functions=True, azure_servicebus=True, requests=True)

import azure.functions as func  # noqa: E402
import requests  # noqa: E402


app = func.FunctionApp()


@app.route(route="httpgetok", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.GET])
def http_get_ok(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse("Hello Datadog!")


@app.route(route="httpgetokasync", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.GET])
async def http_get_ok_async(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse("Hello Datadog!")


@app.route(route="httpgeterror", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.GET])
def http_get_error(req: func.HttpRequest) -> func.HttpResponse:
    raise Exception("Test Error")


@app.route(route="httppostok", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.POST])
def http_post_ok(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse("Hello Datadog!")


@app.route(
    route="httpgettriggerarg",
    auth_level=func.AuthLevel.ANONYMOUS,
    methods=[func.HttpMethod.GET],
    trigger_arg_name="reqarg",
)
def http_get_trigger_arg(reqarg: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse("Hello Datadog!")


@app.function_name(name="functionnamedecorator")
@app.route(route="httpgetfunctionnamedecorator", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.GET])
def http_get_function_name_decorator(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse("Hello Datadog!")


@app.route(route="httpgetfunctionnamenodecorator", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.GET])
def http_get_function_name_no_decorator(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse("Hello Datadog!")


@app.route(
    route="httpgetfunctionnamedecoratororder", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.GET]
)
@app.function_name(name="functionnamedecoratororder")
def http_get_function_name_decorator_order(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse("Hello Datadog!")


@app.route(route="httpgetroot", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.GET])
def http_get_root(req: func.HttpRequest) -> func.HttpResponse:
    requests.get(f"http://localhost:{os.environ['AZURE_FUNCTIONS_TEST_PORT']}/api/httpgetchild", timeout=5)
    return func.HttpResponse("Hello Datadog!")


@app.route(route="httpgetchild", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.GET])
def http_get_child(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse("Hello Datadog!")


@app.route(route="httppostrootservicebus", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.POST])
def http_post_root_servicebus(req: func.HttpRequest) -> func.HttpResponse:
    message_with_properties = azure_servicebus.ServiceBusMessage(
        "test message 1",
        application_properties={"property": "val", b"byteproperty": b"byteval"},
    )
    message_with_properties_none = azure_servicebus.ServiceBusMessage("test message 2")
    with azure_servicebus.ServiceBusClient.from_connection_string(
        conn_str=os.getenv("CONNECTION_STRING", "")
    ) as servicebus_client:
        with servicebus_client.get_queue_sender(queue_name="queue.1") as queue_sender:
            queue_sender.send_messages(message_with_properties)
            queue_sender.send_messages([message_with_properties, message_with_properties_none])
        with servicebus_client.get_topic_sender(topic_name="topic.1") as topic_sender:
            topic_sender.send_messages(message_with_properties)
            topic_sender.send_messages([message_with_properties, message_with_properties_none])
    return func.HttpResponse("Hello Datadog!")


@app.route(route="httppostrootservicebusasync", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.POST])
async def http_post_root_servicebus_async(req: func.HttpRequest) -> func.HttpResponse:
    message_with_properties = azure_servicebus.ServiceBusMessage(
        "test message 1",
        application_properties={"property": "val", b"byteproperty": b"byteval"},
    )
    message_with_properties_none = azure_servicebus.ServiceBusMessage("test message 2")
    async with azure_servicebus_async.ServiceBusClient.from_connection_string(
        conn_str=os.getenv("CONNECTION_STRING", "")
    ) as servicebus_client:
        async with servicebus_client.get_queue_sender(queue_name="queue.1") as queue_sender:
            await queue_sender.send_messages(message_with_properties)
            await queue_sender.send_messages([message_with_properties, message_with_properties_none])
        async with servicebus_client.get_topic_sender(topic_name="topic.1") as topic_sender:
            await topic_sender.send_messages(message_with_properties)
            await topic_sender.send_messages([message_with_properties, message_with_properties_none])
    return func.HttpResponse("Hello Datadog!")


@app.function_name(name="servicebusqueue")
@app.service_bus_queue_trigger(arg_name="msg", queue_name="queue.1", connection="CONNECTION_STRING")
def service_bus_queue(msg: func.ServiceBusMessage):
    pass


@app.function_name(name="servicebustopic")
@app.service_bus_topic_trigger(
    arg_name="msg", topic_name="topic.1", connection="CONNECTION_STRING", subscription_name="subscription.3"
)
def service_bus_topic(msg: func.ServiceBusMessage):
    pass


@app.timer_trigger(schedule="0 0 0 1 1 *", arg_name="timer")
def timer(timer: func.TimerRequest) -> None:
    pass


@app.timer_trigger(schedule="0 0 0 1 1 *", arg_name="timer")
async def timer_async(timer: func.TimerRequest) -> None:
    pass
