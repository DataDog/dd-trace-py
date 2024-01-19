import tempfile

import django
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.http import FileResponse
from django.http import HttpResponse

from ddtrace import tracer


# django.conf.urls.url was deprecated in django 3 and removed in django 4
if django.VERSION < (4, 0, 0):
    from django.conf.urls import url as handler
else:
    from django.urls import re_path as handler


def healthcheck(request):
    return HttpResponse("ok ASM", status=200)


def path_view(request):
    return HttpResponse(status=200)


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
]
