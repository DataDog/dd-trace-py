"""
Ensure that if the ``AsyncioTracer`` is not properly configured,
bad traces are produced but the ``Context`` object will not
leak memory.
"""
import asyncio
import threading
from urllib import request

from tests.utils import assert_is_measured


async def test_full_request(patched_app_tracer, aiohttp_client, loop):
    app, tracer = patched_app_tracer
    client = await aiohttp_client(app)
    # it should create a root span when there is a handler hit
    # with the proper tags
    request = await client.request("GET", "/")
    assert 200 == request.status
    await request.text()
    # the trace is created
    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    request_span = traces[0][0]
    # request
    assert_is_measured(request_span)
    assert "aiohttp-web" == request_span.service
    assert "aiohttp.request" == request_span.name
    assert "GET /" == request_span.resource
    assert request_span.get_tag("span.kind") == "server"


async def test_multiple_full_request(patched_app_tracer, aiohttp_client, loop):
    NUMBER_REQUESTS = 10
    responses = []

    app, tracer = patched_app_tracer
    client = await aiohttp_client(app)

    # it should produce a wrong trace, but the Context must
    # be finished
    def make_requests():
        url = client.make_url("/delayed/")
        response = request.urlopen(str(url)).read().decode("utf-8")
        responses.append(response)

    # blocking call executed in different threads
    threads = [threading.Thread(target=make_requests) for _ in range(NUMBER_REQUESTS)]
    for t in threads:
        t.start()

    # yield back to the event loop until all requests are processed
    while len(responses) < NUMBER_REQUESTS:
        await asyncio.sleep(0.001)

    for response in responses:
        assert "Done" == response

    for t in threads:
        t.join()

    # the trace is wrong but the spans are finished and written
    spans = tracer.pop()
    assert NUMBER_REQUESTS == len(spans)
