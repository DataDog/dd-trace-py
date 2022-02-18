import asyncio
import os
import random

from sanic import Sanic
from sanic.response import json

from ddtrace import tracer
from tests.webclient import PingFilter


tracer.configure(
    settings={
        "FILTERS": [PingFilter()],
    }
)


app = Sanic("test_sanic_server")


@tracer.wrap()
async def random_sleep():
    await asyncio.sleep(random.random() * 0.1)


@app.route("/hello")
async def hello(request):
    await random_sleep()
    return json({"hello": "world"})


@app.route("/internal_error")
async def internal_error(request):
    1 / 0


@app.route("/shutdown-tracer")
async def shutdown_tracer(request):
    tracer.shutdown()
    return json({"success": True})


app.run(host="0.0.0.0", port=os.environ["SANIC_PORT"], access_log=False)
