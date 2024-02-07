import os

from flask import Flask
from flask import request

from ddtrace import tracer
from tests.webclient import PingFilter


tracer.configure(
    settings={
        "FILTERS": [PingFilter()],
    }
)
cur_dir = os.path.dirname(os.path.realpath(__file__))
tmpl_path = os.path.join(cur_dir, "test_templates")
app = Flask(__name__, template_folder=tmpl_path)


@app.route("/", methods=["GET", "POST", "OPTIONS"])
def index():
    return "ok ASM"


@app.route("/asm/", methods=["GET", "POST", "OPTIONS"])
@app.route("/asm/<int:param_int>/<string:param_str>/", methods=["GET", "POST", "OPTIONS"])
@app.route("/asm/<int:param_int>/<string:param_str>", methods=["GET", "POST", "OPTIONS"])
def multi_view(param_int=0, param_str=""):
    query_params = request.args.to_dict()
    body = {
        "path_params": {"param_int": param_int, "param_str": param_str},
        "query_params": query_params,
        "cookies": dict(request.cookies),
        "body": request.data.decode("utf-8"),
        "method": request.method,
    }
    status = int(query_params.get("status", "200"))
    headers_query = query_params.get("headers", "").split(",")
    response_headers = {}
    for header in headers_query:
        vk = header.split("=")
        if len(vk) == 2:
            response_headers[vk[0]] = vk[1]
    return body, status, response_headers


@app.route("/new_service/<string:service_name>/", methods=["GET", "POST", "OPTIONS"])
@app.route("/new_service/<string:service_name>", methods=["GET", "POST", "OPTIONS"])
def new_service(service_name: str):
    import ddtrace

    ddtrace.Pin.override(Flask, service=service_name, tracer=ddtrace.tracer)
    return service_name
