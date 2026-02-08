"""
Events API — an abstraction above the Core API for type-safe event dispatching.

Events enforce the arguments that can be passed when dispatching, and allow better
correlation between dispatch sites and handlers.

Example using ``context_with_event``::

    @context_event
    class MyEvent(ContextEvent):
        event_name = "my.event"

        foo: str  # automatically converted to event_field by @context_event
        bar: str = event_field(in_context=True)  # stored in ExecutionContext

        @classmethod
        def _on_context_started(cls, ctx, call_trace=True, **kwargs):
            print(ctx.get_item("bar"))

    with core.context_with_event(MyEvent(foo="hello", bar="world")):
        pass

Example using ``TracedEvent`` (pure data, tracing handled by subscribers)::

    @context_event
    class HttpClientRequestEvent(TracedEvent):
        event_name = "http.client.request"
        url: str = event_field(in_context=True)
        method: str = event_field(in_context=True)
"""

from dataclasses import MISSING
from dataclasses import dataclass
from dataclasses import field
from dataclasses import fields
import sys
from types import TracebackType
from typing import Any
from typing import Dict
from typing import Optional
from typing import Tuple

from ddtrace.internal import core


def context_event(cls: Any) -> Any:
    """Decorator that converts a class into a dataclass with automatic event_field() defaults.

    By automatically applying event_field() to fields without defaults, this decorator allows child classes
    to define required fields naturally while maintaining compatibility with parent class fields that have defaults.

    Example:
        @context_event
        class MyEvent(ContextEvent):
            url: str  # Automatically gets event_field() applied
    """

    annotations = cls.__dict__.get("__annotations__", {})

    # For each annotated field without a default, set it to event_field()
    for name, _ in annotations.items():
        # Only apply `event_field()` for fields that don't define a default value
        # in the class body.
        if name not in cls.__dict__:
            setattr(cls, name, event_field())

    return dataclass(cls)


def event_field(
    default: Any = MISSING,
    default_factory: Any = MISSING,
    in_context: bool = False,
) -> Any:
    """Creates a dataclass field with special handling for event context data and Python version compatibility.
       Event fields ensure retro compatibility as python 3.9 does not support kw_only which is
       required to allow a child class to have attributes without value.

    Args:
        default: Default value for the field
        default_factory: Factory function to generate default values
        in_context: Whether this field should be included in the ExecutionContext dict
    """
    if default is not MISSING and default_factory is not MISSING:
        raise ValueError("Cannot specify both default and default_factory")

    kwargs: Dict[str, Any] = {"repr": in_context}
    if default is not MISSING:
        kwargs["default"] = default
    elif default_factory is not MISSING:
        kwargs["default_factory"] = default_factory

    # Python 3.9: Give fields without defaults a None default to work around
    # field ordering constraints with inheritance
    if sys.version_info < (3, 10):
        if default is MISSING and default_factory is MISSING:
            kwargs["default"] = None
    else:
        kwargs["kw_only"] = True

    return field(**kwargs)


@dataclass
class ContextEvent:
    """Base class for context-based events used with ``core.context_with_event()``.

    Subclasses that define ``event_name`` auto-register their ``_on_context_started``
    and ``_on_context_ended`` hooks via ``__init_subclass__``.

    Should be decorated with ``@context_event``.
    """

    event_name: str = field(init=False, repr=False)

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        if "event_name" not in cls.__dict__:
            return

        core.on(
            f"context.started.{cls.event_name}",
            cls._registered_context_started,
            name=f"{cls.__name__}_started",
        )
        core.on(
            f"context.ended.{cls.event_name}",
            cls._registered_context_ended,
            name=f"{cls.__name__}_ended",
        )

    def create_event_context(self):
        """Convert this event instance into a dict for creating an ExecutionContext.
        Only event_field marked with in_context=True will be included.
        """
        return {f.name: getattr(self, f.name) for f in fields(self) if f.repr}

    @classmethod
    def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
        """Override in subclasses to handle context start."""
        pass

    @classmethod
    def _on_context_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        """Override in subclasses to handle context end."""
        pass

    @classmethod
    def _registered_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
        # _on_context_started will be called in order from parent class to children classes.
        for base_cls in reversed(cls.__mro__[:-1]):
            if issubclass(base_cls, ContextEvent) and "_on_context_started" in base_cls.__dict__:
                base_cls._on_context_started(ctx, call_trace, **kwargs)

    @classmethod
    def _registered_context_ended(
        cls,
        ctx: core.ExecutionContext,
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        # _on_context_ended will be called in order from parent class to children classes.
        for base_cls in reversed(cls.__mro__[:-1]):
            if issubclass(base_cls, ContextEvent) and "_on_context_ended" in base_cls.__dict__:
                base_cls._on_context_ended(ctx, exc_info)


@dataclass
class TracedEvent(ContextEvent):
    """Base for events that carry span metadata — pure data, no tracing logic.

    Domain-specific subclasses add their own fields. Tracing logic lives in
    subscriber classes (ddtrace/_trace/trace_subscribers/).

    TracedEvent subclasses do NOT auto-register ContextEvent handlers — subscribers
    handle all start/end logic independently.
    """

    def __init_subclass__(cls, **kwargs):
        # Skip ContextEvent's auto-registration of _registered_context_started/ended.
        # TracedEvent subclasses rely on TracingSubscriber for all event handling.
        pass

    span_name: str = event_field(in_context=True)
    span_type: str = event_field(in_context=True)
    call_trace: bool = event_field(default=True, in_context=True)
    service: Optional[str] = event_field(default=None, in_context=True)
    resource: Optional[str] = event_field(default=None, in_context=True)
    tags: Dict[str, str] = event_field(default_factory=dict, in_context=True)
    measured: bool = event_field(default=True, in_context=True)
    integration_config: object = event_field(in_context=True)
