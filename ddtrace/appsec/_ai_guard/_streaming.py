"""AI Guard streaming response buffer and replay.

Provides two ``wrapt.ObjectProxy`` wrappers — ``BufferedAIGuardStream`` (sync)
and ``BufferedAIGuardAsyncStream`` (async) — that intercept the iteration
protocol of a contrib ``TracedStream`` / ``TracedAsyncStream``.

SECURITY INVARIANT: no chunk is ever handed to the caller until
``evaluate(reconstruct(chunks))`` has returned without raising.  On block,
``evaluate`` raises ``AIGuardAbortError`` and the accumulated buffer is never
replayed.  This is buffer-then-evaluate, NOT live forwarding — do not
"optimise" it into a pass-through (that reintroduces the token-leak this
module exists to prevent).
"""

from typing import Any
from typing import AsyncIterator
from typing import Callable
from typing import Iterator
from typing import Optional

import wrapt

from ddtrace.appsec._ai_guard._context import is_aiguard_context_active
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.settings.asm import ai_guard_config
from ddtrace.llmobs._integrations.base_stream_handler import BaseStreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import AsyncStreamHandler


logger = ddlogger.get_logger(__name__)

# ``reconstruct`` rebuilds a provider-shaped response from buffered chunks;
# ``evaluate`` runs the AI Guard verdict and raises ``AIGuardAbortError`` on block.
ReconstructFn = Callable[[list[Any]], Any]
EvaluateFn = Callable[[Any], Any]


def _is_traced_stream(result: Any) -> bool:
    """Return True if *result* is a TracedStream or TracedAsyncStream from LLMObs.

    Both are identified by their ``_self_handler`` being a ``BaseStreamHandler``;
    :func:`_is_async_traced_stream` narrows that to the async subclass.
    """
    return isinstance(getattr(result, "_self_handler", None), BaseStreamHandler)


def _is_async_traced_stream(result: Any) -> bool:
    """Return True if *result*'s handler is an AsyncStreamHandler (TracedAsyncStream)."""
    return isinstance(getattr(result, "_self_handler", None), AsyncStreamHandler)


def _text_delta_from_chunk(chunk: Any) -> Optional[str]:
    """Return the text string from a text_delta chunk, or None for all other chunk types."""
    if getattr(chunk, "type", "") == "content_block_delta":
        delta = getattr(chunk, "delta", None)
        if delta and getattr(delta, "type", "") == "text_delta":
            text: str = getattr(delta, "text", "")
            return text
    return None


def _reconstruct_and_evaluate(reconstruct: ReconstructFn, evaluate: EvaluateFn, chunks: list[Any]) -> None:
    """Reconstruct a provider response from buffered *chunks* and run the AI Guard verdict.

    AIDEV-NOTE: reconstruction must fail OPEN -- a converter bug must not break
    the user's legitimate stream. Only ``evaluate`` (a block decision) is allowed
    to raise; reconstruction errors are swallowed and the buffer is replayed
    unevaluated. ``evaluate`` itself (``_anthropic_messages_create_after``) already
    swallows non-block exceptions and only re-raises AIGuardAbortError. Shared by
    the sync and async ``_drained`` paths so this invariant lives in one place.
    """
    try:
        response = reconstruct(chunks)
    except Exception:
        logger.debug("AI Guard: stream reconstruction failed; failing open", exc_info=True)
    else:
        evaluate(response)  # raises AIGuardAbortError on block


