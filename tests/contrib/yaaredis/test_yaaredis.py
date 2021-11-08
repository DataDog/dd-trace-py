# -*- encoding: utf-8 -*-
import uuid

import pytest
import yaaredis

from ddtrace import Pin
from ddtrace.contrib.yaaredis.patch import patch
from ddtrace.contrib.yaaredis.patch import unpatch
from ddtrace.vendor.wrapt import ObjectProxy
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
async def test_basics(snapshot_context, traced_yaaredis):
    with snapshot_context():
        await traced_yaaredis.get("cheese")


@pytest.mark.asyncio
async def test_analytics_without_rate(snapshot_context, traced_yaaredis):
    with override_config("yaaredis", dict(analytics_enabled=True)):
        with snapshot_context():
            await traced_yaaredis.get("cheese")


@pytest.mark.asyncio
async def test_analytics_with_rate(snapshot_context, traced_yaaredis):
    with override_config("yaaredis", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
        with snapshot_context():
            await traced_yaaredis.get("cheese")


@pytest.mark.asyncio
async def test_pipeline_traced(snapshot_context, traced_yaaredis):
    with snapshot_context():
        p = await traced_yaaredis.pipeline(transaction=False)
        await p.set("blah", 32)
        await p.rpush("foo", u"éé")
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
    pin.clone(tags={"cheese": "camembert"}, tracer=tracer).onto(traced_yaaredis)

    await traced_yaaredis.get("cheese")
    test_spans.assert_trace_count(1)
    test_spans.assert_span_count(1)
    assert test_spans.spans[0].service == "redis"
    assert "cheese" in test_spans.spans[0].meta and test_spans.spans[0].meta["cheese"] == "camembert"


@pytest.mark.asyncio
async def test_service_name(tracer, test_spans, traced_yaaredis):
    service = str(uuid.uuid4())
    Pin.override(traced_yaaredis, service=service, tracer=tracer)

    await traced_yaaredis.set("cheese", "1")
    test_spans.assert_trace_count(1)
    test_spans.assert_span_count(1)
    assert test_spans.spans[0].service == service


@pytest.mark.asyncio
async def test_service_name_config(tracer, test_spans, traced_yaaredis):
    service = str(uuid.uuid4())
    with override_config("yaaredis", dict(service=service)):
        Pin.override(traced_yaaredis, tracer=tracer)
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
