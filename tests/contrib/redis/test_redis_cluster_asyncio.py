# -*- coding: utf-8 -*-
import pytest
import redis

from ddtrace.contrib.internal.redis.patch import patch
from ddtrace.contrib.internal.redis.patch import unpatch
from ddtrace.trace import Pin
from tests.contrib.config import REDISCLUSTER_CONFIG
from tests.utils import DummyTracer
from tests.utils import assert_is_measured


TEST_HOST = REDISCLUSTER_CONFIG["host"]
TEST_PORTS = REDISCLUSTER_CONFIG["ports"]


@pytest.mark.asyncio
@pytest.fixture
async def redis_cluster():
    startup_nodes = [redis.asyncio.cluster.ClusterNode(TEST_HOST, int(port)) for port in TEST_PORTS.split(",")]
    yield redis.asyncio.cluster.RedisCluster(startup_nodes=startup_nodes)


@pytest.mark.asyncio
@pytest.fixture
async def traced_redis_cluster(tracer, test_spans):
    patch()
    startup_nodes = [redis.asyncio.cluster.ClusterNode(TEST_HOST, int(port)) for port in TEST_PORTS.split(",")]
    redis_cluster = redis.asyncio.cluster.RedisCluster(startup_nodes=startup_nodes)
    await redis_cluster.flushall()
    Pin._override(redis_cluster, tracer=tracer)
    try:
        yield redis_cluster, test_spans
    finally:
        unpatch()
    await redis_cluster.flushall()


@pytest.mark.skipif(redis.VERSION < (4, 3, 0), reason="redis.asyncio.cluster is not implemented in redis<4.3.0")
@pytest.mark.asyncio
async def test_basics(traced_redis_cluster):
    cluster, test_spans = traced_redis_cluster
    us = await cluster.get("cheese")
    assert us is None

    traces = test_spans.pop_traces()
    assert len(traces) == 1
    spans = traces[0]
    assert len(spans) == 1

    span = spans[0]
    assert_is_measured(span)
    assert span.service == "redis"
    assert span.name == "redis.command"
    assert span.span_type == "redis"
    assert span.error == 0
    assert span.get_tag("redis.raw_command") == "GET cheese"
    assert span.get_tag("component") == "redis"
    assert span.get_tag("db.system") == "redis"
    assert span.get_metric("redis.args_length") == 2
    assert span.resource == "GET"


@pytest.mark.skipif(redis.VERSION < (4, 3, 0), reason="redis.asyncio.cluster is not implemented in redis<4.3.0")
@pytest.mark.asyncio
async def test_unicode(traced_redis_cluster):
    cluster, test_spans = traced_redis_cluster
    us = await cluster.get("😐")
    assert us is None

    traces = test_spans.pop_traces()
    assert len(traces) == 1
    spans = traces[0]
    assert len(spans) == 1

    span = spans[0]
    assert_is_measured(span)
    assert span.service == "redis"
    assert span.name == "redis.command"
    assert span.span_type == "redis"
    assert span.error == 0
    assert span.get_tag("redis.raw_command") == "GET 😐"
    assert span.get_tag("component") == "redis"
    assert span.get_tag("db.system") == "redis"
    assert span.get_metric("redis.args_length") == 2
    assert span.resource == "GET"


@pytest.mark.skipif(
    redis.VERSION < (4, 3, 2), reason="redis.asyncio.cluster pipeline is not implemented in redis<4.3.2"
)
@pytest.mark.asyncio
async def test_pipeline(traced_redis_cluster):
    cluster, test_spans = traced_redis_cluster
    async with cluster.pipeline(transaction=False) as p:
        p.set("blah", 32)
        p.rpush("foo", "éé")
        p.hgetall("xxx")
        await p.execute()

    traces = test_spans.pop_traces()
    assert len(traces) == 1
    spans = traces[0]
    assert len(spans) == 1

    span = spans[0]
    assert_is_measured(span)
    assert span.service == "redis"
    assert span.name == "redis.command"
    assert span.resource == "SET\nRPUSH\nHGETALL"
    assert span.span_type == "redis"
    assert span.error == 0
    assert span.get_tag("redis.raw_command") == "SET blah 32\nRPUSH foo éé\nHGETALL xxx"
    assert span.get_tag("component") == "redis"
    assert span.get_metric("redis.pipeline_length") == 3


@pytest.mark.skipif(redis.VERSION < (4, 3, 0), reason="redis.asyncio.cluster is not implemented in redis<4.3.0")
@pytest.mark.asyncio
async def test_patch_unpatch(redis_cluster):
    tracer = DummyTracer()

    # Test patch idempotence
    patch()
    patch()

    r = redis_cluster
    Pin._override(r, tracer=tracer)
    await r.get("key")

    spans = tracer.pop()
    assert spans, spans
    assert len(spans) == 1

    # Test unpatch
    unpatch()

    r = redis_cluster
    await r.get("key")

    spans = tracer.pop()
    assert not spans, spans

    # Test patch again
    patch()

    r = redis_cluster
    Pin._override(r, tracer=tracer)
    await r.get("key")

    spans = tracer.pop()
    assert spans, spans
    assert len(spans) == 1
    unpatch()


