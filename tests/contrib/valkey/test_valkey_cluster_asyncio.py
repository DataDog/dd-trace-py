# -*- coding: utf-8 -*-
import pytest
import valkey

from ddtrace.contrib.internal.valkey.patch import patch
from ddtrace.contrib.internal.valkey.patch import unpatch
from ddtrace.trace import Pin
from tests.contrib.config import VALKEY_CLUSTER_CONFIG
from tests.utils import DummyTracer
from tests.utils import assert_is_measured


TEST_HOST = VALKEY_CLUSTER_CONFIG["host"]
TEST_PORTS = VALKEY_CLUSTER_CONFIG["ports"]


@pytest.mark.asyncio
@pytest.fixture
async def valkey_cluster():
    startup_nodes = [valkey.asyncio.cluster.ClusterNode(TEST_HOST, int(port)) for port in TEST_PORTS.split(",")]
    yield valkey.asyncio.cluster.ValkeyCluster(startup_nodes=startup_nodes)


@pytest.mark.asyncio
@pytest.fixture
async def traced_valkey_cluster(tracer, test_spans):
    patch()
    startup_nodes = [valkey.asyncio.cluster.ClusterNode(TEST_HOST, int(port)) for port in TEST_PORTS.split(",")]
    valkey_cluster = valkey.asyncio.cluster.ValkeyCluster(startup_nodes=startup_nodes)
    await valkey_cluster.flushall()
    Pin._override(valkey_cluster, tracer=tracer)
    try:
        yield valkey_cluster, test_spans
    finally:
        unpatch()
    await valkey_cluster.flushall()


@pytest.mark.asyncio
async def test_basics(traced_valkey_cluster):
    cluster, test_spans = traced_valkey_cluster
    us = await cluster.get("cheese")
    assert us is None

    traces = test_spans.pop_traces()
    assert len(traces) == 1
    spans = traces[0]
    assert len(spans) == 1

    span = spans[0]
    assert_is_measured(span)
    assert span.service == "valkey"
    assert span.name == "valkey.command"
    assert span.span_type == "valkey"
    assert span.error == 0
    assert span.get_tag("valkey.raw_command") == "GET cheese"
    assert span.get_tag("component") == "valkey"
    assert span.get_tag("db.system") == "valkey"
    assert span.get_metric("valkey.args_length") == 2
    assert span.resource == "GET"


@pytest.mark.asyncio
async def test_unicode(traced_valkey_cluster):
    cluster, test_spans = traced_valkey_cluster
    us = await cluster.get("😐")
    assert us is None

    traces = test_spans.pop_traces()
    assert len(traces) == 1
    spans = traces[0]
    assert len(spans) == 1

    span = spans[0]
    assert_is_measured(span)
    assert span.service == "valkey"
    assert span.name == "valkey.command"
    assert span.span_type == "valkey"
    assert span.error == 0
    assert span.get_tag("valkey.raw_command") == "GET 😐"
    assert span.get_tag("component") == "valkey"
    assert span.get_tag("db.system") == "valkey"
    assert span.get_metric("valkey.args_length") == 2
    assert span.resource == "GET"


@pytest.mark.asyncio
async def test_pipeline(traced_valkey_cluster):
    cluster, test_spans = traced_valkey_cluster
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
    assert span.service == "valkey"
    assert span.name == "valkey.command"
    assert span.resource == "SET\nRPUSH\nHGETALL"
    assert span.span_type == "valkey"
    assert span.error == 0
    assert span.get_tag("valkey.raw_command") == "SET blah 32\nRPUSH foo éé\nHGETALL xxx"
    assert span.get_tag("component") == "valkey"
    assert span.get_metric("valkey.pipeline_length") == 3


@pytest.mark.asyncio
async def test_patch_unpatch(valkey_cluster):
    tracer = DummyTracer()

    # Test patch idempotence
    patch()
    patch()

    r = valkey_cluster
    Pin._override(r, tracer=tracer)
    await r.get("key")

    spans = tracer.pop()
    assert spans, spans
    assert len(spans) == 1

    # Test unpatch
    unpatch()

    r = valkey_cluster
    await r.get("key")

    spans = tracer.pop()
    assert not spans, spans

    # Test patch again
    patch()

    r = valkey_cluster
    Pin._override(r, tracer=tracer)
    await r.get("key")

    spans = tracer.pop()
    assert spans, spans
    assert len(spans) == 1
    unpatch()


@pytest.mark.subprocess(
    env=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"),
    err=None,  # avoid checking stderr because of an expected deprecation warning
)
def test_default_service_name_v1():
    import asyncio

    import valkey

    from ddtrace.contrib.internal.valkey.patch import patch
    from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
    from ddtrace.trace import Pin
    from tests.contrib.config import VALKEY_CLUSTER_CONFIG
    from tests.utils import DummyTracer
    from tests.utils import TracerSpanContainer

    patch()

    async def test():
        startup_nodes = [
            valkey.asyncio.cluster.ClusterNode(VALKEY_CLUSTER_CONFIG["host"], int(port))
            for port in VALKEY_CLUSTER_CONFIG["ports"].split(",")
        ]
        r = valkey.asyncio.cluster.ValkeyCluster(startup_nodes=startup_nodes)
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


