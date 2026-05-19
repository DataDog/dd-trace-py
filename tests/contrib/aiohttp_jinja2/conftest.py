import pytest

from ddtrace.contrib.internal.aiohttp_jinja2.patch import patch
from ddtrace.contrib.internal.aiohttp_jinja2.patch import unpatch
from tests.contrib.aiohttp.conftest import app  # noqa:F401
from tests.contrib.aiohttp.conftest import patched_app  # noqa:F401
from tests.contrib.aiohttp.conftest import untraced_app  # noqa:F401


@pytest.fixture
def patched_app_jinja(patched_app):  # noqa: F811
    patch()
    yield patched_app
    unpatch()


@pytest.fixture
def untraced_app_jinja(untraced_app):  # noqa: F811
    patch()
    yield untraced_app
    unpatch()
