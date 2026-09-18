"""Tests for http.client_ip propagation onto LLMObs span tags.

When a web framework calls set_http_meta, the client IP must be copied from the
service-entry span into the LLMObs span's tags so the anomaly detection pipeline
can access it without joining against the APM trace (which is sampled away 99.96%
of the time when APM tracing is disabled for LLMObs-only deployments).
"""

import pytest

from ddtrace.contrib.internal.trace_utils import set_http_meta
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.settings._config import Config
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.llmobs._constants import LLMOBS_CLIENT_IP_CORE_KEY
from ddtrace.llmobs._constants import LLMOBS_NETWORK_CLIENT_IP_CORE_KEY
from ddtrace.llmobs._utils import get_llmobs_tags
from ddtrace.trace import Span


@pytest.fixture(autouse=True)
def _clear_llmobs_ip_core_state():
    core.discard_item(LLMOBS_CLIENT_IP_CORE_KEY)
    core.discard_item(LLMOBS_NETWORK_CLIENT_IP_CORE_KEY)
    yield
    core.discard_item(LLMOBS_CLIENT_IP_CORE_KEY)
    core.discard_item(LLMOBS_NETWORK_CLIENT_IP_CORE_KEY)


def test_client_ip_from_root_span_is_copied_to_llmobs_tags(llmobs, tracer):
    """http.client_ip already on the root span (e.g. set by AppSec) lands in LLMObs tags."""
    with tracer.trace("web.request", span_type=SpanTypes.WEB) as root_span:
        root_span.set_tag("http.client_ip", "8.8.8.8")
        root_span.set_tag("network.client.ip", "10.0.0.1")
        with llmobs.llm("my_model", model_provider="openai") as span:
            pass

    tags = get_llmobs_tags(span)
    assert tags["http.client_ip"] == "8.8.8.8"
    assert tags["network.client.ip"] == "10.0.0.1"


def test_client_ip_from_set_http_meta_is_copied_to_llmobs_tags(llmobs, tracer):
    """The client IP stashed by set_http_meta lands in LLMObs tags at span finish."""
    cfg = Config()
    cfg.myint = IntegrationConfig(cfg, "myint")
    web_span = Span("http.request", span_type=SpanTypes.WEB)
    set_http_meta(
        web_span,
        cfg.myint,
        request_headers={"x-forwarded-for": "8.8.8.8", "user-agent": "test-agent/1.0"},
        peer_ip="10.0.0.1",
    )

    with tracer.trace("web.request", span_type=SpanTypes.WEB):
        with llmobs.llm("my_model", model_provider="openai") as span:
            pass

    tags = get_llmobs_tags(span)
    assert tags["http.client_ip"] == "8.8.8.8"
    assert tags["network.client.ip"] == "10.0.0.1"


def test_network_client_ip_set_when_no_forwarded_header(llmobs, tracer):
    """peer_ip is used as network.client.ip when no forwarding headers are present."""
    cfg = Config()
    cfg.myint = IntegrationConfig(cfg, "myint")
    web_span = Span("http.request", span_type=SpanTypes.WEB)
    set_http_meta(
        web_span,
        cfg.myint,
        peer_ip="10.0.0.1",
    )

    with tracer.trace("web.request", span_type=SpanTypes.WEB):
        with llmobs.llm("my_model", model_provider="openai") as span:
            pass

    tags = get_llmobs_tags(span)
    assert tags.get("http.client_ip") == "10.0.0.1"
    assert tags["network.client.ip"] == "10.0.0.1"


def test_no_root_span_does_not_raise(llmobs):
    """evaluate() outside a trace context must not raise when there's nothing to copy from."""
    with llmobs.llm("my_model", model_provider="openai") as span:
        pass

    tags = get_llmobs_tags(span)
    assert "http.client_ip" not in tags
    assert "network.client.ip" not in tags


def test_outbound_http_span_does_not_set_ip(llmobs, tracer):
    """set_http_meta on an outbound HTTP client span must not overwrite the stashed IP."""
    cfg = Config()
    cfg.myint = IntegrationConfig(cfg, "myint")

    # Simulate a web entry span that sets the real client IP.
    inbound_span = Span("http.server", span_type=SpanTypes.WEB)
    set_http_meta(inbound_span, cfg.myint, peer_ip="1.2.3.4")

    # Simulate an outbound HTTP client span with a different peer_ip (the downstream server).
    outbound_span = Span("http.client", span_type=SpanTypes.HTTP)
    set_http_meta(outbound_span, cfg.myint, peer_ip="5.6.7.8")

    with tracer.trace("web.request", span_type=SpanTypes.WEB):
        with llmobs.llm("my_model", model_provider="openai") as span:
            pass

    # The LLMObs span should see the inbound IP, not the outbound server IP.
    tags = get_llmobs_tags(span)
    assert tags.get("network.client.ip") == "1.2.3.4"


def test_no_ip_headers_results_in_no_tag(llmobs, tracer):
    """When set_http_meta fires with no IP information, no tags are added."""
    cfg = Config()
    cfg.myint = IntegrationConfig(cfg, "myint")
    web_span = Span("http.request", span_type=SpanTypes.WEB)
    set_http_meta(web_span, cfg.myint, request_headers={"content-type": "application/json"})

    with tracer.trace("web.request", span_type=SpanTypes.WEB):
        with llmobs.llm("my_model", model_provider="openai") as span:
            pass

    tags = get_llmobs_tags(span)
    assert "http.client_ip" not in tags
    assert "network.client.ip" not in tags
