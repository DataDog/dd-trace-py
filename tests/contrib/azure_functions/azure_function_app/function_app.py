from ddtrace import patch


patch(azure_functions=True)

import azure.functions as func  # noqa: E402


app = func.FunctionApp()


@app.route(route="httptest", auth_level=func.AuthLevel.ANONYMOUS)
def http_test(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse("Hello Datadog!")
