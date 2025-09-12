import os

import azure.functions as func
import requests

from ddtrace import patch


patch(azure_functions=True, azure_servicebus=True, requests=True)


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
    requests.get(
        f"http://localhost:{os.environ['AZURE_FUNCTIONS_TEST_PORT']}/api/httpgetchild",
        headers={"User-Agent": "python-requests/x.xx.x"},
        timeout=5,
    )
    return func.HttpResponse("Hello Datadog!")


@app.route(route="httpgetchild", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.GET])
def http_get_child(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse("Hello Datadog!")


@app.timer_trigger(schedule="0 0 0 1 1 *", arg_name="timer")
def timer(timer: func.TimerRequest) -> None:
    pass


@app.timer_trigger(schedule="0 0 0 1 1 *", arg_name="timer")
async def timer_async(timer: func.TimerRequest) -> None:
    pass
