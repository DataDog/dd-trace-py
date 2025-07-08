# -*- encoding: utf-8 -*-
import os
import uuid

import pytest
from wrapt import ObjectProxy
import yaaredis

from ddtrace.contrib.internal.yaaredis.patch import patch
from ddtrace.contrib.internal.yaaredis.patch import unpatch
from ddtrace.trace import Pin
from tests.opentracer.utils import init_tracer
from tests.utils import override_config

from ..config import REDIS_CONFIG


@pytest.fixture(autouse=True)
async def traced_yaaredis():
    r = yaaredis.StrictRedis(port=REDIS_CONFIG["port"])
    await r.flushall()

    patch()
    try:
        yield r
    finally:
        unpatch()

        r = yaaredis.StrictRedis(port=REDIS_CONFIG["port"])
        await r.flushall()


def test_patching():
    """
    When patching yaaredis library
        We wrap the correct methods
    When unpatching yaaredis library
        We unwrap the correct methods
    """
    assert isinstance(yaaredis.client.StrictRedis.execute_command, ObjectProxy)
    assert isinstance(yaaredis.client.StrictRedis.pipeline, ObjectProxy)
    assert isinstance(yaaredis.pipeline.StrictPipeline.execute, ObjectProxy)
    assert isinstance(yaaredis.pipeline.StrictPipeline.immediate_execute_command, ObjectProxy)

    unpatch()

    assert not isinstance(yaaredis.client.StrictRedis.execute_command, ObjectProxy)
    assert not isinstance(yaaredis.client.StrictRedis.pipeline, ObjectProxy)
    assert not isinstance(yaaredis.pipeline.StrictPipeline.execute, ObjectProxy)
    assert not isinstance(yaaredis.pipeline.StrictPipeline.immediate_execute_command, ObjectProxy)


@pytest.mark.asyncio
async def test_long_command(snapshot_context, traced_yaaredis):
    with snapshot_context():
        await traced_yaaredis.mget(*range(1000))


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_cmd_max_length(traced_yaaredis):
    with override_config("yaaredis", dict(cmd_max_length=7)):
        await traced_yaaredis.get("here-is-a-long-key")


@pytest.mark.skip(reason="No traces sent to the test agent")
@pytest.mark.subprocess(env=dict(DD_YAAREDIS_CMD_MAX_LENGTH="10"), ddtrace_run=True)
@pytest.mark.snapshot
def test_cmd_max_length_env():
    import asyncio

    import yaaredis

    from tests.contrib.config import REDIS_CONFIG

    async def main():
        r = yaaredis.StrictRedis(port=REDIS_CONFIG["port"])
        await r.get("here-is-a-long-key")

    asyncio.run(main())


@pytest.mark.asyncio
async def test_basics(snapshot_context, traced_yaaredis):
    with snapshot_context():
        await traced_yaaredis.get("cheese")


@pytest.mark.asyncio
async def test_unicode(snapshot_context, traced_yaaredis):
    with snapshot_context():
        await traced_yaaredis.get("😐")


@pytest.mark.asyncio
async def test_pipeline_traced(snapshot_context, traced_yaaredis):
    with snapshot_context():
        p = await traced_yaaredis.pipeline(transaction=False)
        await p.set("blah", 32)
        await p.rpush("foo", "éé")
        await p.hgetall("xxx")
        await p.execute()


@pytest.mark.asyncio
async def test_pipeline_immediate(snapshot_context, traced_yaaredis):
    with snapshot_context():
        p = await traced_yaaredis.pipeline()
        await p.set("a", 1)
        await p.immediate_execute_command("SET", "a", 1)
        await p.execute()


