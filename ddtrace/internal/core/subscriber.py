"""Explicit event subscribers with inherited handler composition."""

from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from functools import wraps
from importlib import import_module
import sys
from types import ModuleType
from types import TracebackType
from typing import Any
from typing import ClassVar
from typing import Generic
from typing import Optional
from typing import Protocol
from typing import TypeVar
from typing import cast

from ddtrace.internal import core

from . import event_hub
from .events import EventType


class RegistrableSubscriber(Protocol):
    def register(self) -> None: ...

    def unregister(self) -> None: ...


@dataclass(frozen=True)
class SubscriberBundle:
    """Subscribers exported by one lazily imported product module.

    Importing subscriber modules does not activate listeners. Resolved classes are cached;
    activation is valid until any core listener reset invalidates it.
    """

    module: str
    subscribers: tuple[str, ...]
    _resolved: Optional[tuple[RegistrableSubscriber, ...]] = field(default=None, init=False, compare=False, repr=False)
    _registered_generation: int = field(default=-1, init=False, compare=False, repr=False)

    def __reduce__(self) -> tuple[type["SubscriberBundle"], tuple[str, tuple[str, ...]]]:
        # Activation belongs to this interpreter, not to a serialized declaration.
        return type(self), (self.module, self.subscribers)

    def _resolve(self, module: ModuleType) -> tuple[RegistrableSubscriber, ...]:
        # Resolve the whole declaration before changing listeners, so a misspelled
        # subscriber name cannot leave an otherwise valid bundle partly registered.
        return tuple(cast(RegistrableSubscriber, getattr(module, name)) for name in self.subscribers)

    def register(self) -> None:
        """Ensure the bundle is active, importing its module only when requested."""
        self.ensure_registered()

    def ensure_registered(self) -> None:
        """Activate once between listener resets, retrying after registration failures."""
        with event_hub._registration_lock:
            if self._registered_generation == event_hub._listener_generation:
                return
            resolved = self._resolved

        # Imports may wait for another thread that is itself registering listeners.
        # Never hold the registration lock while acquiring a module import lock.
        module = import_module(self.module) if resolved is None else None

        with event_hub._registration_lock:
            if self._registered_generation == event_hub._listener_generation:
                return
            resolved = self._resolved
            if resolved is None:
                resolved = self._resolve(cast(ModuleType, module))
                object.__setattr__(self, "_resolved", resolved)
            generation = event_hub._listener_generation
            for subscriber in resolved:
                subscriber.register()
            # A registration callback can reset listeners reentrantly. Do not cache
            # success in that case, or if a callback raises before we get here.
            if generation == event_hub._listener_generation:
                object.__setattr__(self, "_registered_generation", generation)

    def unregister(self) -> None:
        """Remove the bundle's listeners after the owner has drained active contexts.

        Teardown never imports a previously unused product module. Products needing
        completion listeners for active requests must defer this operation.
        """
        with event_hub._registration_lock:
            object.__setattr__(self, "_registered_generation", -1)
            resolved = self._resolved
            if resolved is None:
                module = sys.modules.get(self.module)
                if module is None:
                    return
                resolved = self._resolve(module)
            for subscriber in resolved:
                subscriber.unregister()


_Setup = TypeVar("_Setup", bound=Callable[..., Any])


def requires_subscribers(*bundles: SubscriberBundle) -> Callable[[_Setup], _Setup]:
    """Activate lazy subscriber bundles before calling a synchronous setup function.

    Decoration does not import subscriber modules. Calls reuse active bundles and
    restore listeners after a reset. Registration failures propagate before setup runs.
    """

    def decorate(setup: _Setup) -> _Setup:
        @wraps(setup)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            for bundle in bundles:
                bundle.ensure_registered()
            return setup(*args, **kwargs)

        return cast(_Setup, wrapped)

    return decorate


