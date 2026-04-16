import os

import django
from django.conf import settings
from django.test.client import Client
import pytest

from ddtrace.propagation._utils import get_wsgi_header
from tests.appsec.contrib_appsec import utils
from tests.utils import scoped_tracer


class _Test_Django_Base:
    """Django-specific interface, response accessors, and argument parsing."""

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
        interface.SERVER_PORT = self.SERVER_PORT
        interface.version = django.VERSION
        with scoped_tracer() as tracer:
            interface.tracer = tracer
            interface.printer = printer
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


class Test_Django(_Test_Django_Base, utils.Contrib_TestClass_For_Threats):
    ENDPOINT_DISCOVERY_EXPECTED_PATHS = {
        "^$",
        "asm/<int:param_int>/<str:param_str>",
        "^asm/?$",
        "new_service/<str:service_name>",
        "login",
        "login_sdk",
        "rasp/<str:endpoint>",
    }

    @staticmethod
    def endpoint_path_to_uri(path: str) -> str:
        import re

        # Django regex-style routes: ^asm/?$ → /asm
        if re.match(r"^\^.*\$$", path):
            path = path[1:-1]
        if path.endswith("/?"):
            path = path[:-2]
        path = re.sub(r"<int:[a-z_]+>", "123", path)
        path = re.sub(r"<str:[a-z_]+>", "abczx", path)
        return path if path.startswith("/") else ("/" + path)


class Test_Django_RC(_Test_Django_Base, utils.Contrib_TestClass_For_Threats_RC):
    pass
