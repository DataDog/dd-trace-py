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


class MessagingContextPropagationHook(BaseHook):
    """
    Handles distributed context propagation for messaging integrations.

    - For consume events: extracts context from incoming message headers (on_event)
    - For send events: injects context into outgoing message headers (on_span_start)
    """

    event_names = [
        "integration.event.messaging.consume",
        "integration.event.messaging.send",
    ]

    def _is_propagation_enabled(self, event: "IntegrationEvent") -> bool:
        """Check if distributed tracing is enabled for this integration."""
        integration_config = getattr(dd_config, event.integration.name, None)
        return not integration_config or getattr(integration_config, "distributed_tracing_enabled", True)

    def _get_adapter(self, event: "IntegrationEvent"):
        """Get the carrier adapter for this integration."""
        adapter_class = event.integration.carrier_adapter or DefaultHeaderAdapter
        return adapter_class()

    def on_event(self, event: "IntegrationEvent") -> None:
        """Extract trace context from incoming message headers (consume only)."""
        if event.event_type != "consume" or not self._is_propagation_enabled(event):
            return

        headers = self._get_adapter(event).extract(event.payload)
        if headers:
            parent = HTTPPropagator.extract(headers)
            event.extracted_context = parent
            core.set_item("extracted_parent_ctx", parent)

    def on_span_start(self, event: "IntegrationEvent", span: "Span") -> None:
        """Inject span's trace context into outgoing message headers (send only)."""
        if event.event_type != "send" or not self._is_propagation_enabled(event):
            return

        self._get_adapter(event).inject(event.payload, span.context)


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
_context_propagation_hook = None
_span_tagging_hook = None


def register_messaging_hooks() -> None:
    """Register all messaging hooks."""
    global _context_propagation_hook, _span_tagging_hook

    if _context_propagation_hook is None:
        _context_propagation_hook = MessagingContextPropagationHook()
        _context_propagation_hook.register()

    if _span_tagging_hook is None:
        _span_tagging_hook = MessagingSpanTaggingHook()
        _span_tagging_hook.register()


def unregister_messaging_hooks() -> None:
    """Unregister all messaging hooks (for testing)."""
    global _context_propagation_hook, _span_tagging_hook

    if _context_propagation_hook is not None:
        _context_propagation_hook.unregister()
        _context_propagation_hook = None

    if _span_tagging_hook is not None:
        _span_tagging_hook.unregister()
        _span_tagging_hook = None