@pytest.mark.asyncio
async def test_meta_override(tracer, test_spans, traced_yaaredis):
    pin = Pin.get_from(traced_yaaredis)
    assert pin is not None
    pin._clone(tags={"cheese": "camembert"}, tracer=tracer).onto(traced_yaaredis)

    await traced_yaaredis.get("cheese")
    test_spans.assert_trace_count(1)
    test_spans.assert_span_count(1)
    assert test_spans.spans[0].service == "redis"
    assert test_spans.spans[0].get_tag("component") == "yaaredis"
    assert test_spans.spans[0].get_tag("span.kind") == "client"
    assert test_spans.spans[0].get_tag("db.system") == "redis"
    assert "cheese" in test_spans.spans[0].get_tags() and test_spans.spans[0].get_tag("cheese") == "camembert"


@pytest.mark.asyncio
async def test_service_name(tracer, test_spans, traced_yaaredis):
    service = str(uuid.uuid4())
    Pin._override(traced_yaaredis, service=service, tracer=tracer)

    await traced_yaaredis.set("cheese", "1")
    test_spans.assert_trace_count(1)
    test_spans.assert_span_count(1)
    assert test_spans.spans[0].service == service


@pytest.mark.asyncio
async def test_service_name_config(tracer, test_spans, traced_yaaredis):
    service = str(uuid.uuid4())
    with override_config("yaaredis", dict(service=service)):
        Pin._override(traced_yaaredis, tracer=tracer)
        await traced_yaaredis.set("cheese", "1")
        test_spans.assert_trace_count(1)
        test_spans.assert_span_count(1)
        assert test_spans.spans[0].service == service


@pytest.mark.asyncio
async def test_opentracing(tracer, snapshot_context, traced_yaaredis):
    """Ensure OpenTracing works with redis."""

    with snapshot_context():
        pin = Pin.get_from(traced_yaaredis)
        ot_tracer = init_tracer("redis_svc", pin.tracer)

        with ot_tracer.start_active_span("redis_get"):
            await traced_yaaredis.get("cheese")


@pytest.mark.parametrize(
    "service_schema",
    [
        (None, None),
        (None, "v0"),
        (None, "v1"),
        ("mysvc", None),
        ("mysvc", "v0"),
        ("mysvc", "v1"),
    ],
)
@pytest.mark.snapshot()
def test_schematization(ddtrace_run_python_code_in_subprocess, service_schema):
    service, schema = service_schema
    code = """
import sys

import pytest

from tests.contrib.yaaredis.test_yaaredis import traced_yaaredis

@pytest.mark.asyncio
async def test_basics(traced_yaaredis):
    async for client in traced_yaaredis:
        await client.get("cheese")


if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """
    env = os.environ.copy()
    if service:
        env["DD_SERVICE"] = service
    if schema:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, (err.decode(), out.decode())
    assert err == b"", err.decode()


@pytest.mark.subprocess(env=dict(DD_REDIS_RESOURCE_ONLY_COMMAND="false"))
@pytest.mark.snapshot
def test_full_command_in_resource_env():
    import ddtrace.auto  # noqa

    import asyncio

    import yaaredis

    import ddtrace
    from tests.contrib.config import REDIS_CONFIG

    async def traced_client():
        with ddtrace.tracer.trace("web-request", service="test"):
            redis_client = yaaredis.StrictRedis(port=REDIS_CONFIG["port"])
            await redis_client.get("put_key_in_resource")
            p = await redis_client.pipeline(transaction=False)
            await p.set("pipeline-cmd1", 1)
            await p.set("pipeline-cmd2", 2)
            await p.execute()

    ddtrace.patch(yaaredis=True)
    asyncio.run(traced_client())


@pytest.mark.snapshot
@pytest.mark.asyncio
@pytest.mark.parametrize("use_global_tracer", [True])
async def test_full_command_in_resource_config(tracer, traced_yaaredis):
    with override_config("yaaredis", dict(resource_only_command=False)):
        with tracer.trace("web-request", service="test"):
            await traced_yaaredis.get("put_key_in_resource")
            p = await traced_yaaredis.pipeline(transaction=False)
            await p.set("pipeline-cmd1", 1)
            await p.set("pipeline-cmd2", 2)
            await p.execute()
