import aiohttp_jinja2
import pytest

from ddtrace.constants import ERROR_MSG
from tests.contrib.aiohttp.app.web import set_filesystem_loader
from tests.contrib.aiohttp.app.web import set_package_loader
import tests.contrib.aiohttp.conftest  # noqa:F401


VERSION = tuple(map(int, aiohttp_jinja2.__version__.split(".")))


async def test_template_rendering(untraced_app_jinja, test_spans, aiohttp_client):
    client = await aiohttp_client(untraced_app_jinja)
    # it should trace a template rendering
    request = await client.request("GET", "/template/")
    assert 200 == request.status
    text = await request.text()
    assert "OK" == text
    # the trace is created
    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # with the right fields
    assert "aiohttp.template" == span.name
    assert "template" == span.span_type
    assert "/template.jinja2" == span.get_tag("aiohttp.template")
    assert "aiohttp_jinja2" == span.get_tag("component")
    assert 0 == span.error


async def test_template_rendering_snapshot(untraced_app_jinja, aiohttp_client, snapshot_context):
    with snapshot_context():
        client = await aiohttp_client(untraced_app_jinja)
        # it should trace a template rendering
        request = await client.request("GET", "/template/")
        assert 200 == request.status


async def test_template_rendering_snapshot_patched_server(
    patched_app_jinja,
    aiohttp_client,
    snapshot_context,
):
    # Ignore meta.http.url tag as the port is not fixed on the server
    with snapshot_context(ignores=["meta.http.url", "meta.http.useragent"]):
        client = await aiohttp_client(patched_app_jinja)
        # it should trace a template rendering
        request = await client.request("GET", "/template/")
        assert 200 == request.status


async def test_template_rendering_filesystem(untraced_app_jinja, test_spans, aiohttp_client):
    client = await aiohttp_client(untraced_app_jinja)
    # it should trace a template rendering with a FileSystemLoader
    set_filesystem_loader(untraced_app_jinja)
    request = await client.request("GET", "/template/")
    assert 200 == request.status
    text = await request.text()
    assert "OK" == text
    # the trace is created
    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # with the right fields
    assert "aiohttp.template" == span.name
    assert "template" == span.span_type
    assert "/template.jinja2" == span.get_tag("aiohttp.template")
    assert "aiohttp_jinja2" == span.get_tag("component")
    assert 0 == span.error


@pytest.mark.skipif(VERSION < (1, 5, 0), reason="Package loader doesn't work in older versions")
async def test_template_rendering_package(untraced_app_jinja, test_spans, aiohttp_client):
    client = await aiohttp_client(untraced_app_jinja)
    # it should trace a template rendering with a PackageLoader
    set_package_loader(untraced_app_jinja)
    request = await client.request("GET", "/template/")
    assert 200 == request.status
    text = await request.text()
    assert "OK" == text
    # the trace is created
    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # with the right fields
    assert "aiohttp.template" == span.name
    assert "template" == span.span_type
    assert "templates/template.jinja2" == span.get_tag("aiohttp.template")
    assert "aiohttp_jinja2" == span.get_tag("component")
    assert 0 == span.error


async def test_template_decorator(untraced_app_jinja, test_spans, aiohttp_client, loop=None):
    client = await aiohttp_client(untraced_app_jinja)
    # it should trace a template rendering
    request = await client.request("GET", "/template_decorator/")
    assert 200 == request.status
    text = await request.text()
    assert "OK" == text
    # the trace is created
    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # with the right fields
    assert "aiohttp.template" == span.name
    assert "template" == span.span_type
    assert "/template.jinja2" == span.get_tag("aiohttp.template")
    assert "aiohttp_jinja2" == span.get_tag("component")
    assert 0 == span.error


async def test_template_error(untraced_app_jinja, test_spans, aiohttp_client):
    client = await aiohttp_client(untraced_app_jinja)
    # it should trace a template rendering
    request = await client.request("GET", "/template_error/")
    assert 500 == request.status
    await request.text()
    # the trace is created
    traces = test_spans.pop_traces()
    assert 1 == len(traces)
    assert 1 == len(traces[0])
    span = traces[0][0]
    # with the right fields
    assert "aiohttp.template" == span.name
    assert "template" == span.span_type
    assert "/error.jinja2" == span.get_tag("aiohttp.template")
    assert "aiohttp_jinja2" == span.get_tag("component")
    assert 1 == span.error
    assert "division by zero" == span.get_tag(ERROR_MSG)
    assert "ZeroDivisionError: division by zero" in span.get_tag("error.stack")
