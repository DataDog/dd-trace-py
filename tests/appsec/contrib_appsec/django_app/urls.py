import json
import os
import sqlite3
import subprocess
import tempfile
from typing import Optional

import django
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.http import FileResponse
from django.http import HttpResponse
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

import ddtrace.constants
from ddtrace.trace import tracer


# django.conf.urls.url was deprecated in django 3 and removed in django 4
if django.VERSION < (4, 0, 0):
    from django.conf.urls import url as handler
else:
    from django.urls import re_path as handler

if django.VERSION >= (2, 0, 0):
    from django.urls import path
else:
    from django.conf.urls import url as path


# for user events


# creating users at start


@csrf_exempt
def healthcheck(request):
    return HttpResponse("ok ASM", status=200)


@csrf_exempt
def multi_view(request, param_int=0, param_str=""):
    query_params = request.GET.dict()
    body = {
        "path_params": {"param_int": param_int, "param_str": param_str},
        "query_params": query_params,
        "cookies": dict(request.COOKIES),
        "body": request.body.decode("utf-8"),
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
    # setting headers in the response with compatibility for django < 4.0
    json_response = JsonResponse(body, status=status)
    for k, v in response_headers.items():
        json_response[k] = v
    return json_response


DB = sqlite3.connect(":memory:")
DB.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT)")
DB.execute("INSERT INTO users (id, name) VALUES ('1_secret_id', 'Alice')")
DB.execute("INSERT INTO users (id, name) VALUES ('2_secret_id', 'Bob')")
DB.execute("INSERT INTO users (id, name) VALUES ('3_secret_id', 'Christophe')")


@csrf_exempt
def rasp(request, endpoint: str):
    query_params = request.GET.dict()
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
        return HttpResponse("<\br>\n".join(res))
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

                        request = urllib.request.Request(urlname)
                        with urllib.request.urlopen(request, timeout=0.15) as f:
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
        return HttpResponse("<\\br>\n".join(res))
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
        return HttpResponse("<\\br>\n".join(res))
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
        return HttpResponse("<\\br>\n".join(res))
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
        return HttpResponse("<\\br>\n".join(res))
    tracer.current_span()._local_root.set_tag("rasp.request.done", endpoint)
    return HttpResponse(f"Unknown endpoint: {endpoint}")


@csrf_exempt
def login_user(request):
    from django.contrib.auth import authenticate
    from django.contrib.auth import get_user_model
    from django.contrib.auth import login

    for username, email, passwd, last_name, user_id in [
        ("test", "testuser@ddog.com", "1234", "test", "social-security-id"),
        ("testuuid", "testuseruuid@ddog.com", "1234", "testuuid", "591dc126-8431-4d0f-9509-b23318d3dce4"),
    ]:
        try:
            CustomUser = get_user_model()
            if not CustomUser.objects.filter(id=user_id).exists():
                user = CustomUser.objects.create_user(username, email, passwd, last_name=last_name, id=user_id)
                user.save()
        except Exception:
            pass

    username = request.GET.get("username", "")
    password = request.GET.get("password", "")
    user = authenticate(username=username, password=password)
    if user is not None:
        login(request, user)
        return HttpResponse("OK")
    return HttpResponse("login failure", status=401)


@csrf_exempt
def login_user_sdk(request):
    """manual instrumentation login endpoint using SDK V2"""
    try:
        from ddtrace.appsec import track_user_sdk
    except ImportError:
        return HttpResponse("SDK V2 not available", status=422)

    USERS = {
        "test": {"email": "testuser@ddog.com", "password": "1234", "name": "test", "id": "social-security-id"},
        "testuuid": {
            "email": "testuseruuid@ddog.com",
            "password": "1234",
            "name": "testuuid",
            "id": "591dc126-8431-4d0f-9509-b23318d3dce4",
        },
    }
    metadata = json.loads(request.GET.get("metadata", "{}"))

    def authenticate(username: str, password: str) -> Optional[str]:
        """authenticate user"""
        if username in USERS:
            if USERS[username]["password"] == password:
                return USERS[username]["id"]
            else:
                track_user_sdk.track_login_failure(
                    login=username, user_id=USERS[username]["id"], exists=True, metadata=metadata
                )
                return None
        track_user_sdk.track_login_failure(login=username, exists=False, metadata=metadata)
        return None

    def login(user_id: str, login: str) -> None:
        """login user"""
        track_user_sdk.track_login_success(login=login, user_id=user_id, metadata=metadata)

    username = request.GET.get("username", "")
    password = request.GET.get("password", "")
    user_id = authenticate(username=username, password=password)
    if user_id is not None:
        login(user_id, username)
        return HttpResponse("OK")
    return HttpResponse("login failure", status=401)


@csrf_exempt
def new_service(request, service_name: str):
    import ddtrace

    ddtrace.trace.Pin._override(django, service=service_name, tracer=ddtrace.tracer)
    return HttpResponse(service_name, status=200)


def send_file(request):
    f = tempfile.NamedTemporaryFile()
    f.write(b"Stream Hello World!" * 100)
    return FileResponse(f, content_type="text/plain")


def authenticated_view(request):
    """
    This view can be used to test requests with an authenticated user. Create a
    user with a default username, save it and then use this user to log in.
    Always returns a 200.
    """

    user = User(username="Jane Doe")
    user.save()
    login(request, user)
    return HttpResponse(status=200)


def shutdown(request):
    # Endpoint used to flush traces to the agent when doing snapshots.
    tracer.shutdown()
    return HttpResponse(status=200)


urlpatterns = [
    handler(r"^$", healthcheck),
    handler(r"^asm/?$", multi_view),
]

if django.VERSION >= (2, 0, 0):
    urlpatterns += [
        path("asm/<int:param_int>/<str:param_str>/", multi_view, name="multi_view"),
        path("asm/<int:param_int>/<str:param_str>", multi_view, name="multi_view"),
        path("new_service/<str:service_name>/", new_service, name="new_service"),
        path("new_service/<str:service_name>", new_service, name="new_service"),
        path("rasp/<str:endpoint>/", rasp, name="rasp"),
        path("rasp/<str:endpoint>", rasp, name="rasp"),
        path("login/", login_user, name="login"),
        path("login", login_user, name="login"),
        path("login_sdk/", login_user_sdk, name="login_sdk"),
        path("login_sdk", login_user_sdk, name="login_sdk"),
    ]
else:
    urlpatterns += [
        path(r"asm/(?P<param_int>[0-9]{4})/(?P<param_str>\w+)/$", multi_view, name="multi_view"),
        path(r"asm/(?P<param_int>[0-9]{4})/(?P<param_str>\w+)$", multi_view, name="multi_view"),
        path(r"new_service/(?P<service_name>\w+)/$", new_service, name="new_service"),
        path(r"new_service/(?P<service_name>\w+)$", new_service, name="new_service"),
        path(r"rasp/(?P<endpoint>\w+)/$", new_service, name="rasp"),
        path(r"rasp/(?P<endpoint>\w+)$", new_service, name="rasp"),
        path("login/", login_user, name="login"),
        path("login", login_user, name="login"),
    ]
