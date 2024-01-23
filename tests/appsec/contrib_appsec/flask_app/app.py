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


@app.route("/")
def index():
    return "ok ASM"


@app.route("/asm/", methods=["GET", "POST", "OPTIONS"])
@app.route("/asm/<int:param_int>/<string:param_str>/")
def multi_view(param_int=0, param_str=""):
    query_params = request.args.to_dict()
    body = {
        "path_params": {"param_int": param_int, "param_str": param_str},
        "query_params": query_params,
        "headers": dict(request.headers),
        "cookies": dict(request.cookies),
        "body": request.data.decode("utf-8"),
        "method": request.method,
    }
    status = int(query_params.get("status", "200"))
    return body, status
