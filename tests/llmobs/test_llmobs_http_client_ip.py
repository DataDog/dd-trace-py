"""Tests for http.client_ip propagation onto LLMObs span tags.

When AppSec or DD_TRACE_CLIENT_IP_ENABLED is active, the APM root span carries
http.client_ip and network.client.ip. At LLMObs root span finish those tags are
copied into the LLMObs tags so the anomaly detection pipeline can access them
without joining against APM traces.

Only the LLMObs root span is enriched; nested spans join against the root at
query time.
"""
from ddtrace.ext import SpanTypes
from ddtrace.llmobs._utils import get_llmobs_tags


def test_client_ip_from_root_span_is_copied_to_llmobs_tags(llmobs, tracer):
    """http.client_ip and network.client.ip already on the APM root span land in LLMObs tags."""
    with tracer.trace("web.request", span_type=SpanTypes.WEB) as root_span:
        root_span.set_tag("http.client_ip", "8.8.8.8")
        root_span.set_tag("network.client.ip", "10.0.0.1")
        with llmobs.llm("my_model", model_provider="openai") as span:
            pass

    tags = get_llmobs_tags(span)
    assert tags["http.client_ip"] == "8.8.8.8"
    assert tags["network.client.ip"] == "10.0.0.1"


def test_no_root_span_does_not_raise(llmobs):
    """An LLMObs span started outside any trace context must not raise."""
    with llmobs.llm("my_model", model_provider="openai") as span:
        pass

    tags = get_llmobs_tags(span)
    assert "http.client_ip" not in tags
    assert "network.client.ip" not in tags


def test_only_client_ip_set(llmobs, tracer):
    """Only http.client_ip on the root span — network.client.ip must not appear."""
    with tracer.trace("web.request", span_type=SpanTypes.WEB) as root_span:
        root_span.set_tag("http.client_ip", "1.2.3.4")
        with llmobs.llm("my_model", model_provider="openai") as span:
            pass

    tags = get_llmobs_tags(span)
    assert tags["http.client_ip"] == "1.2.3.4"
    assert "network.client.ip" not in tags


def test_non_root_llmobs_span_is_not_enriched(llmobs, tracer):
    """IP tags must not be added to nested LLMObs spans — only the root gets them."""
    with tracer.trace("web.request", span_type=SpanTypes.WEB) as root_span:
        root_span.set_tag("http.client_ip", "8.8.8.8")
        root_span.set_tag("network.client.ip", "10.0.0.1")
        with llmobs.workflow("outer") as root_llmobs:
            with llmobs.llm("my_model", model_provider="openai") as nested:
                pass

    root_tags = get_llmobs_tags(root_llmobs)
    nested_tags = get_llmobs_tags(nested)
    assert root_tags["http.client_ip"] == "8.8.8.8"
    assert "http.client_ip" not in nested_tags
    assert "network.client.ip" not in nested_tags


def test_no_ip_tags_on_root_span_results_in_no_tag(llmobs, tracer):
    """When the APM root span has no IP tags, no IP tags appear on the LLMObs span."""
    with tracer.trace("web.request", span_type=SpanTypes.WEB):
        with llmobs.llm("my_model", model_provider="openai") as span:
            pass

    tags = get_llmobs_tags(span)
    assert "http.client_ip" not in tags
    assert "network.client.ip" not in tags
