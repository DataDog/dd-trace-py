"""
This file contains shared utilities for tracing streams in LLMobs integrations. Integrations should
implement a StreamHandler and / or AsyncStreamHandler subclass to be passed into the make_traced_stream
factory function along with the stream to wrap.
"""
from abc import ABC
from abc import abstractmethod
import sys
from typing import Union

import wrapt

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class BaseStreamHandler(ABC):
    def __init__(self, integration, span, args, kwargs, **options):
        self.integration = integration
        self.primary_span = span
        self.request_args = args
        self.request_kwargs = kwargs
        self.options = options

        self.spans = [(span, kwargs)]
        self.chunks = self.initialize_chunk_storage()

    def initialize_chunk_storage(self):
        return []

    def add_span(self, span, kwargs):
        """
        Add a span to the list of spans to be finished when the stream ends. This is useful for integrations that
        need to create multiple spans for a single stream like LiteLLM.
        """
        self.spans.append((span, kwargs))

    def handle_exception(self, exception):
        """
        Handle exceptions that occur during streaming.

        Default implementation sets exception info on the primary span.

        Args:
            exception: The exception that occurred
        """
        if self.primary_span:
            self.primary_span.set_exc_info(*sys.exc_info())

    @abstractmethod
    def finalize_stream(self, exception=None):
        """
        Finalize the stream and complete all spans.

        This method is called when the stream ends (successfully or with error).
        Implementations should:
        1. Process accumulated chunks into final response
        2. Set appropriate span tags
        3. Finish all spans
        """
        raise NotImplementedError("finalize_stream must be implemented by the subclass")


class StreamHandler(BaseStreamHandler):
    """
    Instances of StreamHandler and AsyncStreamHandler contain the logic for initializing chunk storage, processing
    chunks, handling exceptions, and finalizing a stream. The only methods that need to be implemented are
    process_chunk (a callback function that is called for each chunk in the stream) and finalize_stream (a
    callback function that is called when the stream ends). All other methods are optional and can be
    overridden if needed (for example, extra exception processing logic in handle_exception).

    Note that it is possible to pass in extra arguments via the options argument in case you need to access other
    information within the stream handler that is not covered by the existing arguments.
    """

    @abstractmethod
    def process_chunk(self, chunk, iterator=None):
        """
        Process a single chunk from the stream.

        This method is called for each chunk as it's received.
        Implementations should extract and store relevant data.

        Args:
            chunk: The chunk object from the stream
            iterator: The sync iterator object from the stream
        """
        raise NotImplementedError("process_chunk must be implemented by the subclass")


class AsyncStreamHandler(BaseStreamHandler):
    """
    Async version of StreamHandler.
    """

    @abstractmethod
    async def process_chunk(self, chunk, iterator=None):
        """
        Process a single chunk from the stream.

        This method is called for each chunk as it's received.
        Implementations should extract and store relevant data.

        Args:
            chunk: The chunk object from the stream
            iterator: The async iterator object from the stream
        """
        raise NotImplementedError("process_chunk must be implemented by the subclass")


