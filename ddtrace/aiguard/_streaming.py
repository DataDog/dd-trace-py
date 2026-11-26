"""AI Guard buffer-then-evaluate of the event source of a streamed response.

The provider contribs hand AI Guard the event iterator of each new SDK stream (their
stream.created events). That iterator sits below every SDK consumer: the caller's iteration, the
SDK message accumulators with their snapshots and final messages, the text helpers and the tool
runners. Replacing it with BufferedEvents or AsyncBufferedEvents means those consumers only ever
see events that AI Guard allowed.

SECURITY INVARIANT: no event is handed to the SDK until evaluate(reconstruct(events)) has
returned without raising, and only the events evaluated together are replayed. On block, evaluate
raises AIGuardAbortError and nothing is replayed. This is buffer-then-evaluate, NOT live
forwarding: do not "optimise" it into a pass-through.
"""

import asyncio
from collections.abc import AsyncIterator
from collections.abc import Iterator
from typing import Any
from typing import Callable
from typing import Optional
from typing import Union

from ddtrace.aiguard._api_client import AIGuardAbortError
from ddtrace.aiguard._context import is_aiguard_context_active
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.settings.aiguard import aiguard_config


logger = ddlogger.get_logger(__name__)

# reconstruct rebuilds a provider-shaped response from buffered events;
# evaluate runs the AI Guard verdict and raises AIGuardAbortError on block.
ReconstructFn = Callable[[list[Any]], Any]
EvaluateFn = Callable[[Any], Any]
# Checked when the stream is first read, so a flag turned off by then leaves it unbuffered.
EnabledFn = Callable[[], bool]


def _stream_analysis_enabled() -> bool:
    return bool(aiguard_config._ai_guard_analyze_stream_responses_enabled)


def _reconstruct_and_evaluate(reconstruct: ReconstructFn, evaluate: EvaluateFn, chunks: list[Any]) -> None:
    """Reconstruct a provider response from buffered chunks and run the AI Guard verdict.

    Reconstruction fails open: a converter bug must not break a legitimate stream, so its errors
    are swallowed and the buffer is replayed unevaluated. Only evaluate may raise a block; the
    listeners already swallow every other evaluation error.
    """
    try:
        response = reconstruct(chunks)
    except Exception:
        logger.debug("AI Guard: stream reconstruction failed; failing open", exc_info=True)
    else:
        evaluate(response)  # raises AIGuardAbortError on block


class InterruptedStreamError(RuntimeError):
    """A later read of a stream whose drain was interrupted before AI Guard evaluated it."""


def _remember_block(block: AIGuardAbortError) -> AIGuardAbortError:
    """Keep a block for later reads without its frames or the frames of the exceptions it chains."""
    block.__traceback__ = None
    seen: set[int] = set()
    chained = [block.__cause__, block.__context__]
    while chained:
        exc = chained.pop()
        if exc is None or id(exc) in seen:
            continue
        seen.add(id(exc))
        exc.__traceback__ = None
        chained += [exc.__cause__, exc.__context__]
    return block


def _drain_failure(error: BaseException) -> BaseException:
    """The failure that every later read of a stream with an interrupted drain raises."""
    # A cancellation or an interrupt belongs to the reader it stopped, not to the next one.
    return error if isinstance(error, Exception) else InterruptedStreamError("the stream was interrupted")


def _raise_failure(failure: Optional[BaseException]) -> None:
    """Raise a remembered failure again; the traceback is reset so repeated reads do not grow it."""
    if failure is not None:
        raise failure.with_traceback(None)


class _BufferedEventsBase:
    def __init__(
        self,
        events: Any,
        *,
        reconstruct: ReconstructFn,
        evaluate: EvaluateFn,
        enabled: EnabledFn = _stream_analysis_enabled,
    ) -> None:
        self._events = events
        self._reconstruct = reconstruct
        self._evaluate = evaluate
        self._enabled = enabled
        self._passthrough: Optional[bool] = None
        # Drained so far; only a complete drain is evaluated, and an interrupted one is terminal.
        self._buffer: list[Any] = []
        self._drain_complete = False
        # The buffer, once the evaluation allowed it.
        self._chunks: Optional[list[Any]] = None
        # A block, a provider error or an interrupted drain: every later read raises it again.
        self._failure: Optional[BaseException] = None
        self._index = 0

    def _is_passthrough(self) -> bool:
        # Decided once, on the first read: a stream that started buffering is always evaluated.
        if self._passthrough is None:
            self._passthrough = not self._enabled() or is_aiguard_context_active()
        return self._passthrough

    def _next_chunk(self) -> Any:
        assert self._chunks is not None  # nosec B101
        if self._index >= len(self._chunks):
            return _END
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk


_END = object()


