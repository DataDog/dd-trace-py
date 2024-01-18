import os

import django
from django.conf import settings
import pytest

from ddtrace.contrib.django import patch
from tests.appsec.contrib_appsec import utils


class Test_Django(utils.Contrib_TestClass_For_Threats):
    SERVER_PORT = 8000

    @pytest.fixture
    def interface(self):
        from django.test.client import Client

        client = Client("http://localhost:%d" % self.SERVER_PORT)
        yield utils.Interface("fastapi", django, client)

    def setup_class(cls):
        os.environ["DJANGO_SETTINGS_MODULE"] = "tests.contrib.django.django_app.settings"
        settings.DEBUG = False
        patch()
        django.setup()
