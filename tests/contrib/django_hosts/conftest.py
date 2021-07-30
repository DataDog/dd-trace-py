import os

import django
from django.conf import settings

from ddtrace.contrib.django import patch


# We manually designate which settings we will be using in an environment variable
# This is similar to what occurs in the `manage.py`
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.contrib.django_hosts.django_app.settings")


# `pytest` automatically calls this function once when tests are run.
def pytest_configure():
    settings.DEBUG = False
    patch()
    django.setup()