class BufferedEvents(_BufferedEventsBase):
    """Sync SDK event iterator that replays the events only after AI Guard allowed them."""

    def __iter__(self) -> Iterator[Any]:
        return self

    def __next__(self) -> Any:
        _raise_failure(self._failure)
        if self._chunks is None:
            if self._is_passthrough():
                return next(self._events)
            self._drain_and_evaluate()
        chunk = self._next_chunk()
        if chunk is _END:
            raise StopIteration
        return chunk

    def _drain_and_evaluate(self) -> None:
        if not self._drain_complete:
            try:
                for event in self._events:
                    self._buffer.append(event)
            except BaseException as e:
                # A drain that does not complete is terminal: none of its events reach the SDK.
                self._failure = _drain_failure(e)
                raise
            self._drain_complete = True
        try:
            _reconstruct_and_evaluate(self._reconstruct, self._evaluate, self._buffer)
        except AIGuardAbortError as e:
            self._failure = _remember_block(e)
        # Raised outside the handler, so the remembered block does not chain the evaluation frames.
        _raise_failure(self._failure)
        self._chunks = self._buffer


class AsyncBufferedEvents(_BufferedEventsBase):
    """Async SDK event iterator that replays the events only after AI Guard allowed them."""

    def __init__(
        self,
        events: Any,
        *,
        reconstruct: ReconstructFn,
        evaluate: EvaluateFn,
        enabled: EnabledFn = _stream_analysis_enabled,
    ) -> None:
        super().__init__(events, reconstruct=reconstruct, evaluate=evaluate, enabled=enabled)
        self._lock: Optional[asyncio.Lock] = None
        # One evaluation shared by every reader, so a cancelled reader does not make the next one start another.
        self._evaluation: Optional[asyncio.Future[None]] = None

    def __aiter__(self) -> AsyncIterator[Any]:
        return self

    async def __anext__(self) -> Any:
        _raise_failure(self._failure)
        if self._chunks is None:
            if self._is_passthrough():
                return await self._events.__anext__()
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                # Not on asyncio (e.g. trio): evaluate inline.
                await self._drain_and_evaluate(off_loop=False)
            else:
                if self._lock is None:
                    self._lock = asyncio.Lock()
                # Concurrent readers wait for the same verdict instead of reading the exhausted stream.
                async with self._lock:
                    await self._drain_and_evaluate(off_loop=True)
        chunk = self._next_chunk()
        if chunk is _END:
            raise StopAsyncIteration
        return chunk

    async def _drain_and_evaluate(self, off_loop: bool) -> None:
        if self._chunks is not None:
            return
        # A reader that waited on the lock gets the failure of the read it waited for.
        _raise_failure(self._failure)
        if not self._drain_complete:
            try:
                async for event in self._events:
                    self._buffer.append(event)
            except BaseException as e:
                # A drain that does not complete is terminal: none of its events reach the SDK.
                self._failure = _drain_failure(e)
                raise
            self._drain_complete = True
        if not off_loop:
            self._evaluate_buffer()
        else:
            if self._evaluation is None:
                # The evaluation is a blocking HTTP call: run it off the event loop (contextvars are copied).
                self._evaluation = asyncio.ensure_future(asyncio.to_thread(self._evaluate_buffer))
                self._evaluation.add_done_callback(self._release_evaluation)
            # Shielded: cancelling this reader leaves the evaluation running, and the next reader gets its verdict.
            await asyncio.shield(self._evaluation)
        _raise_failure(self._failure)

    def _release_evaluation(self, evaluation: "asyncio.Future[None]") -> None:
        """Drop a finished evaluation, also when no reader awaits it.

        Its verdict is recorded, or without one the next read evaluates again.
        """
        if self._evaluation is evaluation:
            self._evaluation = None
        if not evaluation.cancelled():
            evaluation.exception()  # retrieved, so a failure nobody awaits is not logged

    def _evaluate_buffer(self) -> None:
        """Evaluate the drained buffer and record its verdict, inline or in a worker thread."""
        try:
            _reconstruct_and_evaluate(self._reconstruct, self._evaluate, self._buffer)
        except AIGuardAbortError as e:
            # Recorded, not raised, so a verdict nobody awaits is not logged as an unretrieved task exception.
            self._failure = _remember_block(e)
        else:
            self._chunks = self._buffer


def buffered_events(
    events: Any,
    is_async: bool,
    *,
    reconstruct: ReconstructFn,
    evaluate: EvaluateFn,
    enabled: EnabledFn = _stream_analysis_enabled,
) -> Union[BufferedEvents, AsyncBufferedEvents]:
    """Wrap the event iterator of an SDK stream in the buffer that matches it."""
    cls = AsyncBufferedEvents if is_async else BufferedEvents
    return cls(events, reconstruct=reconstruct, evaluate=evaluate, enabled=enabled)
