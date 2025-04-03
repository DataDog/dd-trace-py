import aiohttp_jinja2
import pytest

from ddtrace.contrib.internal.aiohttp_jinja2.patch import patch
from ddtrace.contrib.internal.aiohttp_jinja2.patch import unpatch
from ddtrace.trace import Pin
from tests.contrib.aiohttp.conftest import app_tracer  # noqa:F401
from tests.contrib.aiohttp.conftest import patched_app_tracer  # noqa:F401
from tests.contrib.aiohttp.conftest import untraced_app_tracer  # noqa:F401


@pytest.fixture
def patched_app_tracer_jinja(patched_app_tracer):  # noqa: F811
    app, tracer = patched_app_tracer
    patch()
    Pin._override(aiohttp_jinja2, tracer=tracer)
    yield app, tracer
    unpatch()


@pytest.fixture
def untraced_app_tracer_jinja(untraced_app_tracer):  # noqa: F811
    patch()
    app, tracer = untraced_app_tracer
    Pin._override(aiohttp_jinja2, tracer=tracer)
    yield app, tracer
    unpatch()
