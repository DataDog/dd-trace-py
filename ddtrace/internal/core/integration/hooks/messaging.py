"""
Messaging integration hooks.

These hooks handle distributed context propagation and span tagging
for messaging integrations (Kafka, RabbitMQ, SQS, etc.).
"""

from typing import TYPE_CHECKING

from ddtrace import config as dd_config
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.constants import MESSAGING_SYSTEM
from ddtrace.propagation.http import HTTPPropagator

from ..carrier import DefaultHeaderAdapter
from .base import BaseHook


if TYPE_CHECKING:
    from ddtrace._trace.span import Span

    from ..event import IntegrationEvent


class MessagingContextExtractionHook(BaseHook):
    """
    Extracts distributed trace context from incoming messages.

    Runs before span creation (on_event) so the extracted context
    can be used as the parent for the new span.
    """

    event_names = [
        "integration.event.messaging.consume",
    ]

    def on_event(self, event: "IntegrationEvent") -> None:
        """Extract trace context from incoming message headers."""
        # Check if distributed tracing is enabled for this integration
        integration_config = getattr(dd_config, event.integration.name, None)
        if integration_config and not getattr(integration_config, "distributed_tracing_enabled", True):
            return

        integration = event.integration
        adapter_class = integration.carrier_adapter or DefaultHeaderAdapter
        adapter = adapter_class()

        headers = adapter.extract(event.payload)
        if headers:
            parent = HTTPPropagator.extract(headers)
            event.extracted_context = parent
            core.set_item("extracted_parent_ctx", parent)


class MessagingContextInjectionHook(BaseHook):
    """
    Injects distributed trace context into outgoing messages.

    Runs after span creation (on_span_start) so we have a span
    context to inject into the message headers.
    """

    event_names = [
        "integration.event.messaging.send",
    ]

    def on_span_start(self, event: "IntegrationEvent", span: "Span") -> None:
        """Inject span's trace context into outgoing message headers."""
        # Check if distributed tracing is enabled for this integration
        integration_config = getattr(dd_config, event.integration.name, None)
        if integration_config and not getattr(integration_config, "distributed_tracing_enabled", True):
            return

        integration = event.integration
        adapter_class = integration.carrier_adapter or DefaultHeaderAdapter
        adapter = adapter_class()

        # Inject the span's context into the payload headers
        adapter.inject(event.payload, span.context)


class MessagingSpanTaggingHook(BaseHook):
    """
    Applies tags to messaging spans from payload data.

    Default tags are applied from standard payload keys via a mapping.
    The mapping defines which payload keys map to which tag names, and
    whether they are required (logs warning if missing) or optional.

    Additional tags can be provided via:
    - payload["span_tags"] - applied on span start
    - payload["span_metrics"] - applied on span start
    - payload["span_tags_deferred"] - applied on span finish (for result-based tags)
    """

    # Register for all messaging events at category level
    event_names = [
        "integration.event.messaging",
    ]

    # Default mapping of payload keys to (tag_name, required)
    # Format: payload_key -> (tag_name, required) or just tag_name (optional)
    DEFAULT_TAG_MAPPING = {
        "topic": ("messaging.destination.name", True),
        "partition": "messaging.kafka.partition",
        "offset": "messaging.kafka.message.offset",
        "message_key": "messaging.kafka.message.key",
        "group_id": "messaging.kafka.consumer.group",
        "consumer_group": "messaging.kafka.consumer.group",
    }

    def on_span_start(self, event: "IntegrationEvent", span: "Span") -> None:
        """Apply default and additional tags from payload to the span."""
        payload = event.payload
        integration = event.integration

        # Apply standard messaging tags based on integration metadata
        span._set_tag_str(MESSAGING_SYSTEM, integration.name)

        # Set span kind based on role
        if integration.role == "producer":
            span._set_tag_str(SPAN_KIND, SpanKind.PRODUCER)
        elif integration.role == "consumer":
            span._set_tag_str(SPAN_KIND, SpanKind.CONSUMER)

        # Set component from config
        integration_config = getattr(dd_config, integration.name, None)
        if integration_config:
            component = getattr(integration_config, "integration_name", None)
            if component:
                span._set_tag_str(COMPONENT, component)

        # Apply default tags from standard payload keys
        self._apply_tags_from_payload(span, payload, self.DEFAULT_TAG_MAPPING, context=integration.name)

        # Apply additional tags from span_tags dict (integration-specific overrides/additions)
        self._apply_tags(span, payload.get("span_tags", {}))

        # Apply metrics from span_metrics dict
        self._apply_metrics(span, payload.get("span_metrics", {}))

    def on_span_finish(self, event: "IntegrationEvent", span: "Span") -> None:
        """Apply deferred tags (result-based) to the span."""
        # Apply deferred tags (set after function execution completes)
        self._apply_tags(span, event.payload.get("span_tags_deferred", {}))


# Singleton instances for registration
_context_extraction_hook = None
_context_injection_hook = None
_span_tagging_hook = None


def register_messaging_hooks() -> None:
    """Register all messaging hooks."""
    global _context_extraction_hook, _context_injection_hook, _span_tagging_hook

    if _context_extraction_hook is None:
        _context_extraction_hook = MessagingContextExtractionHook()
        _context_extraction_hook.register()

    if _context_injection_hook is None:
        _context_injection_hook = MessagingContextInjectionHook()
        _context_injection_hook.register()

    if _span_tagging_hook is None:
        _span_tagging_hook = MessagingSpanTaggingHook()
        _span_tagging_hook.register()


def unregister_messaging_hooks() -> None:
    """Unregister all messaging hooks (for testing)."""
    global _context_extraction_hook, _context_injection_hook, _span_tagging_hook

    if _context_extraction_hook is not None:
        _context_extraction_hook.unregister()
        _context_extraction_hook = None

    if _context_injection_hook is not None:
        _context_injection_hook.unregister()
        _context_injection_hook = None

    if _span_tagging_hook is not None:
        _span_tagging_hook.unregister()
        _span_tagging_hook = None