class Subscriber:
    """Base class for event subscribers.

    Call register() to activate the events in event_names, or include the class in a
    SubscriberBundle. Class definition only collects inherited on_event handlers
    in parent-to-child order; it never installs listeners.

    Example:
        @dataclass
        class MyEvent(Event):
            event_name = "my.event"
            data: str

        class MySubscriber(Subscriber):
            event_names = ("my.event",)

            @classmethod
            def on_event(cls, event_instance):
                print(f"Received: {event_instance.data}")

        MySubscriber.register()
        core.dispatch_event(MyEvent(data="hello"))
    """

    event_names: Sequence[str]
    _event_handlers: tuple = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Collect inherited handlers in parent-to-child order without activating listeners."""
        super().__init_subclass__(**kwargs)

        cls._event_handlers = tuple(
            base_cls.on_event
            for base_cls in reversed(cls.__mro__[:-1])
            if issubclass(base_cls, Subscriber) and "on_event" in base_cls.__dict__ and base_cls is not Subscriber
        )

    @classmethod
    def register(cls) -> None:
        """Register this subscriber for its declared events."""
        for event_name in cls.event_names:
            core.on(event_name, cls._on_event, name=cls.__name__)

    @classmethod
    def unregister(cls) -> None:
        """Unregister this subscriber from its declared events."""
        for event_name in cls.event_names:
            core.reset_listeners(event_name, cls._on_event)

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


class ContextSubscriber(Generic[EventType]):
    """Base class for context event subscribers.

    Call register() to listen to these lifecycle events for each name in event_names:
    - ``context.started.{event_name}`` when the context begins
    - ``context.ended.{event_name}`` when the context ends

    Example:
        @dataclass
        class MyContextEvent(ContextEvent):
            event_name = "my.context"

            user_id: str = event_field()

        class MyContextSubscriber(ContextSubscriber):
            event_names = ("my.context",)

            @classmethod
            def on_started(cls, ctx):
                user_id = ctx.get_item("user_id")
                print(f"Context started for user {user_id}")

            @classmethod
            def on_ended(cls, ctx, exc_info):
                if exc_info[1]:
                    print(f"Context ended with error: {exc_info[1]}")

        MyContextSubscriber.register()
        with core.context_with_event(MyContextEvent(url="/api", user_id="123")):
            pass
    """

    event_names: ClassVar[Sequence[str]]
    _started_handlers: tuple = ()
    _ended_handlers: tuple = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Collect inherited handlers in parent-to-child order without activating listeners."""

        super().__init_subclass__(**kwargs)

        cls._started_handlers = tuple(
            base_cls.on_started
            for base_cls in reversed(cls.__mro__[:-1])
            if issubclass(base_cls, ContextSubscriber)
            and "on_started" in base_cls.__dict__
            and base_cls is not ContextSubscriber
        )
        cls._ended_handlers = tuple(
            base_cls.on_ended
            for base_cls in reversed(cls.__mro__[:-1])
            if issubclass(base_cls, ContextSubscriber)
            and "on_ended" in base_cls.__dict__
            and base_cls is not ContextSubscriber
        )

    @classmethod
    def register(cls) -> None:
        """Register this subscriber for its declared context events."""
        for event_name in cls.event_names:
            core.on(
                f"context.started.{event_name}",
                cls._on_context_started,
                name=f"{cls.__name__}.started",
            )
            core.on(
                f"context.ended.{event_name}",
                cls._on_context_ended,
                name=f"{cls.__name__}.ended",
            )

    @classmethod
    def unregister(cls) -> None:
        """Unregister this subscriber from its declared context events."""
        for event_name in cls.event_names:
            core.reset_listeners(f"context.started.{event_name}", cls._on_context_started)
            core.reset_listeners(f"context.ended.{event_name}", cls._on_context_ended)

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
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
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
        exc_info: tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
    ) -> None:
        """Internal handler that calls all _on_context_ended methods from parent to children"""
        for handler in cls._ended_handlers:
            handler(ctx, exc_info)
