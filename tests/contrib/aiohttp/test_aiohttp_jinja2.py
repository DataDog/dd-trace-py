import sys

import aiohttp_jinja2
import pytest

from ddtrace import Pin
from ddtrace import tracer

from .app.web import set_filesystem_loader
from .app.web import set_package_loader


async def test_template_rendering(untraced_app_tracer, aiohttp_client):
    app, tracer = untraced_app_tracer
    client = await aiohttp_client(app)
    # it should trace a template rendering
    request = await client.request("GET", "/template/")
    assert 200 == request.status
    text = await request.text()
    assert "OK" == text
    # the trace is created
    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # with the right fields
    assert "aiohttp.template" == span.name
    assert "template" == span.span_type
    assert "/template.jinja2" == span.get_tag("aiohttp.template")
    assert 0 == span.error


async def test_template_rendering_snapshot(untraced_app_tracer, aiohttp_client, snapshot_context):
    app, _ = untraced_app_tracer
    Pin.override(aiohttp_jinja2, tracer=tracer)
    with snapshot_context():
        client = await aiohttp_client(app)
        # it should trace a template rendering
        request = await client.request("GET", "/template/")
        assert 200 == request.status


async def test_template_rendering_filesystem(untraced_app_tracer, aiohttp_client, loop):
    app, tracer = untraced_app_tracer
    client = await aiohttp_client(app)
    # it should trace a template rendering with a FileSystemLoader
    set_filesystem_loader(app)
    request = await client.request("GET", "/template/")
    assert 200 == request.status
    text = await request.text()
    assert "OK" == text
    # the trace is created
    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # with the right fields
    assert "aiohttp.template" == span.name
    assert "template" == span.span_type
    assert "/template.jinja2" == span.get_tag("aiohttp.template")
    assert 0 == span.error


@pytest.mark.skipif(sys.version_info < (3, 6), reason="Not compatible with Python 3.5")
async def test_template_rendering_package(untraced_app_tracer, aiohttp_client, loop):
    app, tracer = untraced_app_tracer
    client = await aiohttp_client(app)
    # it should trace a template rendering with a PackageLoader
    set_package_loader(app)
    request = await client.request("GET", "/template/")
    assert 200 == request.status
    text = await request.text()
    assert "OK" == text
    # the trace is created
    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # with the right fields
    assert "aiohttp.template" == span.name
    assert "template" == span.span_type
    assert "templates/template.jinja2" == span.get_tag("aiohttp.template")
    assert 0 == span.error


async def test_template_decorator(untraced_app_tracer, aiohttp_client, loop):
    app, tracer = untraced_app_tracer
    client = await aiohttp_client(app)
    # it should trace a template rendering
    request = await client.request("GET", "/template_decorator/")
    assert 200 == request.status
    text = await request.text()
    assert "OK" == text
    # the trace is created
    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # with the right fields
    assert "aiohttp.template" == span.name
    assert "template" == span.span_type
    assert "/template.jinja2" == span.get_tag("aiohttp.template")
    assert 0 == span.error


async def test_template_error(untraced_app_tracer, aiohttp_client, loop):
    app, tracer = untraced_app_tracer
    client = await aiohttp_client(app)
    # it should trace a template rendering
    request = await client.request("GET", "/template_error/")
    assert 500 == request.status
    await request.text()
    # the trace is created
    traces = tracer.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # with the right fields
    assert "aiohttp.template" == span.name
    assert "template" == span.span_type
    assert "/error.jinja2" == span.get_tag("aiohttp.template")
    assert 1 == span.error
    assert "division by zero" == span.get_tag("error.msg")
    assert "ZeroDivisionError: division by zero" in span.get_tag("error.stack")
