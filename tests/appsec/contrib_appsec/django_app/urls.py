import tempfile

import django
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.http import FileResponse
from django.http import HttpResponse
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from ddtrace import tracer
import ddtrace.constants


# django.conf.urls.url was deprecated in django 3 and removed in django 4
if django.VERSION < (4, 0, 0):
    from django.conf.urls import url as handler
else:
    from django.urls import re_path as handler

if django.VERSION >= (2, 0, 0):
    from django.urls import path
else:
    from django.conf.urls import url as path


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
        return HttpResponse("<\\br>\n".join(res))
    tracer.current_span()._local_root.set_tag("rasp.request.done", endpoint)
    return HttpResponse(f"Unknown endpoint: {endpoint}")


@csrf_exempt
def new_service(request, service_name: str):
    import ddtrace

    ddtrace.Pin.override(django, service=service_name, tracer=ddtrace.tracer)
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
    ]
else:
    urlpatterns += [
        path(r"asm/(?P<param_int>[0-9]{4})/(?P<param_str>\w+)/$", multi_view, name="multi_view"),
        path(r"asm/(?P<param_int>[0-9]{4})/(?P<param_str>\w+)$", multi_view, name="multi_view"),
        path(r"new_service/(?P<service_name>\w+)/$", new_service, name="new_service"),
        path(r"new_service/(?P<service_name>\w+)$", new_service, name="new_service"),
        path(r"rasp/(?P<endpoint>\w+)/$", new_service, name="rasp"),
        path(r"rasp/(?P<endpoint>\w+)$", new_service, name="rasp"),
    ]