@pytest.mark.subprocess(
    env=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"),
    err=None,  # avoid checking stderr because of an expected deprecation warning
)
def test_user_specified_service_v0():
    """
    When a user specifies a service for the app
        The valkeycluster integration should not use it.
    """
    import asyncio

    import valkey

    from ddtrace import config
    from ddtrace.contrib.internal.valkey.patch import patch
    from ddtrace.trace import Pin
    from tests.contrib.config import VALKEY_CLUSTER_CONFIG
    from tests.utils import DummyTracer
    from tests.utils import TracerSpanContainer

    patch()

    async def test():
        # # Ensure that the service name was configured
        assert config.service == "mysvc"

        startup_nodes = [
            valkey.asyncio.cluster.ClusterNode(VALKEY_CLUSTER_CONFIG["host"], int(port))
            for port in VALKEY_CLUSTER_CONFIG["ports"].split(",")
        ]
        r = valkey.asyncio.cluster.ValkeyCluster(startup_nodes=startup_nodes)
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


@pytest.mark.subprocess(
    env=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"),
    err=None,  # avoid checking stderr because of an expected deprecation warning
)
def test_user_specified_service_v1():
    """
    When a user specifies a service for the app
        The valkeycluster integration should use it.
    """
    import asyncio

    import valkey

    from ddtrace import config
    from ddtrace.contrib.internal.valkey.patch import patch
    from ddtrace.trace import Pin
    from tests.contrib.config import VALKEY_CLUSTER_CONFIG
    from tests.utils import DummyTracer
    from tests.utils import TracerSpanContainer

    patch()

    async def test():
        # # Ensure that the service name was configured
        assert config.service == "mysvc"

        startup_nodes = [
            valkey.asyncio.cluster.ClusterNode(VALKEY_CLUSTER_CONFIG["host"], int(port))
            for port in VALKEY_CLUSTER_CONFIG["ports"].split(",")
        ]
        r = valkey.asyncio.cluster.ValkeyCluster(startup_nodes=startup_nodes)
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


@pytest.mark.subprocess(
    env=dict(DD_VALKEY_SERVICE="myvalkeycluster", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"),
    err=None,  # avoid checking stderr because of an expected deprecation warning
)
def test_env_user_specified_valkeycluster_service_v0():
    import asyncio

    import valkey

    from ddtrace.contrib.internal.valkey.patch import patch
    from ddtrace.trace import Pin
    from tests.contrib.config import VALKEY_CLUSTER_CONFIG
    from tests.utils import DummyTracer
    from tests.utils import TracerSpanContainer

    patch()

    async def test():
        startup_nodes = [
            valkey.asyncio.cluster.ClusterNode(VALKEY_CLUSTER_CONFIG["host"], int(port))
            for port in VALKEY_CLUSTER_CONFIG["ports"].split(",")
        ]
        r = valkey.asyncio.cluster.ValkeyCluster(startup_nodes=startup_nodes)
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
        assert span.service == "myvalkeycluster"

    asyncio.run(test())


@pytest.mark.subprocess(
    env=dict(DD_VALKEY_SERVICE="myvalkeycluster", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"),
    err=None,  # avoid checking stderr because of an expected deprecation warning
)
def test_env_user_specified_valkeycluster_service_v1():
    import asyncio

    import valkey

    from ddtrace.contrib.internal.valkey.patch import patch
    from ddtrace.trace import Pin
    from tests.contrib.config import VALKEY_CLUSTER_CONFIG
    from tests.utils import DummyTracer
    from tests.utils import TracerSpanContainer

    patch()

    async def test():
        startup_nodes = [
            valkey.asyncio.cluster.ClusterNode(VALKEY_CLUSTER_CONFIG["host"], int(port))
            for port in VALKEY_CLUSTER_CONFIG["ports"].split(",")
        ]
        r = valkey.asyncio.cluster.ValkeyCluster(startup_nodes=startup_nodes)
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
        assert span.service == "myvalkeycluster"

    asyncio.run(test())


@pytest.mark.subprocess(
    env=dict(
        DD_SERVICE="mysvc",
        DD_VALKEY_SERVICE="myvalkeycluster",
        DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0",
    ),
    err=None,  # avoid checking stderr because of an expected deprecation warning
)
def test_service_precedence_v0():
    import asyncio

    import valkey

    from ddtrace import config
    from ddtrace.contrib.internal.valkey.patch import patch
    from ddtrace.trace import Pin
    from tests.contrib.config import VALKEY_CLUSTER_CONFIG
    from tests.utils import DummyTracer
    from tests.utils import TracerSpanContainer

    patch()

    async def test():
        # # Ensure that the service name was configured
        assert config.service == "mysvc"

        startup_nodes = [
            valkey.asyncio.cluster.ClusterNode(VALKEY_CLUSTER_CONFIG["host"], int(port))
            for port in VALKEY_CLUSTER_CONFIG["ports"].split(",")
        ]
        r = valkey.asyncio.cluster.ValkeyCluster(startup_nodes=startup_nodes)
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
        assert span.service == "myvalkeycluster"

    asyncio.run(test())


@pytest.mark.subprocess(
    env=dict(DD_SERVICE="mysvc", DD_VALKEY_SERVICE="myvalkeycluster", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"),
    err=None,  # avoid checking stderr because of an expected deprecation warning
)
def test_service_precedence_v1():
    import asyncio

    import valkey

    from ddtrace import config
    from ddtrace.contrib.internal.valkey.patch import patch
    from ddtrace.trace import Pin
    from tests.contrib.config import VALKEY_CLUSTER_CONFIG
    from tests.utils import DummyTracer
    from tests.utils import TracerSpanContainer

    patch()

    async def test():
        # # Ensure that the service name was configured
        assert config.service == "mysvc"

        startup_nodes = [
            valkey.asyncio.cluster.ClusterNode(VALKEY_CLUSTER_CONFIG["host"], int(port))
            for port in VALKEY_CLUSTER_CONFIG["ports"].split(",")
        ]
        r = valkey.asyncio.cluster.ValkeyCluster(startup_nodes=startup_nodes)
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
        assert span.service == "myvalkeycluster"

    asyncio.run(test())
