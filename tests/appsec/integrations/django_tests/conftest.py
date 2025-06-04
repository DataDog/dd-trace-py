import os

import django
from django.conf import settings
import pytest

from ddtrace.appsec._iast import enable_iast_propagation
from ddtrace.contrib.internal.django.patch import patch
from ddtrace.trace import Pin
from tests.appsec.iast.conftest import _end_iast_context_and_oce
from tests.appsec.iast.conftest import _start_iast_context_and_oce
from tests.utils import DummyTracer
from tests.utils import TracerSpanContainer
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
    ):
        settings.DEBUG = False
        enable_iast_propagation()
        patch()
        django.setup()


@pytest.fixture
def tracer():
    tracer = DummyTracer()
    # Patch Django and override tracer to be our test tracer
    pin = Pin.get_from(django)
    original_tracer = pin.tracer
    Pin.override(django, tracer=tracer)

    # Yield to our test
    yield tracer
    tracer.pop()

    # Reset the tracer pinned to Django and unpatch
    # DEV: unable to properly unpatch and reload django app with each test
    # unpatch()
    Pin.override(django, tracer=original_tracer)


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


@pytest.fixture
def iast_spans_with_zero_sampling(tracer):
    """Fixture that provides a span container with IAST enabled but 0% sampling rate.

    This fixture is used to test IAST behavior when sampling is disabled (0%).
    It sets up a test environment where:
    - IAST is enabled
    - Deduplication is disabled
    - Sampling rate is set to 0%
    - A span container is provided for collecting traces

    Args:
        tracer: The test tracer instance

    Yields:
        TracerSpanContainer: A container for collecting spans during the test
    """
    with override_global_config(
        dict(
            _iast_enabled=True,
            _iast_deduplication_enabled=False,
            _iast_request_sampling=0.0,
        )
    ):
        container = TracerSpanContainer(tracer)
        _start_iast_context_and_oce()
        yield container
        _end_iast_context_and_oce()
        container.reset()
