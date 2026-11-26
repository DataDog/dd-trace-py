"""Core event on the _iterator of Anthropic and OpenAI SDK streams, which every SDK consumer reads.

Older SDKs set _iterator in __init__; newer ones make it on first use (a cached property).
"""

from functools import cached_property
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
    """Return the event iterator of a new stream, for the listeners to wrap, or None.

    The SDK makes this iterator in one of two ways:

    1. Older SDKs set it in __init__ (self._iterator = self.__stream__()). It is in the instance
       dict, so it is returned as is.
    2. Newer SDKs (anthropic>=2) make it on first use, with a cached_property that copies the stream.
       Reading it here, in the middle of __init__, would copy the stream before a subclass finishes
       its own __init__: BedrockStream sets its AWS decoder after super().__init__(), and its events
       would be decoded with the wrong decoder. So a lazy iterator is returned instead: it calls the
       cached_property function on the first read, when every constructor has run.

    Any other layout returns None, and the stream is left as the SDK made it.
    """
    if "_iterator" in vars(instance):
        return instance._iterator
    # On the class, a cached_property returns itself and computes nothing.
    descriptor = getattr(type(instance), "_iterator", None)
    if not isinstance(descriptor, cached_property):
        return None
    # A weak reference: the stream keeps the iterator that replaces it, which must not keep the stream alive.
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
