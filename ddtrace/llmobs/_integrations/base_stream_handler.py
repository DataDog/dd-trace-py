import sys
from abc import ABC, abstractmethod
from collections import defaultdict
from types import GeneratorType, AsyncGeneratorType

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
        self.chunks = self._initialize_chunk_storage()
    
    def _initialize_chunk_storage(self):
        return defaultdict(list)
    
    def add_span(self, span, kwargs):
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
        pass


class StreamHandler(BaseStreamHandler):
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
        pass


class AsyncStreamHandler(BaseStreamHandler):
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
        pass


class _ClassTracedStream(wrapt.ObjectProxy):
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
        self._handler = handler
        self._on_stream_created = on_stream_created
        if hasattr(self.__wrapped__, '__next__') or not hasattr(self.__wrapped__, '__iter__'):
            self._stream_iter = self.__wrapped__
        else:
            self._stream_iter = iter(self.__wrapped__)
    
    def __iter__(self):
        exc = None
        try:
            for chunk in self._stream_iter:
                self._handler.process_chunk(chunk, self._stream_iter)
                yield chunk
        except Exception as e:
            exc = e
            self._handler.handle_exception(e)
            raise
        finally:
            self._handler.finalize_stream(exc)
    
    def __enter__(self):
        if hasattr(self.__wrapped__, '__enter__'):
            result = self.__wrapped__.__enter__()
            # update iterator in case we are wrapping a stream manager
            if result is not self.__wrapped__:
                self._stream_iter = iter(result) if not hasattr(result, '__next__') else result
                traced_stream = _ClassTracedStream(result, self._handler, self._on_stream_created)
                if self._on_stream_created:
                    self._on_stream_created(traced_stream)
                return traced_stream
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if hasattr(self.__wrapped__, '__exit__'):
            return self.__wrapped__.__exit__(exc_type, exc_val, exc_tb)
    
    @property
    def handler(self):
        return self._handler


class _ClassTracedAsyncStream(wrapt.ObjectProxy):
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
        self._handler = handler
        self._on_stream_created = on_stream_created
        if hasattr(self.__wrapped__, '__anext__') or not hasattr(self.__wrapped__, '__aiter__'):
            self._async_stream_iter = self.__wrapped__
        else:
            self._async_stream_iter = aiter(self.__wrapped__)
    
    async def __aiter__(self):
        exc = None
        try:
            async for chunk in self._async_stream_iter:
                await self._handler.process_chunk(chunk, self._async_stream_iter)
                yield chunk
        except Exception as e:
            exc = e
            self._handler.handle_exception(e)
            raise
        finally:
            self._handler.finalize_stream(exc)
    
    async def __aenter__(self):
        if hasattr(self.__wrapped__, '__aenter__'):
            result = await self.__wrapped__.__aenter__()
            # update iterator in case we are wrapping a stream manager
            if result is not self.__wrapped__:
                self._async_stream_iter = aiter(result) if not hasattr(result, '__anext__') else result
                traced_stream = _ClassTracedAsyncStream(result, self._handler, self._on_stream_created)
                if self._on_stream_created:
                    self._on_stream_created(traced_stream)
                return traced_stream
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if hasattr(self.__wrapped__, '__aexit__'):
            return await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)
    
    @property
    def handler(self):
        return self._handler
    

class _GeneratorTracedStream:
    def __init__(self, wrapped, handler: StreamHandler):
        """
        Wrap a generator to trace the stream.
        
        Args:
            wrapped: The stream object to wrap
            handler: The StreamHandler instance to use for processing chunks
        """
        self._wrapped = wrapped
        self._handler = handler
    
    def __iter__(self):
        exc = None
        try:
            for chunk in self._wrapped:
                self._handler.process_chunk(chunk, self._wrapped)
                yield chunk
        except Exception as e:
            exc = e
            self._handler.handle_exception(e)
            raise
        finally:
            self._handler.finalize_stream(exc)
    
    @property
    def handler(self):
        return self._handler


class _AsyncGeneratorTracedStream:
    def __init__(self, wrapped, handler: AsyncStreamHandler):
        """
        Wrap an async generator to trace the stream.
        
        Args:
            wrapped: The stream object to wrap
            handler: The StreamHandler instance to use for processing chunks
        """
        self._wrapped = wrapped
        self._handler = handler
    
    async def __aiter__(self):
        exc = None
        try:
            async for chunk in self._wrapped:
                await self._handler.process_chunk(chunk, self._wrapped)
                yield chunk
        except Exception as e:
            exc = e
            self._handler.handle_exception(e)
            raise
        finally:
            self._handler.finalize_stream(exc)
    
    @property
    def handler(self):
        return self._handler


def make_traced_stream(wrapped, handler: StreamHandler, on_stream_created=None):
    if isinstance(wrapped, GeneratorType):
        return _GeneratorTracedStream(wrapped, handler)
    return _ClassTracedStream(wrapped, handler, on_stream_created)


def make_traced_async_stream(wrapped, handler: AsyncStreamHandler, on_stream_created=None):
    if isinstance(wrapped, AsyncGeneratorType):
        return _AsyncGeneratorTracedStream(wrapped, handler)
    return _ClassTracedAsyncStream(wrapped, handler, on_stream_created) 