import json
import os
import sqlite3
import subprocess
from typing import Optional

from flask import Flask
from flask import request

# from ddtrace.appsec.iast import ddtrace_iast_flask_patch
import ddtrace.constants
from ddtrace.trace import tracer
from tests.webclient import PingFilter


tracer.configure(trace_processors=[PingFilter()])
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

    ddtrace.trace.Pin._override(Flask, service=service_name, tracer=ddtrace.tracer)
    return service_name


DB = sqlite3.connect(":memory:")
DB.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT)")
DB.execute("INSERT INTO users (id, name) VALUES ('1_secret_id', 'Alice')")
DB.execute("INSERT INTO users (id, name) VALUES ('2_secret_id', 'Bob')")
DB.execute("INSERT INTO users (id, name) VALUES ('3_secret_id', 'Christophe')")


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
    elif endpoint == "sql_injection":
        res = ["sql_injection endpoint"]
        for param in query_params:
            if param.startswith("user_id"):
                user_id = query_params[param]
            try:
                if param.startswith("user_id"):
                    cursor = DB.execute(f"SELECT * FROM users WHERE id = {user_id}")
                    res.append(f"Url: {list(cursor)}")
            except Exception as e:
                res.append(f"Error: {e}")
        tracer.current_span()._local_root.set_tag("rasp.request.done", endpoint)
        return "<\\br>\n".join(res)
    elif endpoint == "shell_injection":
        res = ["shell_injection endpoint"]
        for param in query_params:
            if param.startswith("cmd"):
                cmd = query_params[param]
                try:
                    if param.startswith("cmdsys"):
                        res.append(f'cmd stdout: {os.system(f"ls {cmd}")}')
                    else:
                        res.append(f'cmd stdout: {subprocess.run(f"ls {cmd}", shell=True)}')
                except Exception as e:
                    res.append(f"Error: {e}")
        tracer.current_span()._local_root.set_tag("rasp.request.done", endpoint)
        return "<\\br>\n".join(res)
    elif endpoint == "command_injection":
        res = ["command_injection endpoint"]
        for param in query_params:
            if param.startswith("cmda"):
                cmd = query_params[param]
                try:
                    res.append(f'cmd stdout: {subprocess.run([cmd, "-c", "3", "localhost"])}')
                except Exception as e:
                    res.append(f"Error: {e}")
            elif param.startswith("cmds"):
                cmd = query_params[param]
                try:
                    res.append(f"cmd stdout: {subprocess.run(cmd)}")
                except Exception as e:
                    res.append(f"Error: {e}")
        tracer.current_span()._local_root.set_tag("rasp.request.done", endpoint)
        return "<\\br>\n".join(res)
    tracer.current_span()._local_root.set_tag("rasp.request.done", endpoint)
    return f"Unknown endpoint: {endpoint}"


# Auto user event manual instrumentation


@app.route("/login/", methods=["GET"])
@app.route("/login", methods=["GET"])
def login_user():
    """manual instrumentation login endpoint"""
    from ddtrace.appsec import trace_utils as appsec_trace_utils

    USERS = {
        "test": {"email": "testuser@ddog.com", "password": "1234", "name": "test", "id": "social-security-id"},
        "testuuid": {
            "email": "testuseruuid@ddog.com",
            "password": "1234",
            "name": "testuuid",
            "id": "591dc126-8431-4d0f-9509-b23318d3dce4",
        },
    }

    def authenticate(username: str, password: str) -> Optional[str]:
        """authenticate user"""
        if username in USERS:
            if USERS[username]["password"] == password:
                return USERS[username]["id"]
            else:
                appsec_trace_utils.track_user_login_failure_event(
                    tracer, user_id=USERS[username]["id"], exists=True, login_events_mode="auto", login=username
                )
                return None
        appsec_trace_utils.track_user_login_failure_event(
            tracer, user_id=username, exists=False, login_events_mode="auto", login=username
        )
        return None

    def login(user_id: str, login: str) -> None:
        """login user"""
        appsec_trace_utils.track_user_login_success_event(
            tracer, user_id=user_id, login_events_mode="auto", login=login
        )

    username = request.args.get("username", "")
    password = request.args.get("password", "")
    user_id = authenticate(username=username, password=password)
    if user_id is not None:
        login(user_id, username)
        return "OK"
    return "login failure", 401


@app.route("/login_sdk/", methods=["GET"])
@app.route("/login_sdk", methods=["GET"])
def login_user_sdk():
    """manual instrumentation login endpoint using SDK V2"""
    try:
        from ddtrace.appsec import track_user_sdk
    except ImportError:
        return "SDK V2 not available", 422

    USERS = {
        "test": {"email": "testuser@ddog.com", "password": "1234", "name": "test", "id": "social-security-id"},
        "testuuid": {
            "email": "testuseruuid@ddog.com",
            "password": "1234",
            "name": "testuuid",
            "id": "591dc126-8431-4d0f-9509-b23318d3dce4",
        },
    }
    metadata = json.loads(request.args.get("metadata", "{}"))

    def authenticate(username: str, password: str) -> Optional[str]:
        """authenticate user"""
        if username in USERS:
            if USERS[username]["password"] == password:
                return USERS[username]["id"]
            track_user_sdk.track_login_failure(
                login=username, user_id=USERS[username]["id"], exists=True, metadata=metadata
            )
            return None
        track_user_sdk.track_login_failure(login=username, exists=False, metadata=metadata)
        return None

    def login(user_id: str, login: str) -> None:
        """login user"""
        track_user_sdk.track_login_success(login=login, user_id=user_id, metadata=metadata)

    username = request.args.get("username", "")
    password = request.args.get("password", "")
    user_id = authenticate(username=username, password=password)
    if user_id is not None:
        login(user_id, username)
        return "OK"
    return "login failure", 401
