import os

import django
from django.conf import settings
import pytest

from ddtrace.appsec._iast import enable_iast_propagation
from ddtrace.appsec._iast._patch_modules import patch_iast
from ddtrace.contrib.internal.django.patch import patch as django_patch
from ddtrace.trace import Pin
from tests.appsec.iast.conftest import _end_iast_context_and_oce
from tests.appsec.iast.conftest import _start_iast_context_and_oce
from tests.utils import DummyTracer
from tests.utils import TracerSpanContainer
from tests.utils import override_env
from tests.utils import override_global_config


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.appsec.integrations.django_tests.django_app.settings")


# `pytest` automatically calls this function once when tests are run.
def pytest_configure():
    with override_global_config(
        dict(
            _iast_enabled=True,
            _iast_deduplication_enabled=False,
            _iast_request_sampling=100.0,
        )
    ), override_env(dict(_DD_IAST_PATCH_MODULES="tests.appsec.integrations")):
        settings.DEBUG = False
        patch_iast()
        django_patch()
        enable_iast_propagation()
        django.setup()


@pytest.fixture
def debug_mode():
    from django.conf import settings

    original_debug = settings.DEBUG
    settings.DEBUG = True
    yield
    settings.DEBUG = original_debug


@pytest.fixture
def tracer():
    tracer = DummyTracer()
    # Patch Django and override tracer to be our test tracer
    pin = Pin.get_from(django)
    original_tracer = pin.tracer
    Pin._override(django, tracer=tracer)

    # Yield to our test
    yield tracer
    tracer.pop()

    # Reset the tracer pinned to Django and unpatch
    # DEV: unable to properly unpatch and reload django app with each test
    # unpatch()
    Pin._override(django, tracer=original_tracer)


@pytest.fixture
def test_spans(tracer):
    with override_global_config(
        dict(
            _iast_enabled=True,
            _iast_deduplication_enabled=False,
            _iast_request_sampling=100.0,
        )
    ):
        container = TracerSpanContainer(tracer)
        _start_iast_context_and_oce()
        yield container
        _end_iast_context_and_oce()
        container.reset()