@pytest.mark.skipif(redis.VERSION < (4, 3, 0), reason="redis.asyncio.cluster is not implemented in redis<4.3.0")
@pytest.mark.subprocess(
    env=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"),
    err=None,  # avoid checking stderr because of an expected deprecation warning
)
def test_default_service_name_v1():
    import asyncio

    import redis

    from ddtrace.contrib.internal.redis.patch import patch
    from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
    from ddtrace.trace import Pin
    from tests.contrib.config import REDISCLUSTER_CONFIG
    from tests.utils import DummyTracer
    from tests.utils import TracerSpanContainer

    patch()

    async def test():
        startup_nodes = [
            redis.asyncio.cluster.ClusterNode(REDISCLUSTER_CONFIG["host"], int(port))
            for port in REDISCLUSTER_CONFIG["ports"].split(",")
        ]
        r = redis.asyncio.cluster.RedisCluster(startup_nodes=startup_nodes)
        tracer = DummyTracer()
        test_spans = TracerSpanContainer(tracer)

        Pin.get_from(r)._clone(tracer=tracer).onto(r)
        await r.get("key")
        await r.close()

        traces = test_spans.pop_traces()
        assert len(traces) == 1
        spans = traces[0]
        assert len(spans) == 1
        span = spans[0]
        assert span.service == DEFAULT_SPAN_SERVICE_NAME

    asyncio.run(test())


@pytest.mark.skipif(redis.VERSION < (4, 3, 0), reason="redis.asyncio.cluster is not implemented in redis<4.3.0")
@pytest.mark.subprocess(
    env=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"),
    err=None,  # avoid checking stderr because of an expected deprecation warning
)
def test_user_specified_service_v0():
    """
    When a user specifies a service for the app
        The rediscluster integration should not use it.
    """
    import asyncio

    import redis

    from ddtrace import config
    from ddtrace.contrib.internal.redis.patch import patch
    from ddtrace.trace import Pin
    from tests.contrib.config import REDISCLUSTER_CONFIG
    from tests.utils import DummyTracer
    from tests.utils import TracerSpanContainer

    patch()

    async def test():
        # # Ensure that the service name was configured
        assert config.service == "mysvc"

        startup_nodes = [
            redis.asyncio.cluster.ClusterNode(REDISCLUSTER_CONFIG["host"], int(port))
            for port in REDISCLUSTER_CONFIG["ports"].split(",")
        ]
        r = redis.asyncio.cluster.RedisCluster(startup_nodes=startup_nodes)
        tracer = DummyTracer()
        test_spans = TracerSpanContainer(tracer)

        Pin.get_from(r)._clone(tracer=tracer).onto(r)
        await r.get("key")
        await r.close()

        traces = test_spans.pop_traces()
        assert len(traces) == 1
        spans = traces[0]
        assert len(spans) == 1
        span = spans[0]
        assert span.service != "mysvc"

    asyncio.run(test())


@pytest.mark.skipif(redis.VERSION < (4, 3, 0), reason="redis.asyncio.cluster is not implemented in redis<4.3.0")
@pytest.mark.subprocess(
    env=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"),
    err=None,  # avoid checking stderr because of an expected deprecation warning
)
def test_user_specified_service_v1():
    """
    When a user specifies a service for the app
        The rediscluster integration should use it.
    """
    import asyncio

    import redis

    from ddtrace import config
    from ddtrace.contrib.internal.redis.patch import patch
    from ddtrace.trace import Pin
    from tests.contrib.config import REDISCLUSTER_CONFIG
    from tests.utils import DummyTracer
    from tests.utils import TracerSpanContainer

    patch()

    async def test():
        # # Ensure that the service name was configured
        assert config.service == "mysvc"

        startup_nodes = [
            redis.asyncio.cluster.ClusterNode(REDISCLUSTER_CONFIG["host"], int(port))
            for port in REDISCLUSTER_CONFIG["ports"].split(",")
        ]
        r = redis.asyncio.cluster.RedisCluster(startup_nodes=startup_nodes)
        tracer = DummyTracer()
        test_spans = TracerSpanContainer(tracer)

        Pin.get_from(r)._clone(tracer=tracer).onto(r)
        await r.get("key")
        await r.close()

        traces = test_spans.pop_traces()
        assert len(traces) == 1
        spans = traces[0]
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "mysvc"

    asyncio.run(test())


