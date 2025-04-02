from ddtrace import patch


patch(azure_functions=True)

import azure.functions as func  # noqa: E402


app = func.FunctionApp()


@app.route(route="httpgetok", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.GET])
def http_get_ok(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse("Hello Datadog!")


@app.route(route="httpgeterror", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.GET])
def http_get_error(req: func.HttpRequest) -> func.HttpResponse:
    raise Exception("Test Error")


@app.route(route="httppostok", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.POST])
def http_post_ok(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse("Hello Datadog!")


@app.function_name(name="functionnamedecorator")
@app.route(route="httpgetfunctionname", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.GET])
def http_get_function_name(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse("Hello Datadog!")
