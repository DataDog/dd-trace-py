import aiohttp_jinja2
import pytest

from ddtrace.contrib.aiohttp_jinja2 import patch
from ddtrace.contrib.aiohttp_jinja2 import unpatch
from ddtrace.pin import Pin
from tests.contrib.aiohttp.conftest import app_tracer  # noqa
from tests.contrib.aiohttp.conftest import patched_app_tracer  # noqa
from tests.contrib.aiohttp.conftest import untraced_app_tracer  # noqa


@pytest.fixture
def patched_app_tracer_jinja(patched_app_tracer):  # noqa: F811
    app, tracer = patched_app_tracer
    patch()
    Pin.override(aiohttp_jinja2, tracer=tracer)
    yield app, tracer
    unpatch()


@pytest.fixture
def untraced_app_tracer_jinja(untraced_app_tracer):  # noqa: F811
    patch()
    app, tracer = untraced_app_tracer
    Pin.override(aiohttp_jinja2, tracer=tracer)
    yield app, tracer
    unpatch()
