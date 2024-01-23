import tempfile

import django
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.http import FileResponse
from django.http import HttpResponse
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from ddtrace import tracer


# django.conf.urls.url was deprecated in django 3 and removed in django 4
if django.VERSION < (4, 0, 0):
    from django.conf.urls import url as handler
else:
    from django.urls import re_path as handler

if django.VERSION >= (2, 0, 0):
    from django.urls import path
else:
    from django.conf.urls import url as path


def healthcheck(request):
    return HttpResponse("ok ASM", status=200)


@csrf_exempt
def multi_view(request, param_int=0, param_str=""):
    query_params = request.GET.dict()
    body = {
        "path_params": {"param_int": param_int, "param_str": param_str},
        "query_params": query_params,
        "headers": dict(request.headers),
        "cookies": dict(request.COOKIES),
        "body": request.body.decode("utf-8"),
        "method": request.method,
    }
    status = int(query_params.get("status", "200"))
    return JsonResponse(body, status=status)


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
    ]
else:
    urlpatterns += [
        path(r"asm/(?P<param_int>[0-9]{4})/(?P<param_str>\w+)/$", multi_view, name="multi_view"),
    ]
