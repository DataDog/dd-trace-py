"""
Base tracing plugin class.

All tracing plugins inherit from TracingPlugin. This provides:
- Event subscription based on package/operation
- Span lifecycle management
- Common utilities for span creation
"""

from abc import ABC
from abc import abstractmethod
from typing import TYPE_CHECKING
from typing import Any
from typing import Optional
from typing import Tuple


if TYPE_CHECKING:
    from ddtrace._trace.span import Span
    from ddtrace.internal.core import ExecutionContext


class TracingPlugin(ABC):
    """
    Root base class for all tracing plugins.

    Subclasses define `package` and `operation` to auto-subscribe to events.
    The event pattern is: context.started.{package}.{operation}

    Example:
        class MyPlugin(TracingPlugin):
            package = "mylib"
            operation = "execute"

        # This subscribes to:
        # - context.started.mylib.execute
        # - context.ended.mylib.execute
    """

    # --- Required attributes (override in subclasses) ---

    @property
    @abstractmethod
    def package(self) -> str:
        """
        Package name (e.g., 'asyncpg', 'flask', 'kafka').

        This should match the integration name used in config.
        """
        pass

    @property
    @abstractmethod
    def operation(self) -> str:
        """
        Operation name (e.g., 'execute', 'request', 'produce').

        Combined with package, forms the event name: {package}.{operation}
        """
        pass

    # --- Optional attributes (override as needed) ---

    kind: Optional[str] = None  # SpanKind: "client", "server", "producer", "consumer"
    type: str = "custom"  # Span type category: "web", "sql", "storage", "worker"

    # --- Computed properties ---

    @property
    def event_name(self) -> str:
        """Full event name: {package}.{operation}"""
        return f"{self.package}.{self.operation}"

    @property
    def integration_config(self) -> Any:
        """
        Get the integration's config from ddtrace.config.

        Returns the config object or empty dict if not found.
        """
        from ddtrace import config

        return getattr(config, self.package, {})

    # --- Registration ---

    def register(self) -> None:
        """
        Register this plugin's event handlers with the core system.

        Subscribes to:
        - context.started.{event_name} -> on_start
        - context.ended.{event_name} -> on_finish
        """
        from ddtrace.internal import core

        core.on(f"context.started.{self.event_name}", self._on_started)
        core.on(f"context.ended.{self.event_name}", self._on_ended)

    # --- Internal event handlers ---

    def _on_started(self, ctx: "ExecutionContext") -> None:
        """Internal handler for context.started event."""
        self.on_start(ctx)

    def _on_ended(
        self,
        ctx: "ExecutionContext",
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[Any]],
    ) -> None:
        """Internal handler for context.ended event."""
        self.on_finish(ctx, exc_info)

    # --- Override these in subclasses ---

    def on_start(self, ctx: "ExecutionContext") -> None:
        """
        Called when the traced context starts.

        Override this to create spans and set initial tags.
        The span should be stored on ctx.span.

        Args:
            ctx: The execution context containing pin, event-specific context, etc.
        """
        pass

    def on_finish(
        self,
        ctx: "ExecutionContext",
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[Any]],
    ) -> None:
        """
        Called when the traced context ends.

        Override this to set final tags and finish the span.
        Default implementation handles error tagging and span.finish().

        Args:
            ctx: The execution context
            exc_info: Tuple of (exc_type, exc_value, traceback) or (None, None, None)
        """
        span = ctx.span
        if not span:
            return

        # Set error info if an exception occurred
        if exc_info[0] is not None:
            span.set_exc_info(*exc_info)

        span.finish()

    # --- Utility methods ---

    def start_span(
        self,
        ctx: "ExecutionContext",
        name: str,
        **options: Any,
    ) -> Optional["Span"]:
        """
        Create and configure a span with common tags.

        This utility method:
        - Gets the tracer from the pin
        - Creates a span with the given name and options
        - Sets component and span.kind tags
        - Stores the span on ctx.span

        Args:
            ctx: The execution context (must contain 'pin')
            name: Span operation name
            **options: Additional options passed to tracer.trace()
                       (service, resource, span_type, etc.)

        Returns:
            The created span, or None if tracing is disabled
        """
        from ddtrace.constants import SPAN_KIND
        from ddtrace.internal.constants import COMPONENT

        pin = ctx.get_item("pin")
        if not pin or not pin.enabled():
            return None

        # Create the span
        span = pin.tracer.trace(name, **options)

        # Set common tags
        integration_name = self.package
        if hasattr(self.integration_config, "integration_name"):
            integration_name = self.integration_config.integration_name

        span._set_tag_str(COMPONENT, integration_name)

        if self.kind:
            span._set_tag_str(SPAN_KIND, self.kind)

        # Store on context
        ctx.span = span

        return span

    def get_service(self, ctx: "ExecutionContext") -> Optional[str]:
        """
        Get the service name for this span.

        Default implementation uses ext_service helper.
        Override in subclasses for custom service resolution.

        Args:
            ctx: The execution context

        Returns:
            Service name or None to use default
        """
        from ddtrace.contrib.internal.trace_utils import ext_service

        pin = ctx.get_item("pin")
        if not pin:
            return None

        return ext_service(pin, self.integration_config)
