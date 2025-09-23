import aiohttp  # noqa:F401
import pytest
import pytest_asyncio

from ddtrace.contrib.internal.aiohttp.middlewares import trace_app
from ddtrace.contrib.internal.aiohttp.patch import unpatch
from ddtrace.internal.utils import version  # noqa:F401
from ddtrace.internal.utils.version import parse_version

from .app.web import setup_app


PYTEST_ASYNCIO_VERSION = parse_version(pytest_asyncio.__version__)


if PYTEST_ASYNCIO_VERSION < (1, 0):

    @pytest.fixture
    async def app_tracer(tracer, loop):
        app = setup_app()
        trace_app(app, tracer)
        return app, tracer

    @pytest.fixture
    async def untraced_app_tracer(tracer, loop):
        app = setup_app()
        yield app, tracer
        unpatch()

else:

    @pytest.fixture
    async def app_tracer(tracer):
        app = setup_app()
        trace_app(app, tracer)
        return app, tracer

    @pytest.fixture
    async def untraced_app_tracer(tracer):
        app = setup_app()
        yield app, tracer
        unpatch()


@pytest.fixture
async def patched_app_tracer(app_tracer):
    app, tracer = app_tracer
    yield app, tracer
    unpatch()
