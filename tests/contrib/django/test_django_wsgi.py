import logging
import os
import subprocess

import django
from django.core.signals import request_finished
from django.core.wsgi import get_wsgi_application
from django.dispatch import receiver
from django.http import HttpResponse
from django.urls import path
import pytest

from ddtrace.contrib.wsgi import DDWSGIMiddleware
from tests.webclient import Client


filepath, extension = os.path.splitext(__file__)
ROOT_URLCONF = os.path.basename(filepath)
WSGI_APPLICATION = os.path.basename(filepath) + ".app"
DEBUG = True
SERVER_PORT = 8000
SENTINEL_LOG = "request finished signal received"

log = logging.getLogger(__name__)


@receiver(request_finished)
def log_request_finished(*_, **__):
    log.warning(SENTINEL_LOG)


def handler(_):
    return HttpResponse("Hello!")


urlpatterns = [path("", handler)]
# it would be better to check for app_is_iterator programmatically, but Django WSGI apps behave like
# iterators for the purpose of DDWSGIMiddleware despite not having both "__next__" and "__iter__" methods
app = DDWSGIMiddleware(get_wsgi_application(), app_is_iterator=True)


@pytest.mark.skipif(django.VERSION < (3, 0, 0), reason="Older Django versions don't work with this use of django-admin")
def test_django_app_receives_request_finished_signal_when_app_is_ddwsgimiddleware():
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": os.path.dirname(os.path.abspath(__file__)) + ":" + env["PYTHONPATH"],
            "DJANGO_SETTINGS_MODULE": "test_django_wsgi",
        }
    )
    cmd = ["django-admin", "runserver", "--noreload", str(SERVER_PORT)]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        env=env,
    )

    client = Client("http://localhost:%d" % SERVER_PORT)
    client.wait()
    output = ""
    try:
        assert client.get("/").status_code == 200
    finally:
        try:
            _, output = proc.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, output = proc.communicate()
    assert SENTINEL_LOG in str(output)