class BufferedAIGuardStream(wrapt.ObjectProxy):  # type: ignore[misc]  # wrapt ships no stubs
    """Sync buffer-then-evaluate proxy for a contrib TracedStream.

    On first use (``__iter__``, ``__next__``, ``__enter__``, ``text_stream``,
    or any convenience method), the proxy drains the underlying TracedStream
    completely, calls ``evaluate`` on the reconstructed response, then replays
    the buffered chunks.

    If the flag is off or a framework collision context is active the proxy is
    transparent: ``_drained()`` returns ``None`` and every method delegates
    directly to the wrapped stream.
    """

    def __init__(self, wrapped: Any, *, reconstruct: ReconstructFn, evaluate: EvaluateFn) -> None:
        super().__init__(wrapped)
        self._self_reconstruct = reconstruct
        self._self_evaluate = evaluate
        self._self_chunks: Optional[list[Any]] = None
        self._self_passthrough: bool = False
        self._self_index: int = 0

    def _drained(self) -> Optional[list[Any]]:
        if self._self_passthrough:
            return None
        if self._self_chunks is None:
            if not ai_guard_config._ai_guard_analyze_stream_responses_enabled or is_aiguard_context_active():
                self._self_passthrough = True
                return None
            chunks = list(self.__wrapped__)  # drives contrib tracing + finalize_stream
            _reconstruct_and_evaluate(self._self_reconstruct, self._self_evaluate, chunks)
            self._self_chunks = chunks
        return self._self_chunks

    def __iter__(self) -> Iterator[Any]:
        chunks = self._drained()
        if chunks is None:
            yield from self.__wrapped__
            return
        while self._self_index < len(chunks):
            chunk = chunks[self._self_index]
            self._self_index += 1
            yield chunk

    def __next__(self) -> Any:
        chunks = self._drained()
        if chunks is None:
            return self.__wrapped__.__next__()
        if self._self_index >= len(chunks):
            raise StopIteration
        chunk = chunks[self._self_index]
        self._self_index += 1
        return chunk

    # ------------------------------------------------------------------
    # Context-manager protocol
    #
    # TracedStream.__enter__() (base_stream_handler.py) has two branches:
    #   - non-manager (raw Stream): returns ``self`` (the TracedStream).
    #   - manager (MessageStreamManager): returns a NEW TracedStream
    #     wrapping the inner MessageStream.
    # Without these overrides, ObjectProxy proxies __enter__ to
    # TracedStream.__enter__() and the caller's "with ... as s:" assigns s
    # to a TracedStream — bypassing this proxy entirely in both cases.
    # ------------------------------------------------------------------

    def __enter__(self) -> "BufferedAIGuardStream":
        result = self.__wrapped__.__enter__()
        if result is self.__wrapped__:
            return self
        # manager path: TracedStream returned a new inner TracedStream; wrap
        # it so the caller still iterates the buffer, not the live stream.
        return BufferedAIGuardStream(
            result,
            reconstruct=self._self_reconstruct,
            evaluate=self._self_evaluate,
        )

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> Any:
        return self.__wrapped__.__exit__(exc_type, exc_val, exc_tb)

    # ------------------------------------------------------------------
    # Anthropic convenience attributes
    #
    # The contrib's on_stream_created callback sets ``text_stream`` on the
    # inner TracedStream as a generator that reads directly from that
    # TracedStream's iteration.  ObjectProxy attribute lookup proxies the
    # attribute through, so ``text_stream`` would bypass the buffer entirely.
    # These overrides ensure no token reaches the caller before _drained()
    # has returned ALLOW.
    # ------------------------------------------------------------------

    @property
    def text_stream(self) -> Iterator[str]:
        """Yield text deltas only after the full buffer has been evaluated.

        SECURITY INVARIANT: do NOT proxy to self.__wrapped__.text_stream —
        that generator reads directly from the underlying TracedStream and
        bypasses this buffer.
        """
        chunks = self._drained()
        if chunks is None:
            yield from self.__wrapped__.text_stream
            return
        for chunk in chunks:
            text = _text_delta_from_chunk(chunk)
            if text is not None:
                yield text

    def get_final_message(self) -> Any:
        self._drained()
        return self.__wrapped__.get_final_message()

    def get_final_text(self) -> Any:
        self._drained()
        return self.__wrapped__.get_final_text()

    def until_done(self) -> Any:
        self._drained()
        return self.__wrapped__.until_done()


class BufferedAIGuardAsyncStream(wrapt.ObjectProxy):  # type: ignore[misc]  # wrapt ships no stubs
    """Async mirror of BufferedAIGuardStream for TracedAsyncStream."""

    def __init__(self, wrapped: Any, *, reconstruct: ReconstructFn, evaluate: EvaluateFn) -> None:
        super().__init__(wrapped)
        self._self_reconstruct = reconstruct
        self._self_evaluate = evaluate
        self._self_chunks: Optional[list[Any]] = None
        self._self_passthrough: bool = False
        self._self_index: int = 0

    async def _drained(self) -> Optional[list[Any]]:
        if self._self_passthrough:
            return None
        if self._self_chunks is None:
            if not ai_guard_config._ai_guard_analyze_stream_responses_enabled or is_aiguard_context_active():
                self._self_passthrough = True
                return None
            chunks: list[Any] = []
            async for chunk in self.__wrapped__:
                chunks.append(chunk)
            _reconstruct_and_evaluate(self._self_reconstruct, self._self_evaluate, chunks)
            self._self_chunks = chunks
        return self._self_chunks

    def __aiter__(self) -> "BufferedAIGuardAsyncStream":
        return self

    async def __anext__(self) -> Any:
        chunks = await self._drained()
        if chunks is None:
            return await self.__wrapped__.__anext__()
        if self._self_index >= len(chunks):
            raise StopAsyncIteration
        chunk = chunks[self._self_index]
        self._self_index += 1
        return chunk

    async def __aenter__(self) -> "BufferedAIGuardAsyncStream":
        result = await self.__wrapped__.__aenter__()
        if result is self.__wrapped__:
            return self
        return BufferedAIGuardAsyncStream(
            result,
            reconstruct=self._self_reconstruct,
            evaluate=self._self_evaluate,
        )

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> Any:
        return await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)

    @property
    def text_stream(self) -> AsyncIterator[str]:
        """Return an async generator that yields text deltas after the buffer is evaluated."""

        async def _gen() -> AsyncIterator[str]:
            chunks = await self._drained()
            if chunks is None:
                async for text in self.__wrapped__.text_stream:
                    yield text
                return
            for chunk in chunks:
                text = _text_delta_from_chunk(chunk)
                if text is not None:
                    yield text

        return _gen()

    async def get_final_message(self) -> Any:
        await self._drained()
        return await self.__wrapped__.get_final_message()

    async def get_final_text(self) -> Any:
        await self._drained()
        return await self.__wrapped__.get_final_text()

    async def until_done(self) -> Any:
        await self._drained()
        return await self.__wrapped__.until_done()
