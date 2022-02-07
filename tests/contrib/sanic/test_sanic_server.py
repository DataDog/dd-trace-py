import asyncio
import random

import httpx
import pytest
from sanic import Sanic
from sanic.config import DEFAULT_CONFIG
from sanic.response import json


# Handle naming of asynchronous client in older httpx versions used in sanic 19.12
httpx_client = getattr(httpx, "AsyncClient", getattr(httpx, "Client"))


@pytest.fixture
def app(tracer):
    app = Sanic(__name__)

    @tracer.wrap()
    async def random_sleep():
        await asyncio.sleep(random.random())

    @app.route("/hello")
    async def hello(request):
        await random_sleep()
        return json({"hello": "world"})

    @app.route("/internal_error")
    async def internal_error(request):
        1 / 0

    yield app


@pytest.fixture
async def sanic_http_server(app, unused_port, loop):
    """Fixture for using sanic async HTTP server rather than a asgi async server used by test client"""
    DEFAULT_CONFIG["REGISTER"] = False
    server = await app.create_server(debug=True, host="0.0.0.0", port=unused_port, return_asyncio_server=True)
    yield server
    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
async def test_multiple_requests_sanic_http(tracer, sanic_http_server, unused_port):
    url = "http://0.0.0.0:{}/hello".format(unused_port)
    async with httpx_client() as client:
        responses = await asyncio.gather(
            client.get(url),
            client.get(url),
        )

    assert len(responses) == 2
    assert [r.status_code for r in responses] == [200] * 2
    assert [r.json() for r in responses] == [{"hello": "world"}] * 2

    spans = tracer.pop_traces()
    assert len(spans) == 2
    assert len(spans[0]) == 2
    assert len(spans[1]) == 2

    assert spans[0][0].name == "sanic.request"
    assert spans[0][1].name == "tests.contrib.sanic.test_sanic_server.random_sleep"
    assert spans[0][0].parent_id is None
    assert spans[0][1].parent_id == spans[0][0].span_id
    assert spans[0][0].get_tag("http.status_code") == "200"

    assert spans[1][0].name == "sanic.request"
    assert spans[1][1].name == "tests.contrib.sanic.test_sanic_server.random_sleep"
    assert spans[1][0].parent_id is None
    assert spans[1][1].parent_id == spans[1][0].span_id
    assert spans[1][0].get_tag("http.status_code") == "200"


@pytest.mark.asyncio
async def test_sanic_errors(tracer, sanic_http_server, unused_port):
    url = "http://0.0.0.0:{}/not_found".format(unused_port)
    async with httpx_client() as client:
        response = await client.get(url)

    assert response.status_code == 404
    spans = tracer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    assert spans[0][0].name == "sanic.request"
    assert spans[0][0].get_tag("http.status_code") == "404"
    assert spans[0][0].error == 0

    url = "http://0.0.0.0:{}/internal_error".format(unused_port)
    async with httpx_client() as client:
        response = await client.get(url)

    assert response.status_code == 500
    spans = tracer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    assert spans[0][0].name == "sanic.request"
    assert spans[0][0].get_tag("http.status_code") == "500"
    assert spans[0][0].error == 1
