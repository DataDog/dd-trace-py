import sys
from typing import Any
from typing import Optional

import wrapt

from ddtrace.contrib.internal.pydantic_ai._constants import PYDANTIC_AI_SYSTEM_TO_PROVIDER
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils import get_argument_value


def get_model_and_provider(model: Optional[Any]) -> tuple[str, Optional[str]]:
    model_name = getattr(model, "model_name", "")
    system = getattr(model, "system", None)
    if system:
        system = PYDANTIC_AI_SYSTEM_TO_PROVIDER.get(system, system)
    return model_name, system


_FINISHED = "pydantic_ai.finished"


def finish_request(ctx, response=None):
    """Dispatch the request's deferred ended event, which tags and finishes the span. Only the first call has effect.

    Errors are recorded on the span before this is called, so LLMObs tagging sees them.
    """
    # A streamed run can finish from get_output(), the generator and the run's __aexit__; the context
    # drops its event once the ended event is dispatched, so later calls must not touch it.
    if ctx.get_item(_FINISHED):
        return
    ctx.set_item(_FINISHED, True)
    ctx.event.response = response
    ctx.dispatch_ended_event()


class TracedPydanticAsyncContextManager(wrapt.ObjectProxy):
    def __init__(self, wrapped, ctx):
        super().__init__(wrapped)
        self._dd_ctx = ctx
        self._agent_run = None

    async def __aenter__(self):
        result = await self.__wrapped__.__aenter__()
        self._agent_run = result
        return result

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)
        finally:
            if exc_type:
                span_from_context(self._dd_ctx).set_exc_info(exc_type, exc_val, exc_tb)
            finish_request(self._dd_ctx, None if exc_type else self._agent_run)


class TracedPydanticRunStream(wrapt.ObjectProxy):
    def __init__(self, wrapped, ctx):
        super().__init__(wrapped)
        self._dd_ctx = ctx
        self._streamed_run_result = None

    async def __aenter__(self):
        result = await self.__wrapped__.__aenter__()
        self._streamed_run_result = TracedPydanticStreamedRunResult(result, self._dd_ctx)
        return self._streamed_run_result

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)
        if exc_type:
            span_from_context(self._dd_ctx).set_exc_info(exc_type, exc_val, exc_tb)
        # ensure span is finished even if not all chunks were consumed or if there was an error
        if self._streamed_run_result and self._streamed_run_result._generator:
            self._streamed_run_result._generator._process_finished_stream()
        else:
            finish_request(self._dd_ctx)


class TracedPydanticStreamedRunResult(wrapt.ObjectProxy):
    def __init__(self, wrapped, ctx):
        super().__init__(wrapped)
        self._dd_ctx = ctx
        # needed for extracting usage metrics from the streamed run result
        ctx.event.request_kwargs["streamed_run_result"] = self.__wrapped__
        self._generator = None

    def stream(self, *args, **kwargs):
        self._generator = TracedPydanticGenerator(self.__wrapped__.stream(*args, **kwargs), self._dd_ctx)
        return self._generator

    def stream_text(self, *args, **kwargs):
        delta = get_argument_value(args, kwargs, 0, "delta", True) or False
        self._generator = TracedPydanticGenerator(self.__wrapped__.stream_text(*args, **kwargs), self._dd_ctx, delta)
        return self._generator

    def stream_structured(self, *args, **kwargs):
        self._generator = TracedPydanticGenerator(self.__wrapped__.stream_structured(*args, **kwargs), self._dd_ctx)
        return self._generator

    def stream_responses(self, *args, **kwargs):
        self._generator = TracedPydanticGenerator(self.__wrapped__.stream_responses(*args, **kwargs), self._dd_ctx)
        return self._generator

    async def get_output(self):
        result = await self.__wrapped__.get_output()
        finish_request(self._dd_ctx, result)
        return result


class TracedPydanticGenerator(wrapt.ObjectProxy):
    def __init__(self, wrapped, ctx, delta=False):
        super().__init__(wrapped)
        self._self_dd_ctx = ctx
        self._self_last_chunk = None
        self._self_delta = delta

    def _process_finished_stream(self):
        finish_request(self._self_dd_ctx, self._self_last_chunk)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            chunk = await self.__wrapped__.__anext__()
            # if delta is True, each chunk is a delta from the previous chunk
            self._self_last_chunk = (
                self._self_last_chunk + chunk if self._self_delta and self._self_last_chunk else chunk
            )
            return chunk
        except StopAsyncIteration:
            self._process_finished_stream()
            raise
        except Exception:
            span_from_context(self._self_dd_ctx).set_exc_info(*sys.exc_info())
            self._process_finished_stream()
            raise
