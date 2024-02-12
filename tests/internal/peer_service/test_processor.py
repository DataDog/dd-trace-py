import os

import mock
import pytest

from ddtrace._trace.span import Span
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.internal.peer_service.processor import PeerServiceProcessor
from ddtrace.settings.peer_service import PeerServiceConfig


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


@pytest.fixture
def test_trace(test_span):
    return [test_span]


@pytest.mark.parametrize("span_kind", [SpanKind.CLIENT, SpanKind.PRODUCER])
def test_processing_peer_service_exists(processor, test_trace, span_kind, peer_service_config):
    processor._set_defaults_enabled = True
    span = test_trace[0]
    span.set_tag(SPAN_KIND, span_kind)
    span.set_tag(peer_service_config.tag_name, "fake_peer_service")
    span.set_tag("out.host", "fake_falue")  # Should not show up
    processor.process_trace(test_trace)

    assert span.get_tag(peer_service_config.tag_name) == "fake_peer_service"
    assert span.get_tag(peer_service_config.source_tag_name) == "peer.service"


@pytest.mark.parametrize("span_kind", [SpanKind.SERVER, SpanKind.CONSUMER])
def test_nothing_happens_for_server_and_consumer(processor, test_trace, span_kind, peer_service_config):
    span = test_trace[0]
    processor._set_defaults_enabled = True
    span.set_tag(SPAN_KIND, span_kind)
    span.set_tag("out.host", "fake_host")
    processor.process_trace(test_trace)

    assert span.get_tag(peer_service_config.source_tag_name) is None


@pytest.mark.parametrize("data_source", PeerServiceConfig.prioritized_data_sources)
def test_existing_data_sources(processor, test_trace, data_source, peer_service_config):
    processor._set_defaults_enabled = True
    span = test_trace[0]
    span.set_tag(SPAN_KIND, SpanKind.CLIENT)
    span.set_tag(data_source, "test_value")

    processor.process_trace(test_trace)

    assert span.get_tag(peer_service_config.tag_name) == "test_value"
    assert span.get_tag(peer_service_config.source_tag_name) == data_source


@pytest.mark.parametrize("data_source", PeerServiceConfig.prioritized_data_sources)
def test_disabled_peer_service(processor, test_trace, data_source, peer_service_config):
    processor._set_defaults_enabled = False
    span = test_trace[0]
    span.set_tag(data_source, "test_value")
    processor.process_trace(test_trace)

    assert span.get_tag(peer_service_config.tag_name) is None
    assert span.get_tag(peer_service_config.source_tag_name) is None


@pytest.mark.parametrize(
    "schema_peer_enabled",
    [
        ("v0", "False", False),
        ("v0", "True", True),
        ("v1", "False", True),
        ("v1", "True", True),
    ],
)
def test_peer_service_enablement(schema_peer_enabled):
    schema_version, env_enabled, expected = schema_peer_enabled

    with mock.patch.dict(os.environ, {"DD_TRACE_PEER_SERVICE_DEFAULTS_ENABLED": env_enabled}):
        with mock.patch("ddtrace.settings.peer_service.SCHEMA_VERSION", schema_version):
            assert PeerServiceConfig().set_defaults_enabled == expected


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
        span_type="span_type",
    )
    span.set_tag(SPAN_KIND, SpanKind.CLIENT)
    span.set_tag("out.host", "test_value")

    span.finish()

    assert span.get_tag(peer_service_config.tag_name) == "test_value"
    assert span.get_tag(peer_service_config.source_tag_name) == "out.host"


def test_peer_service_remap(test_trace):
    with mock.patch.dict(os.environ, {"DD_TRACE_PEER_SERVICE_MAPPING": "fake_peer_service:remapped_service"}):
        peer_service_config = PeerServiceConfig(set_defaults_enabled=True)
        processor = PeerServiceProcessor(peer_service_config)
        processor._set_defaults_enabled = True
        span = test_trace[0]
        span.set_tag(SPAN_KIND, SpanKind.CLIENT)
        span.set_tag(peer_service_config.tag_name, "fake_peer_service")
        processor.process_trace(test_trace)

        assert span.get_tag(peer_service_config.tag_name) == "remapped_service"
        assert span.get_tag(peer_service_config.remap_tag_name) == "fake_peer_service"
        assert span.get_tag(peer_service_config.source_tag_name) == "peer.service"


def test_remap_still_happens_when_defaults_disabled(test_trace):
    with mock.patch.dict(os.environ, {"DD_TRACE_PEER_SERVICE_MAPPING": "fake_peer_service:remapped_service"}):
        peer_service_config = PeerServiceConfig(set_defaults_enabled=False)
        processor = PeerServiceProcessor(peer_service_config)
        processor._set_defaults_enabled = False
        span = test_trace[0]
        span.set_tag(peer_service_config.tag_name, "fake_peer_service")
        processor.process_trace(test_trace)

        assert span.get_tag(peer_service_config.tag_name) == "remapped_service"
        assert span.get_tag(peer_service_config.remap_tag_name) == "fake_peer_service"
