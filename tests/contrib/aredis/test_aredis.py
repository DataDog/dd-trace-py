# -*- coding: utf-8 -*-
import os

import aredis
import pytest
from wrapt import ObjectProxy

from ddtrace import Pin
from ddtrace.contrib.aredis.patch import patch
from ddtrace.contrib.aredis.patch import unpatch
from ddtrace.internal.schema.span_attribute_schema import _DEFAULT_SPAN_SERVICE_NAMES
from tests.opentracer.utils import init_tracer
from tests.utils import override_config

from ..config import REDIS_CONFIG


@pytest.fixture(autouse=True)
async def traced_aredis():
    r = aredis.StrictRedis(port=REDIS_CONFIG["port"])
    await r.flushall()

    patch()
    try:
        yield r
    finally:
        unpatch()

        r = aredis.StrictRedis(port=REDIS_CONFIG["port"])
        await r.flushall()


def test_patching():
    """
    When patching aredis library
        We wrap the correct methods
    When unpatching aredis library
        We unwrap the correct methods
    """
    assert isinstance(aredis.client.StrictRedis.execute_command, ObjectProxy)
    assert isinstance(aredis.client.StrictRedis.pipeline, ObjectProxy)
    assert isinstance(aredis.pipeline.StrictPipeline.execute, ObjectProxy)
    assert isinstance(aredis.pipeline.StrictPipeline.immediate_execute_command, ObjectProxy)

    unpatch()

    assert not isinstance(aredis.client.StrictRedis.execute_command, ObjectProxy)
    assert not isinstance(aredis.client.StrictRedis.pipeline, ObjectProxy)
    assert not isinstance(aredis.pipeline.StrictPipeline.execute, ObjectProxy)
    assert not isinstance(aredis.pipeline.StrictPipeline.immediate_execute_command, ObjectProxy)


@pytest.mark.asyncio
async def test_long_command(snapshot_context):
    with snapshot_context():
        r = aredis.StrictRedis(port=REDIS_CONFIG["port"])
        await r.mget(*range(1000))


@pytest.mark.skip(reason="No traces sent to the test agent")
@pytest.mark.subprocess(env=dict(DD_AREDIS_CMD_MAX_LENGTH="10"), ddtrace_run=True)
@pytest.mark.snapshot
def test_cmd_max_length_env():
    import asyncio

    import aredis

    from tests.contrib.config import REDIS_CONFIG

    async def main():
        r = aredis.StrictRedis(port=REDIS_CONFIG["port"])
        await r.get("here-is-a-long-key")

    asyncio.run(main())


@pytest.mark.asyncio
async def test_cmd_max_length(snapshot_context):
    with override_config("aredis", dict(cmd_max_length=7)):
        with snapshot_context():
            r = aredis.StrictRedis(port=REDIS_CONFIG["port"])
            await r.get("here-is-a-long-key")


@pytest.mark.asyncio
async def test_basics(snapshot_context):
    with snapshot_context():
        r = aredis.StrictRedis(port=REDIS_CONFIG["port"])
        await r.get("cheese")


@pytest.mark.asyncio
async def test_unicode(snapshot_context):
    with snapshot_context():
        r = aredis.StrictRedis(port=REDIS_CONFIG["port"])
        await r.get("😐")


@pytest.mark.asyncio
async def test_analytics_without_rate(snapshot_context):
    with override_config("aredis", dict(analytics_enabled=True)):
        with snapshot_context():
            r = aredis.StrictRedis(port=REDIS_CONFIG["port"])
            await r.get("cheese")