class TracedStream(wrapt.ObjectProxy):
    """
    The TracedStream and AsyncTracedStream classes are wrappers around the underlying stream object that deal with
    iterating over the stream and calling the stream handler to process chunks, handle exceptions, and finalize the
    stream. Because each library's streamed response is different, these traced stream classes are meant to be generic
    enough to work with iterables, iterators, generators, and context managers.
    """

    def __init__(self, wrapped, handler: StreamHandler, on_stream_created=None):
        """
        Wrap a stream object to trace the stream.

        Args:
            wrapped: The stream object to wrap
            handler: The StreamHandler instance to use for processing chunks
            on_stream_created: In the case that the stream is created by a stream manager, this
                callback function will be called when the underlying stream is created in case
                modifications to the stream object are needed
        """
        super().__init__(wrapped)
        self._self_handler = handler
        self._self_on_stream_created = on_stream_created
        self._self_stream_iter = self.__wrapped__

    def __iter__(self):
        exc = None
        try:
            for chunk in self._self_stream_iter:
                self._self_handler.process_chunk(chunk, self._self_stream_iter)
                yield chunk
        except Exception as e:
            exc = e
            self._self_handler.handle_exception(e)
            raise
        finally:
            self._self_handler.finalize_stream(exc)

    def __next__(self):
        try:
            chunk = self._self_stream_iter.__next__()
            self._self_handler.process_chunk(chunk, self._self_stream_iter)
            return chunk
        except StopIteration:
            self._self_handler.finalize_stream()
            raise
        except Exception as e:
            self._self_handler.handle_exception(e)
            self._self_handler.finalize_stream(e)
            raise

    def __enter__(self):
        """
        Enter the context of the stream.

        If the stream is wrapped by a stream manager, the stream manager will be entered and the
        underlying stream will be wrapped in a TracedStream object. We retain a reference to the
        underlying stream object to be consumed.

        If the stream is not wrapped by a stream manager, the stream will be returned as is.
        """
        result = self.__wrapped__.__enter__()
        if result is self.__wrapped__:
            return self
        # update iterator in case we are wrapping a stream manager
        self._self_stream_iter = result
        traced_stream = TracedStream(result, self._self_handler, self._self_on_stream_created)
        if self._self_on_stream_created:
            self._self_on_stream_created(traced_stream)
        return traced_stream

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self.__wrapped__.__exit__(exc_type, exc_val, exc_tb)

    @property
    def handler(self):
        return self._self_handler


class TracedAsyncStream(wrapt.ObjectProxy):
    """
    Async version of TracedStream.
    """

    def __init__(self, wrapped, handler: AsyncStreamHandler, on_stream_created=None):
        """
        Wrap an async stream object to trace the stream.

        Args:
            wrapped: The stream object to wrap
            handler: The AsyncStreamHandler instance to use for processing chunks
            on_stream_created: In the case that the stream is created by a stream manager, this
                callback function will be called when the underlying stream is created in case
                modifications to the stream object are needed
        """
        super().__init__(wrapped)
        self._self_handler = handler
        self._self_on_stream_created = on_stream_created
        self._self_async_stream_iter = self.__wrapped__

    async def __aiter__(self):
        exc = None
        try:
            async for chunk in self._self_async_stream_iter:
                await self._self_handler.process_chunk(chunk, self._self_async_stream_iter)
                yield chunk
        except Exception as e:
            exc = e
            self._self_handler.handle_exception(e)
            raise
        finally:
            self._self_handler.finalize_stream(exc)

    async def __anext__(self):
        try:
            chunk = await self._self_async_stream_iter.__anext__()
            await self._self_handler.process_chunk(chunk, self._self_async_stream_iter)
            return chunk
        except StopAsyncIteration:
            self._self_handler.finalize_stream()
            raise
        except Exception as e:
            self._self_handler.handle_exception(e)
            self._self_handler.finalize_stream(e)
            raise

    async def __aenter__(self):
        """
        Enter the context of the stream.

        If the stream is wrapped by a stream manager, the stream manager will be entered and the
        underlying stream will be wrapped in a TracedAsyncStream object. We retain a reference to the
        underlying stream object to be consumed.

        If the stream is not wrapped by a stream manager, the stream will be returned as is.
        """
        result = await self.__wrapped__.__aenter__()
        if result is self.__wrapped__:
            return self
        # update iterator in case we are wrapping a stream manager
        self._self_async_stream_iter = result
        traced_stream = TracedAsyncStream(result, self._self_handler, self._self_on_stream_created)
        if self._self_on_stream_created:
            self._self_on_stream_created(traced_stream)
        return traced_stream

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)

    @property
    def handler(self):
        return self._self_handler


def make_traced_stream(wrapped, handler: Union[StreamHandler, AsyncStreamHandler], on_stream_created=None):
    """
    Create a TracedStream or TracedAsyncStream object from a stream object and a stream handler.
    """
    if isinstance(handler, AsyncStreamHandler):
        return TracedAsyncStream(wrapped, handler, on_stream_created)
    return TracedStream(wrapped, handler, on_stream_created)
