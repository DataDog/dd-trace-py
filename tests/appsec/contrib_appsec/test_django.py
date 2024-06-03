import os

import django
from django.conf import settings
from django.test.client import Client
import pytest

from ddtrace.propagation._utils import get_wsgi_header
from tests.appsec.contrib_appsec import utils


class Test_Django(utils.Contrib_TestClass_For_Threats):
    @pytest.fixture
    def interface(self, printer):
        os.environ["DJANGO_SETTINGS_MODULE"] = "tests.appsec.contrib_appsec.django_app.settings"
        settings.DEBUG = False
        django.setup()
        client = Client(
            f"http://localhost:{self.SERVER_PORT}",
            SERVER_NAME=f"localhost:{self.SERVER_PORT}",
        )
        initial_get = client.get

        def patch_get(*args, **kwargs):
            headers = {}
            if "cookies" in kwargs:
                client.cookies.load(kwargs["cookies"])
                kwargs.pop("cookies")
            if "headers" in kwargs:
                headers = kwargs["headers"]
                kwargs.pop("headers")
            # https://docs.djangoproject.com/en/5.0/ref/request-response/#:~:text=With%20the%20exception%20of%20CONTENT_LENGTH,HTTP_%20prefix%20to%20the%20name
            # test client does not add HTTP_ prefix to headers like a real Django server would
            meta_headers = {}
            for k, v in headers.items():
                meta_headers[get_wsgi_header(k)] = v
            return initial_get(*args, **kwargs, **meta_headers)

        client.get = patch_get

        initial_post = client.post

        def patch_post(*args, **kwargs):
            headers = {}
            if "cookies" in kwargs:
                client.cookies.load(kwargs["cookies"])
                kwargs.pop("cookies")
            if "headers" in kwargs:
                headers = kwargs["headers"]
                kwargs.pop("headers")
            # https://docs.djangoproject.com/en/5.0/ref/request-response/#:~:text=With%20the%20exception%20of%20CONTENT_LENGTH,HTTP_%20prefix%20to%20the%20name
            # test client does not add HTTP_ prefix to headers like a real Django server would
            meta_headers = {}
            for k, v in headers.items():
                meta_headers[get_wsgi_header(k)] = v
            return initial_post(*args, **kwargs, **meta_headers)

        client.post = patch_post

        interface = utils.Interface("django", django, client)
        interface.version = django.VERSION
        with utils.test_tracer() as tracer:
            interface.tracer = tracer
            interface.printer = printer
            with utils.post_tracer(interface):
                yield interface

    def status(self, response):
        return response.status_code

    def headers(self, response):
        if django.VERSION >= (3, 0, 0):
            return response.headers
        # Django 2.x
        return {k: v[1] for k, v in response._headers.items()}

    def body(self, response):
        return response.content.decode("utf-8")

    def location(self, response):
        return response["location"]
