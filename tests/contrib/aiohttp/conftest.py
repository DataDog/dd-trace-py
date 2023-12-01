import aiohttp  # noqa:F401
import pytest

from ddtrace.contrib.aiohttp.middlewares import trace_app
from ddtrace.contrib.aiohttp.patch import unpatch
from ddtrace.internal.utils import version  # noqa:F401

from .app.web import setup_app


@pytest.fixture
async def app_tracer(tracer, loop):
    app = setup_app()
    trace_app(app, tracer)
    return app, tracer


@pytest.fixture
async def patched_app_tracer(app_tracer):
    app, tracer = app_tracer
    yield app, tracer
    unpatch()


@pytest.fixture
async def untraced_app_tracer(tracer, loop):
    app = setup_app()
    yield app, tracer
    unpatch()
