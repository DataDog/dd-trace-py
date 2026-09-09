from time import time_ns

from ddtrace import config
from ddtrace.contrib._events.messaging import MessagingActionEvent
from ddtrace.contrib._events.messaging import MessagingProcessEvent
from ddtrace.contrib._events.messaging import MessagingProducerEvent
from ddtrace.contrib._events.messaging import MessagingReceiveEvent
from ddtrace.ext import SpanKind
from ddtrace.internal import core
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.trace import tracer
from tests.utils import override_global_config


def _integration_config(distributed_tracing=True):
    return IntegrationConfig(config, "messaging_test", {"distributed_tracing": distributed_tracing})


def _headers():
    return {
        "x-datadog-trace-id": "1234",
        "x-datadog-parent-id": "5678",
        "x-datadog-sampling-priority": "1",
    }


def _event_kwargs(integration_config):
    return {
        "operation": "broker.operation",
        "resource": "broker.operation",
        "component": "messaging_test",
        "integration_config": integration_config,
        "messaging_system": "broker",
        "semantic_operation": "receive",
        "destination": "orders",
    }


def test_producer_injection_and_generic_attributes(test_spans):
    headers = {"application": "preserved"}
    event = MessagingProducerEvent(
        distributed_headers=headers,
        **_event_kwargs(_integration_config()),
    )

    with core.context_with_event(event):
        pass

    span = test_spans.spans[0]
    assert headers["application"] == "preserved"
    assert headers["x-datadog-trace-id"] == str(span.trace_id & ((1 << 64) - 1))
    assert span.get_tag("messaging.system") == "broker"
    assert span.get_tag("messaging.operation") == "receive"
    assert span.get_tag("messaging.destination.name") == "orders"


def test_producer_injection_disabled(test_spans):
    headers = {"application": "preserved"}
    event = MessagingProducerEvent(
        distributed_headers=headers,
        **_event_kwargs(_integration_config(False)),
    )

    with core.context_with_event(event):
        pass

    assert headers == {"application": "preserved"}


def test_process_uses_extracted_parent(test_spans):
    event = MessagingProcessEvent(request_headers=_headers(), **_event_kwargs(_integration_config()))

    with core.context_with_event(event):
        pass

    span = test_spans.spans[0]
    assert span.trace_id == 1234
    assert span.parent_id == 5678


def test_receive_uses_ambient_parent_and_span_link(test_spans):
    event = MessagingReceiveEvent(
        request_headers=_headers(),
        propagation_as_span_links=True,
        **_event_kwargs(_integration_config()),
    )

    with override_global_config({"_propagation_as_span_links": {"unrelated"}}):
        with tracer.trace("ambient") as ambient:
            with core.context_with_event(event):
                pass

    receive = next(span for span in test_spans.spans if span.name == "broker.operation")
    assert receive.parent_id == ambient.span_id
    assert [(link.trace_id, link.span_id) for link in receive._get_links()] == [(1234, 5678)]


def test_receive_missing_or_malformed_headers_falls_back_to_ambient(test_spans):
    for headers in ({}, {"x-datadog-trace-id": "invalid"}):
        with tracer.trace("ambient") as ambient:
            event = MessagingReceiveEvent(
                request_headers=headers,
                propagation_as_span_links=True,
                **_event_kwargs(_integration_config()),
            )
            with core.context_with_event(event):
                pass
            assert tracer.current_span() is ambient

    receives = [span for span in test_spans.spans if span.name == "broker.operation"]
    assert all(not span._get_links() for span in receives)


def test_receive_start_time_is_backdated(test_spans):
    start_ns = time_ns() - 50_000_000
    event = MessagingReceiveEvent(start_ns=start_ns, **_event_kwargs(_integration_config(False)))

    with core.context_with_event(event):
        pass

    span = test_spans.spans[0]
    assert span.start_ns == start_ns
    assert span.duration_ns >= 50_000_000


def test_action_contract(test_spans):
    kwargs = _event_kwargs(_integration_config(False))
    kwargs.pop("semantic_operation")
    event = MessagingActionEvent(
        action="ack",
        **kwargs,
    )

    with core.context_with_event(event):
        pass

    span = test_spans.spans[0]
    assert span.get_tag("span.kind") == SpanKind.CLIENT
    assert span.get_tag("messaging.operation") == "ack"
