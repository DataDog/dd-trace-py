__all__ = ["pytest_configure"]


import django
from django.conf import settings

from ddtrace.contrib.internal.django.patch import patch
from ddtrace.internal.settings import env


# We manually designate which settings we will be using in an environment variable
# This is similar to what occurs in the `manage.py`
env["DJANGO_SETTINGS_MODULE"] = "tests.contrib.djangorestframework.app.settings"


# `pytest` automatically calls this function once when tests are run.
def pytest_configure():
    settings.DEBUG = False
    patch()
    django.setup()
