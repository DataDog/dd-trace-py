# -*- coding: utf-8 -*-
import aredis
import pytest

from ddtrace import Pin
from ddtrace.contrib.aredis.patch import patch
from ddtrace.contrib.aredis.patch import unpatch
from ddtrace.vendor.wrapt import ObjectProxy
from tests.opentracer.utils import init_tracer
from tests.utils import override_config

from ..config import REDIS_CONFIG


@pytest.fixture(autouse=True)
async def traced_aredis():
    r = aredis.StrictRedis(port=REDIS_CONFIG["port"])
    await r.flushall()

    patch()
    try:
        yield
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


@pytest.mark.asyncio
async def test_basics(snapshot_context):
    with snapshot_context():
        r = aredis.StrictRedis(port=REDIS_CONFIG["port"])
        await r.get("cheese")


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
        await p.rpush("foo", u"éé")
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
    assert "cheese" in test_spans.spans[0].meta and test_spans.spans[0].meta["cheese"] == "camembert"


@pytest.mark.asyncio
async def test_opentracing(tracer, snapshot_context):
    """Ensure OpenTracing works with redis."""

    with snapshot_context():
        r = aredis.StrictRedis(port=REDIS_CONFIG["port"])
        pin = Pin.get_from(r)
        ot_tracer = init_tracer("redis_svc", pin.tracer)

        with ot_tracer.start_active_span("redis_get"):
            await r.get("cheese")
