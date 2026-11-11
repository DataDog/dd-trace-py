import asyncio

import aiohttp  # noqa:F401
import pytest
import pytest_asyncio

from ddtrace.contrib.internal.aiohttp.middlewares import trace_app
from ddtrace.contrib.internal.aiohttp.patch import unpatch
from ddtrace.internal import core
from ddtrace.internal.utils import version  # noqa:F401
from ddtrace.internal.utils.version import parse_version

from .app.web import setup_app


PYTEST_ASYNCIO_VERSION = parse_version(pytest_asyncio.__version__)


if PYTEST_ASYNCIO_VERSION < (1, 0):

    @pytest.fixture
    async def app(loop):
        app = setup_app()
        trace_app(app)
        return app

    @pytest.fixture
    async def untraced_app(loop):
        app = setup_app()
        yield app
        unpatch()

else:

    @pytest.fixture
    async def app():
        app = setup_app()
        trace_app(app)
        return app

    @pytest.fixture
    async def untraced_app():
        app = setup_app()
        yield app
        unpatch()


@pytest.fixture
async def patched_app(app):
    yield app
    unpatch()


@pytest.fixture
def request_completed():
    completed = asyncio.Queue()

    def ended(ctx, exc_info):
        completed.put_nowait(ctx)

    async def wait():
        return await asyncio.wait_for(completed.get(), timeout=5)

    core.on("context.ended.web.request", ended)
    try:
        yield wait
    finally:
        core.reset_listeners("context.ended.web.request", ended)
