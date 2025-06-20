"""
Base stream handler for LLMObs integrations providing a clean interface for stream processing.

This module provides a generator-based approach to handling streamed responses that processes
chunks as they arrive while maintaining state between operations.
"""
import sys
from abc import ABC, abstractmethod
from collections import defaultdict

import wrapt

from ddtrace.internal.logger import get_logger

log = get_logger(__name__)


class BaseStreamHandler(ABC):
    """
    Abstract base class for all stream handlers.
    
    Contains common functionality shared between sync and async handlers.
    """
    
    def __init__(self, integration, span, kwargs, **options):
        """
        Initialize the stream handler.
        
        Args:
            integration: The LLMObs integration instance
            span: The primary span for this stream  
            kwargs: Original request kwargs
            **options: Additional handler-specific options
        """
        self.integration = integration
        self.primary_span = span
        self.request_kwargs = kwargs
        self.options = options
        
        # State management
        self.spans = [(span, kwargs)]  # List of (span, kwargs) tuples
        self.chunks = self._initialize_chunk_storage()
        self.metadata = {}
        self.is_finished = False
    
    def _initialize_chunk_storage(self):
        """
        Initialize storage for chunks.
        
        Override this method to customize how chunks are stored.
        Default creates a defaultdict(list) which works for most integrations.
        
        Returns:
            Initial chunk storage structure
        """
        return defaultdict(list)
    
    def add_span(self, span, kwargs):
        """
        Add an additional span to track (useful for multi-span scenarios).
        
        Args:
            span: Additional span to track
            kwargs: Kwargs for this span
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
        pass


class StreamHandler(BaseStreamHandler):
    """
    Abstract base class for synchronous stream handlers.
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
        pass


class AsyncStreamHandler(BaseStreamHandler):
    """
    Abstract base class for asynchronous stream handlers.
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
        pass


class TracedStream(wrapt.ObjectProxy):
    def __init__(self, wrapped_stream, handler: StreamHandler):
        super().__init__(wrapped_stream)
        self._handler = handler
        self._iterator = iter(wrapped_stream) if not hasattr(wrapped_stream, '__next__') else wrapped_stream
    
    def __iter__(self):
        return self
    
    def __next__(self):
        try:
            chunk = next(self._iterator)
            self._handler.process_chunk(chunk, self._iterator)
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
        self._iterator = wrapped_stream if hasattr(wrapped_stream, '__anext__') else aiter(wrapped_stream)
    
    def __aiter__(self):
        return self
    
    async def __anext__(self):
        try:
            chunk = await anext(self._iterator)
            await self._handler.process_chunk(chunk, self._iterator)
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