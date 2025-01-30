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

from ddtrace.contrib.internal.wsgi.wsgi import DDWSGIMiddleware
from ddtrace.internal.compat import PYTHON_VERSION_INFO
from tests.contrib.django.utils import make_soap_request
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

# this import fails for python 3.12
if PYTHON_VERSION_INFO < (3, 12):
    from tests.contrib.django.soap.services import leave_status_service

    urlpatterns.append(path("soap/", leave_status_service, name="soap_account"))


# it would be better to check for app_is_iterator programmatically, but Django WSGI apps behave like
# iterators for the purpose of DDWSGIMiddleware despite not having both "__next__" and "__iter__" methods
app = DDWSGIMiddleware(get_wsgi_application(), app_is_iterator=True)


@pytest.fixture()
def wsgi_app():
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": os.path.dirname(os.path.abspath(__file__)) + ":" + env["PYTHONPATH"],
            "DJANGO_SETTINGS_MODULE": "test_django_wsgi",
            "DD_TRACE_ENABLED": "true",
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

    yield proc

    try:
        proc.terminate()
        proc.wait(timeout=5)  # Wait up to 5 seconds for the process to terminate
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


@pytest.mark.skipif(django.VERSION < (3, 0, 0), reason="Older Django versions don't work with this use of django-admin")
def test_django_app_receives_request_finished_signal_when_app_is_ddwsgimiddleware(wsgi_app):
    client = Client("http://localhost:%d" % SERVER_PORT)
    client.wait()
    output = ""
    try:
        assert client.get("/").status_code == 200
    finally:
        try:
            _, output = wsgi_app.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            wsgi_app.kill()
            _, output = wsgi_app.communicate()
    assert SENTINEL_LOG in str(output)


@pytest.mark.skipif(PYTHON_VERSION_INFO >= (3, 12), reason="A Spyne import fails when using Python 3.12")
def test_django_wsgi_soap_app_works(wsgi_app):
    client = Client("http://localhost:%d" % SERVER_PORT)
    client.wait()

    url = "http://localhost:%d" % SERVER_PORT + "/soap/?wsdl"
    response = make_soap_request(url)

    assert response["success"] is True