@pytest.mark.skipif(redis.VERSION < (4, 3, 0), reason="redis.asyncio.cluster is not implemented in redis<4.3.0")
@pytest.mark.subprocess(
    env=dict(DD_REDIS_SERVICE="myrediscluster", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"),
    err=None,  # avoid checking stderr because of an expected deprecation warning
)
def test_env_user_specified_rediscluster_service_v0():
    import asyncio

    import redis

    from ddtrace.contrib.internal.redis.patch import patch
    from ddtrace.trace import Pin
    from tests.contrib.config import REDISCLUSTER_CONFIG
    from tests.utils import DummyTracer
    from tests.utils import TracerSpanContainer

    patch()

    async def test():
        startup_nodes = [
            redis.asyncio.cluster.ClusterNode(REDISCLUSTER_CONFIG["host"], int(port))
            for port in REDISCLUSTER_CONFIG["ports"].split(",")
        ]
        r = redis.asyncio.cluster.RedisCluster(startup_nodes=startup_nodes)
        tracer = DummyTracer()
        test_spans = TracerSpanContainer(tracer)

        Pin.get_from(r)._clone(tracer=tracer).onto(r)
        await r.get("key")
        await r.close()

        traces = test_spans.pop_traces()
        assert len(traces) == 1
        spans = traces[0]
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "myrediscluster"

    asyncio.run(test())


@pytest.mark.skipif(redis.VERSION < (4, 3, 0), reason="redis.asyncio.cluster is not implemented in redis<4.3.0")
@pytest.mark.subprocess(
    env=dict(DD_REDIS_SERVICE="myrediscluster", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"),
    err=None,  # avoid checking stderr because of an expected deprecation warning
)
def test_env_user_specified_rediscluster_service_v1():
    import asyncio

    import redis

    from ddtrace.contrib.internal.redis.patch import patch
    from ddtrace.trace import Pin
    from tests.contrib.config import REDISCLUSTER_CONFIG
    from tests.utils import DummyTracer
    from tests.utils import TracerSpanContainer

    patch()

    async def test():
        startup_nodes = [
            redis.asyncio.cluster.ClusterNode(REDISCLUSTER_CONFIG["host"], int(port))
            for port in REDISCLUSTER_CONFIG["ports"].split(",")
        ]
        r = redis.asyncio.cluster.RedisCluster(startup_nodes=startup_nodes)
        tracer = DummyTracer()
        test_spans = TracerSpanContainer(tracer)

        Pin.get_from(r)._clone(tracer=tracer).onto(r)
        await r.get("key")
        await r.close()

        traces = test_spans.pop_traces()
        assert len(traces) == 1
        spans = traces[0]
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "myrediscluster"

    asyncio.run(test())


@pytest.mark.skipif(redis.VERSION < (4, 3, 0), reason="redis.asyncio.cluster is not implemented in redis<4.3.0")
@pytest.mark.subprocess(
    env=dict(
        DD_SERVICE="mysvc",
        DD_REDIS_SERVICE="myrediscluster",
        DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0",
    ),
    err=None,  # avoid checking stderr because of an expected deprecation warning
)
def test_service_precedence_v0():
    import asyncio

    import redis

    from ddtrace import config
    from ddtrace.contrib.internal.redis.patch import patch
    from ddtrace.trace import Pin
    from tests.contrib.config import REDISCLUSTER_CONFIG
    from tests.utils import DummyTracer
    from tests.utils import TracerSpanContainer

    patch()

    async def test():
        # # Ensure that the service name was configured
        assert config.service == "mysvc"

        startup_nodes = [
            redis.asyncio.cluster.ClusterNode(REDISCLUSTER_CONFIG["host"], int(port))
            for port in REDISCLUSTER_CONFIG["ports"].split(",")
        ]
        r = redis.asyncio.cluster.RedisCluster(startup_nodes=startup_nodes)
        tracer = DummyTracer()
        test_spans = TracerSpanContainer(tracer)

        Pin.get_from(r)._clone(tracer=tracer).onto(r)
        await r.get("key")
        await r.close()

        traces = test_spans.pop_traces()
        assert len(traces) == 1
        spans = traces[0]
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "myrediscluster"

    asyncio.run(test())


@pytest.mark.skipif(redis.VERSION < (4, 3, 0), reason="redis.asyncio.cluster is not implemented in redis<4.3.0")
@pytest.mark.subprocess(
    env=dict(DD_SERVICE="mysvc", DD_REDIS_SERVICE="myrediscluster", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"),
    err=None,  # avoid checking stderr because of an expected deprecation warning
)
def test_service_precedence_v1():
    import asyncio

    import redis

    from ddtrace import config
    from ddtrace.contrib.internal.redis.patch import patch
    from ddtrace.trace import Pin
    from tests.contrib.config import REDISCLUSTER_CONFIG
    from tests.utils import DummyTracer
    from tests.utils import TracerSpanContainer

    patch()

    async def test():
        # # Ensure that the service name was configured
        assert config.service == "mysvc"

        startup_nodes = [
            redis.asyncio.cluster.ClusterNode(REDISCLUSTER_CONFIG["host"], int(port))
            for port in REDISCLUSTER_CONFIG["ports"].split(",")
        ]
        r = redis.asyncio.cluster.RedisCluster(startup_nodes=startup_nodes)
        tracer = DummyTracer()
        test_spans = TracerSpanContainer(tracer)

        Pin.get_from(r)._clone(tracer=tracer).onto(r)
        await r.get("key")
        await r.close()

        traces = test_spans.pop_traces()
        assert len(traces) == 1
        spans = traces[0]
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "myrediscluster"

    asyncio.run(test())
