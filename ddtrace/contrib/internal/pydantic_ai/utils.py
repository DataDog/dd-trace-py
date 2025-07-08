import sys

import wrapt

from ddtrace.internal.utils import get_argument_value


class TracedPydanticAsyncContextManager(wrapt.ObjectProxy):
    def __init__(self, wrapped, span, instance, integration, args, kwargs):
        super().__init__(wrapped)
        self._dd_span = span
        self._dd_instance = instance
        self._dd_integration = integration
        self._args = args
        self._kwargs = kwargs
        self._agent_run = None

    async def __aenter__(self):
        result = await self.__wrapped__.__aenter__()
        self._agent_run = result
        return result

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)
            if exc_type:
                self._dd_span.set_exc_info(exc_type, exc_val, exc_tb)
            elif self._dd_integration.is_pc_sampled_llmobs(self._dd_span):
                self._dd_integration.llmobs_set_tags(
                    self._dd_span, args=self._args, kwargs=self._kwargs, response=self._agent_run
                )
        finally:
            if exc_type:
                self._dd_span.set_exc_info(exc_type, exc_val, exc_tb)
            self._dd_span.finish()


class TracedPydanticRunStream(wrapt.ObjectProxy):
    def __init__(self, wrapped, span, integration, args, kwargs):
        super().__init__(wrapped)
        self._dd_span = span
        self._dd_integration = integration
        self._args = args
        self._kwargs = kwargs
        self._streamed_run_result = None

    async def __aenter__(self):
        result = await self.__wrapped__.__aenter__()
        self._streamed_run_result = TracedPydanticStreamedRunResult(
            result, self._dd_span, self._dd_integration, self._args, self._kwargs
        )
        return self._streamed_run_result

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)
        if exc_type:
            self._dd_span.set_exc_info(exc_type, exc_val, exc_tb)
            # in case of an error, we need to process the finished stream to set llmobs tags
            if self._streamed_run_result and self._streamed_run_result._generator:
                self._streamed_run_result._generator._process_finished_stream()
            else:
                self._dd_span.finish()


class TracedPydanticStreamedRunResult(wrapt.ObjectProxy):
    def __init__(self, wrapped, span, integration, args, kwargs):
        super().__init__(wrapped)
        self._dd_span = span
        self._dd_integration = integration
        self._args = args
        self._kwargs = kwargs
        # needed for extracting usage metrics from the streamed run result
        self._kwargs["streamed_run_result"] = self.__wrapped__
        self._generator = None

    def stream(self, *args, **kwargs):
        self._generator = TracedPydanticGenerator(
            self.__wrapped__.stream(*args, **kwargs), self._dd_span, self._dd_integration, self._args, self._kwargs
        )
        return self._generator

    def stream_text(self, *args, **kwargs):
        delta = get_argument_value(args, kwargs, 0, "delta", True) or False
        self._generator = TracedPydanticGenerator(
            self.__wrapped__.stream_text(*args, **kwargs),
            self._dd_span,
            self._dd_integration,
            self._args,
            self._kwargs,
            delta,
        )
        return self._generator

    def stream_structured(self, *args, **kwargs):
        self._generator = TracedPydanticGenerator(
            self.__wrapped__.stream_structured(*args, **kwargs),
            self._dd_span,
            self._dd_integration,
            self._args,
            self._kwargs,
        )
        return self._generator

    async def get_output(self):
        result = await self.__wrapped__.get_output()
        self._dd_integration.llmobs_set_tags(self._dd_span, args=self._args, kwargs=self._kwargs, response=result)
        self._dd_span.finish()
        return result


class TracedPydanticGenerator(wrapt.ObjectProxy):
    def __init__(self, wrapped, span, integration, args, kwargs, delta=False):
        super().__init__(wrapped)
        self._self_dd_span = span
        self._self_dd_integration = integration
        self._self_args = args
        self._self_kwargs = kwargs
        self._self_last_chunk = None
        self._self_delta = delta

    def _process_finished_stream(self):
        self._self_dd_integration.llmobs_set_tags(
            self._self_dd_span, args=self._self_args, kwargs=self._self_kwargs, response=self._self_last_chunk
        )
        self._self_dd_span.finish()

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
            self._self_dd_span.set_exc_info(*sys.exc_info())
            self._process_finished_stream()
            raise
