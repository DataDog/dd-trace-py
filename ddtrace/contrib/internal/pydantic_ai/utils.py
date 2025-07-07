import sys
import wrapt


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
    
    async def __aenter__(self):
        result = await self.__wrapped__.__aenter__()
        return TracedPydanticStreamedRunResult(result, self._dd_span, self._dd_integration, self._args, self._kwargs)
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)


class TracedPydanticStreamedRunResult(wrapt.ObjectProxy):
    def __init__(self, wrapped, span, integration, args, kwargs):
        super().__init__(wrapped)
        self._dd_span = span
        self._dd_integration = integration
        self._args = args
        self._kwargs = kwargs
        # needed for extracting usage metrics from the streamed run result
        self._kwargs["streamed_run_result"] = self.__wrapped__

    def stream(self):
        return TracedPydanticGenerator(self.__wrapped__.stream(), self._dd_span, self._dd_integration, self._args, self._kwargs)
    
    def stream_text(self):
        return TracedPydanticGenerator(self.__wrapped__.stream_text(), self._dd_span, self._dd_integration, self._args, self._kwargs)
    
    def stream_structured(self):
        return TracedPydanticGenerator(self.__wrapped__.stream_structured(), self._dd_span, self._dd_integration, self._args, self._kwargs)
    
    async def get_output(self):
        result = await self.__wrapped__.get_output()
        self._dd_integration.llmobs_set_tags(
            self._dd_span, args=self._args, kwargs=self._kwargs, response=result
        )
        self._dd_span.finish()
        return result
    
class TracedPydanticGenerator(wrapt.ObjectProxy):
    def __init__(self, wrapped, span, integration, args, kwargs):
        super().__init__(wrapped)
        self._self_dd_span = span
        self._self_dd_integration = integration
        self._self_args = args
        self._self_kwargs = kwargs
        self._self_last_chunk = None

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
            self._self_last_chunk = chunk
            return chunk
        except StopAsyncIteration:
            self._process_finished_stream() 
            raise
        except Exception:
            self._self_dd_span.set_exc_info(*sys.exc_info())
            self._process_finished_stream()
            raise

        
    



