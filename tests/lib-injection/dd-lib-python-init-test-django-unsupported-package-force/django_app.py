import os

from django.http import HttpResponse
from django.urls import path


filepath, extension = os.path.splitext(__file__)
ROOT_URLCONF = os.path.basename(filepath)
DEBUG = False
SECRET_KEY = "fdsfdasfa"
ALLOWED_HOSTS = ["*"]


def index(request):
    import ddtrace

    if ddtrace.__version__ != "2.6.3":
        print(
            "Assertion failure: unexpected ddtrace version received. Got %r when expecting '2.6.3'"
            % ddtrace.__version__
        )
        # Hard exit so traces aren't flushed and the test will fail.
        os._exit(1)

    return HttpResponse("test")


urlpatterns = [
    path("", index),
]
