"""Core event on the _iterator of Anthropic and OpenAI SDK streams, which every SDK consumer reads.

Older SDKs set _iterator in __init__; newer ones make it on first use (a cached property).
"""

from functools import cached_property
import inspect
from typing import Any
from typing import Callable
from typing import Optional
import weakref

from ddtrace.internal import core


ANTHROPIC_STREAM_CREATED = "anthropic.stream.created"
OPENAI_STREAM_CREATED = "openai.stream.created"


class StreamEvents:
    """The event source of a new SDK stream; a listener may replace iterator.

    response is the httpx response of the stream, whose request is the one the SDK sent.
    """

    __slots__ = ("response", "iterator", "is_async")

    def __init__(self, response: Any, iterator: Any, is_async: bool) -> None:
        self.response = response
        self.iterator = iterator
        self.is_async = is_async


class _LazyEvents:
    """The SDK event iterator, made on the first read, after every subclass constructor has run."""

    __slots__ = ("_source", "_events")

    def __init__(self, source: Callable[[], Any]) -> None:
        self._source: Optional[Callable[[], Any]] = source
        self._events: Any = None

    def _resolve(self) -> Any:
        if self._source is not None:
            self._events, self._source = self._source(), None
        return self._events

    def __iter__(self) -> "_LazyEvents":
        return self

    def __next__(self) -> Any:
        return next(self._resolve())

    def __aiter__(self) -> "_LazyEvents":
        return self

    async def __anext__(self) -> Any:
        return await self._resolve().__anext__()


def _event_source(instance: Any) -> Any:
    """Return the event iterator of a new stream, lazy when the SDK makes it on first use."""
    if "_iterator" in vars(instance):
        return instance._iterator
    descriptor = inspect.getattr_static(type(instance), "_iterator", None)
    if not isinstance(descriptor, cached_property):
        return None
    # A weak reference: the stream keeps the replaced iterator, which must not keep the stream alive.
    stream = weakref.ref(instance)

    def source() -> Any:
        resolved = stream()
        if resolved is None:
            raise RuntimeError("the SDK stream was garbage collected before it was read")
        return descriptor.func(resolved)

    return _LazyEvents(source)


def traced_stream_init(created_event: str, is_async: bool) -> Callable[..., None]:
    """Wrapper for Stream.__init__ that dispatches created_event."""

    def wrapper(func: Callable[..., None], instance: Any, args: Any, kwargs: Any) -> None:
        func(*args, **kwargs)
        if not core.has_listeners(created_event):
            return
        iterator = _event_source(instance)
        if iterator is None:
            return
        events = StreamEvents(getattr(instance, "response", None), iterator, is_async)
        core.dispatch(created_event, (events,))
        if events.iterator is not iterator:
            instance._iterator = events.iterator

    return wrapper
