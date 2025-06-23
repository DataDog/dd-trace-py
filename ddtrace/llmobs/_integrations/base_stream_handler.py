import sys
from abc import ABC, abstractmethod
from collections import defaultdict

import wrapt

from ddtrace.internal.logger import get_logger

log = get_logger(__name__)


class BaseStreamHandler(ABC):
    def __init__(self, integration, span, kwargs, **options):
        self.integration = integration
        self.primary_span = span
        self.request_kwargs = kwargs
        self.options = options
        
        self.spans = [(span, kwargs)]
        self.chunks = self._initialize_chunk_storage()
        self.metadata = {}
        self.is_finished = False
    
    def _initialize_chunk_storage(self):
        return defaultdict(list)
    
    def add_span(self, span, kwargs):
        self.spans.append((span, kwargs))
    
    def handle_exception(self, exception):
        if self.primary_span:
            self.primary_span.set_exc_info(*sys.exc_info())

    @abstractmethod
    def finalize_stream(self, exception=None):
        pass


class StreamHandler(BaseStreamHandler):
    @abstractmethod
    def process_chunk(self, chunk, iterator=None):
        pass


class AsyncStreamHandler(BaseStreamHandler):
    @abstractmethod
    async def process_chunk(self, chunk, iterator=None):
        pass


class TracedStream(wrapt.ObjectProxy):
    def __init__(self, wrapped_stream, handler: StreamHandler):
        super().__init__(wrapped_stream)
        self._handler = handler
        self._stream_iter = iter(self.__wrapped__) if not hasattr(self.__wrapped__, '__next__') else self.__wrapped__
    
    def __iter__(self):
        return self
    
    def __next__(self):
        try:
            chunk = next(self._stream_iter)
            self._handler.process_chunk(chunk, self._stream_iter)
            return chunk
        except StopIteration:
            self._handler.finalize_stream()
            raise
        except Exception as e:
            self._handler.handle_exception(e)
            self._handler.finalize_stream(e)
            raise
    
    def __enter__(self):
        if hasattr(self.__wrapped__, '__enter__'):
            result = self.__wrapped__.__enter__()
            return self if result is self.__wrapped__ else result
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if hasattr(self.__wrapped__, '__exit__'):
            return self.__wrapped__.__exit__(exc_type, exc_val, exc_tb)
    
    @property
    def handler(self):
        return self._handler


class TracedAsyncStream(wrapt.ObjectProxy):
    def __init__(self, wrapped_stream, handler: AsyncStreamHandler):
        super().__init__(wrapped_stream)
        self._handler = handler
        self._async_stream_iter = aiter(self.__wrapped__) if not hasattr(self.__wrapped__, '__anext__') else self.__wrapped__
    
    def __aiter__(self):
        return self
    
    async def __anext__(self):
        try:
            chunk = await anext(self._async_stream_iter)
            await self._handler.process_chunk(chunk, self._async_stream_iter)
            return chunk
        except StopAsyncIteration:
            self._handler.finalize_stream()
            raise
        except Exception as e:
            self._handler.handle_exception(e)
            self._handler.finalize_stream(e)
            raise
    
    async def __aenter__(self):
        if hasattr(self.__wrapped__, '__aenter__'):
            result = await self.__wrapped__.__aenter__()
            return self if result is self.__wrapped__ else result
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if hasattr(self.__wrapped__, '__aexit__'):
            return await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)
    
    @property
    def handler(self):
        return self._handler 