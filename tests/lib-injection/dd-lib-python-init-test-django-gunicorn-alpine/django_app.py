import os

from django.core.wsgi import get_wsgi_application
from django.http import HttpResponse
from django.urls import path


filepath, extension = os.path.splitext(__file__)
ROOT_URLCONF = os.path.basename(filepath)
DEBUG = False
SECRET_KEY = "fdsfdasfa"
ALLOWED_HOSTS = ["*"]


def index(request):
    return HttpResponse("test")


urlpatterns = [
    path("", index),
]

application = get_wsgi_application()
