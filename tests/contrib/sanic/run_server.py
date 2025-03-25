import asyncio
import os
import random

from sanic import Sanic
from sanic.response import json

from ddtrace.trace import tracer
from tests.webclient import PingFilter


tracer.configure(trace_processors=[PingFilter()])


app = Sanic("test_sanic_server")


# Depending on the version of sanic the application can be run in a child process.
# This can alter the name of the function and the default span name. Setting the span name to
# `random_sleep` ensures the snapshot tests produce consistent spans across sanic versions.
@tracer.wrap("random_sleep")
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ["SANIC_PORT"]), access_log=False)