@pytest.mark.asyncio
async def test_analytics_with_rate(snapshot_context):
    with override_config("aredis", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
        with snapshot_context():
            r = aredis.StrictRedis(port=REDIS_CONFIG["port"])
            await r.get("cheese")


@pytest.mark.asyncio
async def test_pipeline_traced(snapshot_context):
    with snapshot_context():
        r = aredis.StrictRedis(port=REDIS_CONFIG["port"])
        p = await r.pipeline(transaction=False)
        await p.set("blah", 32)
        await p.rpush("foo", "éé")
        await p.hgetall("xxx")
        await p.execute()


@pytest.mark.asyncio
async def test_pipeline_immediate(snapshot_context):
    with snapshot_context():
        r = aredis.StrictRedis(port=REDIS_CONFIG["port"])
        p = await r.pipeline()
        await p.set("a", 1)
        await p.immediate_execute_command("SET", "a", 1)
        await p.execute()


@pytest.mark.asyncio
async def test_meta_override(tracer, test_spans):
    r = aredis.StrictRedis(port=REDIS_CONFIG["port"])
    pin = Pin.get_from(r)
    assert pin is not None
    pin.clone(tags={"cheese": "camembert"}, tracer=tracer).onto(r)

    await r.get("cheese")
    test_spans.assert_trace_count(1)
    test_spans.assert_span_count(1)
    assert test_spans.spans[0].service == "redis"
    assert test_spans.spans[0].get_tag("component") == "aredis"
    assert test_spans.spans[0].get_tag("span.kind") == "client"
    assert test_spans.spans[0].get_tag("db.system") == "redis"
    assert "cheese" in test_spans.spans[0].get_tags() and test_spans.spans[0].get_tag("cheese") == "camembert"


@pytest.mark.parametrize(
    "schema_tuplets",
    [
        (None, None, "redis", "redis.command"),
        (None, "v0", "redis", "redis.command"),
        (None, "v1", _DEFAULT_SPAN_SERVICE_NAMES["v1"], "redis.command"),
        ("mysvc", None, "redis", "redis.command"),
        ("mysvc", "v0", "redis", "redis.command"),
        ("mysvc", "v1", "mysvc", "redis.command"),
    ],
)
def test_schematization_of_service_and_operation(ddtrace_run_python_code_in_subprocess, schema_tuplets):
    service, schema, expected_service, expected_operation = schema_tuplets
    code = """
import asyncio
import pytest
import sys
from tests.conftest import *
from ddtrace.pin import Pin
import aredis
from tests.contrib.config import REDIS_CONFIG
from tests.contrib.aredis.test_aredis import traced_aredis

@pytest.mark.asyncio
async def test(tracer, test_spans):
    r = aredis.StrictRedis(port=REDIS_CONFIG["port"])
    pin = Pin.get_from(r)
    assert pin is not None
    pin.clone(tags={{"cheese": "camembert"}}, tracer=tracer).onto(r)

    await r.get("cheese")
    test_spans.assert_trace_count(1)
    test_spans.assert_span_count(1)
    assert test_spans.spans[0].service == "{}"
    assert test_spans.spans[0].name == "{}"

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """.format(
        expected_service, expected_operation
    )
    env = os.environ.copy()
    if service:
        env["DD_SERVICE"] = service
    if schema:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, (err.decode(), out.decode())
    assert err == b"", err.decode()


@pytest.mark.asyncio
async def test_opentracing(tracer, snapshot_context):
    """Ensure OpenTracing works with redis."""

    with snapshot_context():
        r = aredis.StrictRedis(port=REDIS_CONFIG["port"])
        pin = Pin.get_from(r)
        ot_tracer = init_tracer("redis_svc", pin.tracer)

        with ot_tracer.start_active_span("redis_get"):
            await r.get("cheese")


@pytest.mark.subprocess(env=dict(DD_REDIS_RESOURCE_ONLY_COMMAND="false"))
@pytest.mark.snapshot
def test_full_command_in_resource_env():
    import asyncio

    import aredis

    import ddtrace
    from tests.contrib.config import REDIS_CONFIG

    async def traced_client():
        with ddtrace.tracer.trace("web-request", service="test"):
            redis_client = aredis.StrictRedis(port=REDIS_CONFIG["port"])
            await redis_client.get("put_key_in_resource")
            p = await redis_client.pipeline(transaction=False)
            await p.set("pipeline-cmd1", 1)
            await p.set("pipeline-cmd2", 2)
            await p.execute()

    ddtrace.patch(aredis=True)
    asyncio.run(traced_client())


@pytest.mark.snapshot
@pytest.mark.asyncio
@pytest.mark.parametrize("use_global_tracer", [True])
async def test_full_command_in_resource_config(tracer, traced_aredis):
    with override_config("aredis", dict(resource_only_command=False)):
        with tracer.trace("web-request", service="test"):
            await traced_aredis.get("put_key_in_resource")
            p = await traced_aredis.pipeline(transaction=False)
            await p.set("pipeline-cmd1", 1)
            await p.set("pipeline-cmd2", 2)
            await p.execute()
