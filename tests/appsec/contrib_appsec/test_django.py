import os

import django
from django.conf import settings
from django.test.client import Client
import pytest

from ddtrace.contrib.django import patch
from tests.appsec.contrib_appsec import utils


class Test_Django(utils.Contrib_TestClass_For_Threats):
    @pytest.fixture
    def interface(self):
        os.environ["DJANGO_SETTINGS_MODULE"] = "tests.appsec.contrib_appsec.django_app.settings"
        settings.DEBUG = False
        django.setup()
        patch()
        client = Client("http://localhost:%d" % self.SERVER_PORT)
        interface = utils.Interface("django", django, client)
        with utils.test_tracer() as tracer:
            interface.tracer = tracer
            with utils.post_tracer(interface):
                yield interface
        # unpatch failing in this case
        # unpatch()

    def status(self, response):
        return response.status_code

    def headers(self, response):
        if django.VERSION >= (3, 0, 0):
            return response.headers
        # Django 2.x
        return {k: v[1] for k, v in response._headers.items()}

    def body(self, response):
        return response.content.decode("utf-8")
