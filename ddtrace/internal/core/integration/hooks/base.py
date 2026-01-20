"""
Base hook class for integration hooks.

Hooks register themselves with core.on() for specific event names,
leveraging the existing event dispatch infrastructure.
"""

from typing import TYPE_CHECKING
from typing import Any
from typing import Dict
from typing import List
from typing import Tuple
from typing import Union

from ddtrace.internal import core
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


if TYPE_CHECKING:
    from ddtrace._trace.span import Span

    from ..event import IntegrationEvent


class BaseHook:
    """
    Base class for all integration hooks.

    Hooks self-register with core.on() for specific event names.
    This leverages core's O(1) event dispatch instead of custom lookup.

    Event naming convention:
        integration.event.{category}.{event_type}
        integration.event.{category}  (category-level)

    Example:
        integration.event.messaging.send
        integration.event.messaging.consume
        integration.event.messaging  (all messaging events)

    Subclasses should:
        1. Set `event_names` to specify which events to handle
        2. Override on_event, on_span_start, or on_span_finish as needed
        3. Call register() to activate the hook
    """

    # Event names this hook should handle
    # Examples: ["integration.event.messaging.send", "integration.event.messaging.consume"]
    event_names: List[str] = []

    def __init__(self):
        self._registered = False

    def register(self) -> None:
        """
        Register this hook with core event dispatch.

        Call this after instantiation to activate the hook.
        """
        if self._registered:
            return

        for event_name in self.event_names:
            # Register for the main event (pre-span)
            if self._has_on_event():
                core.on(event_name, self._handle_event, name=f"{self.__class__.__name__}.on_event")

            # Register for span start
            if self._has_on_span_start():
                core.on(
                    f"{event_name}.span_start",
                    self._handle_span_start,
                    name=f"{self.__class__.__name__}.on_span_start",
                )

            # Register for span finish
            if self._has_on_span_finish():
                core.on(
                    f"{event_name}.span_finish",
                    self._handle_span_finish,
                    name=f"{self.__class__.__name__}.on_span_finish",
                )

        self._registered = True

    def unregister(self) -> None:
        """Unregister this hook from core event dispatch."""
        if not self._registered:
            return

        for event_name in self.event_names:
            if self._has_on_event():
                core.reset_listeners(event_name, self._handle_event)
            if self._has_on_span_start():
                core.reset_listeners(f"{event_name}.span_start", self._handle_span_start)
            if self._has_on_span_finish():
                core.reset_listeners(f"{event_name}.span_finish", self._handle_span_finish)

        self._registered = False

    def _has_on_event(self) -> bool:
        """Check if subclass overrides on_event."""
        return self.__class__.on_event is not BaseHook.on_event

    def _has_on_span_start(self) -> bool:
        """Check if subclass overrides on_span_start."""
        return self.__class__.on_span_start is not BaseHook.on_span_start

    def _has_on_span_finish(self) -> bool:
        """Check if subclass overrides on_span_finish."""
        return self.__class__.on_span_finish is not BaseHook.on_span_finish

    def _handle_event(self, event: "IntegrationEvent") -> None:
        """Internal handler that wraps on_event."""
        self.on_event(event)

    def _handle_span_start(self, event: "IntegrationEvent", span: "Span") -> None:
        """Internal handler that wraps on_span_start."""
        self.on_span_start(event, span)

    def _handle_span_finish(self, event: "IntegrationEvent", span: "Span") -> None:
        """Internal handler that wraps on_span_finish."""
        self.on_span_finish(event, span)

    def on_event(self, event: "IntegrationEvent") -> None:
        """
        Called before span creation. Use for:
        - Context propagation (extract/inject)
        - Payload normalization
        - Data extraction

        Args:
            event: The integration event with payload data
        """
        pass

    def on_span_start(self, event: "IntegrationEvent", span: "Span") -> None:
        """
        Called after span creation. Use for:
        - Span tagging
        - Metrics
        - Enrichment

        Args:
            event: The integration event with payload data
            span: The span that was created
        """
        pass

    def on_span_finish(self, event: "IntegrationEvent", span: "Span") -> None:
        """
        Called before span finishes. Use for:
        - Final tagging
        - Response processing
        - Cleanup

        Args:
            event: The integration event with payload data
            span: The span that is finishing
        """
        pass

    def _apply_tags(self, span: "Span", tags: Dict[str, Any]) -> None:
        """
        Apply a dictionary of tags to a span.

        Handles different value types appropriately:
        - None values are skipped
        - Numeric values (int, float) use set_tag
        - All other values are converted to str and use _set_tag_str

        Args:
            span: The span to apply tags to
            tags: Dictionary of tag_name -> tag_value
        """
        for tag_name, tag_value in tags.items():
            if tag_value is None:
                continue
            if isinstance(tag_value, (int, float)):
                span.set_tag(tag_name, tag_value)
            else:
                span._set_tag_str(tag_name, str(tag_value))

    def _apply_tags_from_payload(
        self,
        span: "Span",
        payload: Dict[str, Any],
        mapping: Dict[str, Union[str, Tuple[str, bool]]],
        context: str = "",
    ) -> None:
        """
        Apply tags to a span by mapping payload keys to tag names.

        This provides a clean, declarative way to define default tags:
            mapping = {
                "topic": ("messaging.destination.name", True),   # required
                "partition": ("messaging.kafka.partition", False),  # optional
                "offset": "messaging.kafka.message.offset",  # optional (shorthand)
            }
            self._apply_tags_from_payload(span, payload, mapping, context="kafka")

        Args:
            span: The span to apply tags to
            payload: The event payload containing values
            mapping: Dictionary of payload_key -> tag_name or (tag_name, required)
                     If just a string, defaults to optional (required=False)
            context: Optional context string for warning messages (e.g., integration name)
        """
        tags = {}
        for payload_key, tag_config in mapping.items():
            # Parse tag config - can be string or (string, bool) tuple
            if isinstance(tag_config, tuple):
                tag_name, required = tag_config
            else:
                tag_name = tag_config
                required = False

            value = payload.get(payload_key)
            if value is not None:
                tags[tag_name] = value
            elif required:
                ctx_str = f" [{context}]" if context else ""
                log.warning(
                    "Required payload key '%s' missing for tag '%s'%s",
                    payload_key,
                    tag_name,
                    ctx_str,
                )
        self._apply_tags(span, tags)

    def _apply_metrics(self, span: "Span", metrics: Dict[str, Any]) -> None:
        """
        Apply a dictionary of metrics to a span.

        Args:
            span: The span to apply metrics to
            metrics: Dictionary of metric_name -> metric_value
        """
        for metric_name, metric_value in metrics.items():
            if metric_value is not None:
                span.set_metric(metric_name, metric_value)


def register_hook(hook: BaseHook) -> BaseHook:
    """
    Register a hook instance.

    Can be used as a simple function or as a decorator pattern.

    Args:
        hook: The hook instance to register

    Returns:
        The registered hook instance
    """
    hook.register()
    return hook
