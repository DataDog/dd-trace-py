import os

from flask import Flask
from flask import request

from ddtrace import tracer

# from ddtrace.appsec.iast import ddtrace_iast_flask_patch
import ddtrace.constants
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
    priority = query_params.get("priority", None)
    if priority in ("keep", "drop"):
        tracer.current_span().set_tag(
            ddtrace.constants.MANUAL_KEEP_KEY if priority == "keep" else ddtrace.constants.MANUAL_DROP_KEY
        )
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


@app.route("/rasp/<string:endpoint>/", methods=["GET", "POST", "OPTIONS"])
def rasp(endpoint: str):
    query_params = request.args
    if endpoint == "lfi":
        res = ["lfi endpoint"]
        for param in query_params:
            if param.startswith("filename"):
                filename = query_params[param]
                try:
                    with open(filename, "rb") as f:
                        res.append(f"File: {f.read()}")
                except Exception as e:
                    res.append(f"Error: {e}")
        tracer.current_span()._local_root.set_tag("rasp.request.done", endpoint)
        return "<\\br>\n".join(res)
    elif endpoint == "ssrf":
        res = ["ssrf endpoint"]
        for param in query_params:
            if param.startswith("url"):
                urlname = query_params[param]
                if not urlname.startswith("http"):
                    urlname = f"http://{urlname}"
            try:
                if param.startswith("url_urlopen_request"):
                    import urllib.request

                    req = urllib.request.Request(urlname)
                    with urllib.request.urlopen(req, timeout=0.15) as f:
                        res.append(f"Url: {f.read()}")
                elif param.startswith("url_urlopen_string"):
                    import urllib.request

                    with urllib.request.urlopen(urlname, timeout=0.15) as f:
                        res.append(f"Url: {f.read()}")
                elif param.startswith("url_requests"):
                    import requests

                    r = requests.get(urlname, timeout=0.15)
                    res.append(f"Url: {r.text}")
            except Exception as e:
                res.append(f"Error: {e}")
        tracer.current_span()._local_root.set_tag("rasp.request.done", endpoint)
        return "<\\br>\n".join(res)
    elif endpoint == "shell":
        res = ["shell endpoint"]
        for param in query_params:
            if param.startswith("cmd"):
                cmd = query_params[param]
                try:
                    import subprocess

                    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as f:
                        res.append(f"cmd stdout: {f.stdout.read()}")
                except Exception as e:
                    res.append(f"Error: {e}")
        tracer.current_span()._local_root.set_tag("rasp.request.done", endpoint)
        return "<\\br>\n".join(res)
    tracer.current_span()._local_root.set_tag("rasp.request.done", endpoint)
    return f"Unknown endpoint: {endpoint}"
