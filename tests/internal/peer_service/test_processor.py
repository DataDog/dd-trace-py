import os

import mock
import pytest

from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.internal.processor.trace import PeerServiceProcessor
from ddtrace.settings.peer_service import PeerServiceConfig
from ddtrace.span import Span


@pytest.fixture
def peer_service_config():
    return PeerServiceConfig()


@pytest.fixture
def processor(peer_service_config):
    return PeerServiceProcessor(peer_service_config)


@pytest.fixture
def test_span():
    return Span(
        "test_messaging_span",
        service="test_service",
        resource="test_resource",
        span_type="test_span_type",
    )


@pytest.mark.parametrize("span_kind", [SpanKind.CLIENT, SpanKind.PRODUCER])
def test_processing_peer_service_exists(processor, test_span, span_kind, peer_service_config):
    processor.enabled = True
    test_span.set_tag(SPAN_KIND, span_kind)
    test_span.set_tag(peer_service_config.tag_name, "fake_peer_service")
    test_span.set_tag("out.host", "fake_falue")  # Should not show up
    processor.on_span_finish(test_span)

    assert test_span.get_tag(peer_service_config.tag_name) == "fake_peer_service"
    assert test_span.get_tag(peer_service_config.source_tag_name) == "peer.service"


@pytest.mark.parametrize("span_kind", [SpanKind.SERVER, SpanKind.CONSUMER])
def test_nothing_happens_for_server_and_consumer(processor, test_span, span_kind, peer_service_config):
    processor.enabled = True
    test_span.set_tag(SPAN_KIND, span_kind)
    test_span.set_tag("out.host", "fake_host")
    processor.on_span_finish(test_span)

    assert test_span.get_tag(peer_service_config.source_tag_name) is None


@pytest.mark.parametrize("data_source", PeerServiceConfig.prioritized_data_sources)
def test_existing_data_sources(processor, test_span, data_source, peer_service_config):
    processor.enabled = True
    test_span.set_tag(SPAN_KIND, SpanKind.CLIENT)
    test_span.set_tag(data_source, "test_value")

    processor.on_span_finish(test_span)

    assert test_span.get_tag(peer_service_config.tag_name) == "test_value"
    assert test_span.get_tag(peer_service_config.source_tag_name) == data_source


@pytest.mark.parametrize("data_source", PeerServiceConfig.prioritized_data_sources)
def test_disabled_peer_service(processor, test_span, data_source, peer_service_config):
    processor.enabled = False
    test_span.set_tag(data_source, "test_value")
    processor.on_span_finish(test_span)

    assert test_span.get_tag(peer_service_config.tag_name) is None
    assert test_span.get_tag(peer_service_config.source_tag_name) is None


@pytest.mark.parametrize(
    "schema_peer_enabled",
    [
        ("v0", False, False),
        ("v0", True, True),
        ("v1", False, True),
        ("v1", True, True),
    ],
)
def fake_peer_service_enablement(span, schema_peer_enabled):
    schema_version, env_enabled, expected = schema_peer_enabled

    with mock.patch.dict(os.environ, {"DD_TRACE_PEER_SERVICE_DEFAULTS_ENABLED": env_enabled}):
        with mock.patch("ddtrace.internal.schema.SCHEMA_VERSION", schema_version):
            assert PeerServiceConfig().enabled == expected


@pytest.mark.subprocess(env=dict(DD_TRACE_PEER_SERVICE_DEFAULTS_ENABLED="True"), ddtrace_run=True)
def test_tracer_hooks():
    from ddtrace.constants import SPAN_KIND
    from ddtrace.ext import SpanKind
    from ddtrace.settings.peer_service import PeerServiceConfig
    from tests.utils import DummyTracer

    peer_service_config = PeerServiceConfig()
    tracer = DummyTracer()
    span = tracer.trace(
        "test",
        service="test_service",
        resource="test_resource",
        span_type="test_span_type",
    )
    span.set_tag(SPAN_KIND, SpanKind.CLIENT)
    span.set_tag("out.host", "test_value")

    span.finish()

    assert span.get_tag(peer_service_config.tag_name) == "test_value"
    assert span.get_tag(peer_service_config.source_tag_name) == "out.host"
