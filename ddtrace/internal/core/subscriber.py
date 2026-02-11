from types import TracebackType
from typing import Generic
from typing import Optional
from typing import Tuple

from ddtrace.internal import core

from .events import EventType


class BaseSubscriber:
    """Base class for event subscribers.

    Subclasses that define ``event_name`` automatically register themselves to handle that event.
    This provides a clean pattern for handling events from the Events API (Event class).

    The subscriber pattern automatically:
    - Registers the subscriber when the class is defined
    - Collects all ``on_event`` methods from the inheritance chain and calls them in order (parent to child)

    Example:
        @dataclass
        class MyEvent(Event):
            event_name = "my.event"
            data: str

        class MySubscriber(BaseSubscriber):
            event_name = "my.event"

            @classmethod
            def on_event(cls, event_instance):
                print(f"Received: {event_instance.data}")

        # Subscriber is automatically registered, just dispatch:
        core.dispatch_event(MyEvent(data="hello"))
    """

    event_name: str
    _event_handlers: tuple = ()

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        cls._event_handlers = tuple(
            base_cls.on_event
            for base_cls in reversed(cls.__mro__[:-1])
            if issubclass(base_cls, BaseSubscriber)
            and "on_event" in base_cls.__dict__
            and base_cls is not BaseSubscriber
        )

        if "event_name" not in cls.__dict__:
            return

        core.on(
            cls.event_name,
            cls._on_event,
            name=f"{cls.__name__}",
        )

    @classmethod
    def on_event(cls, event_instance):
        """Override this method in child classes to handle the event.

        Args:
            event_instance: The Event instance that was dispatched
        """
        pass

    @classmethod
    def _on_event(cls, event_instance):
        """Internal handler that calls all _on_event methods from parent to children"""
        for handler in cls._event_handlers:
            handler(event_instance)


class BaseContextSubscriber(Generic[EventType]):
    """Base class for context event subscribers.

    Subclasses that define ``event_name`` automatically register themselves to handle context lifecycle events:
    - ``context.started.{event_name}`` when the context begins
    - ``context.ended.{event_name}`` when the context ends

    Example:
        @dataclass
        class MyContextEvent(ContextEvent):
            event_name = "my.context"

            user_id: str = event_field()

        class MyContextSubscriber(BaseContextSubscriber):
            event_name = "my.context"

            @classmethod
            def on_started(cls, ctx):
                user_id = ctx.get_item("user_id")
                print(f"Context started for user {user_id}")

            @classmethod
            def on_ended(cls, ctx, exc_info):
                if exc_info[1]:
                    print(f"Context ended with error: {exc_info[1]}")

        with core.context_with_event(MyContextEvent(url="/api", user_id="123")):
            pass
    """

    event_name: str
    _started_handlers: tuple = ()
    _ended_handlers: tuple = ()

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        cls._started_handlers = tuple(
            base_cls.on_started
            for base_cls in reversed(cls.__mro__[:-1])
            if issubclass(base_cls, BaseContextSubscriber)
            and "on_started" in base_cls.__dict__
            and base_cls is not BaseContextSubscriber
        )
        cls._ended_handlers = tuple(
            base_cls.on_ended
            for base_cls in reversed(cls.__mro__[:-1])
            if issubclass(base_cls, BaseContextSubscriber)
            and "on_ended" in base_cls.__dict__
            and base_cls is not BaseContextSubscriber
        )

        if "event_name" not in cls.__dict__:
            return

        core.on(
            f"context.started.{cls.event_name}",
            cls._on_context_started,
            name=f"{cls.__name__}.started",
        )
        core.on(
            f"context.ended.{cls.event_name}",
            cls._on_context_ended,
            name=f"{cls.__name__}.ended",
        )

    @classmethod
    def on_started(cls, ctx: core.ExecutionContext[EventType]):
        """Override this method in child classes to handle context start events.

        Args:
            ctx: The ExecutionContext instance
        """
        pass

    @classmethod
    def on_ended(
        cls,
        ctx: core.ExecutionContext[EventType],
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ):
        """Override this method in child classes to handle context end events.

        Args:
            ctx: The ExecutionContext instance
            exc_info: Tuple of (exception_type, exception_value, traceback) or (None, None, None)
        """
        pass

    @classmethod
    def _on_context_started(cls, ctx: core.ExecutionContext[EventType]) -> None:
        """Internal handler that calls all _on_context_started methods from parent to children"""
        for handler in cls._started_handlers:
            handler(ctx)

    @classmethod
    def _on_context_ended(
        cls,
        ctx: core.ExecutionContext[EventType],
        exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        """Internal handler that calls all _on_context_ended methods from parent to children"""
        for handler in cls._ended_handlers:
            handler(ctx, exc_info)
