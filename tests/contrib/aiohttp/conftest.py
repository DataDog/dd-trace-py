import aiohttp
import pytest

from ddtrace.contrib.aiohttp.middlewares import trace_app
from ddtrace.internal.utils import version

from .app.web import setup_app


if version.parse_version(aiohttp.__version__) < (3, 0, 0):

    @pytest.fixture
    def aiohttp_client(test_client):
        return test_client

    @pytest.fixture
    def app_tracer(tracer, loop):
        app = setup_app()
        trace_app(app, tracer)
        return app, tracer

    @pytest.fixture
    def patched_app_tracer(app_tracer):
        app, tracer = app_tracer
        return app, tracer
        # When Python 3.5 is dropped, rather do:
        # yield app, tracer
        # unpatch()

    @pytest.fixture
    def untraced_app_tracer(tracer, loop):
        app = setup_app()
        return app, tracer
        # When Python 3.5 is dropped, rather do:
        # yield app, tracer
        # unpatch()


else:

    @pytest.fixture
    async def app_tracer(tracer, loop):
        app = setup_app()
        trace_app(app, tracer)
        return app, tracer

    @pytest.fixture
    async def patched_app_tracer(app_tracer):
        app, tracer = app_tracer
        return app, tracer
        # When Python 3.5 is dropped, rather do:
        # yield app, tracer
        # unpatch()

    @pytest.fixture
    async def untraced_app_tracer(tracer, loop):
        app = setup_app()
        return app, tracer
        # When Python 3.5 is dropped, rather do:
        # yield app, tracer
        # unpatch()
