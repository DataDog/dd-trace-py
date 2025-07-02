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
            # NOTE: the result is available once the END is reached; we need to make this more robust to
            # capture responses during iteration
            elif self._dd_integration.is_pc_sampled_llmobs(self._dd_span):
                self._dd_integration.llmobs_set_tags(
                    self._dd_span, args=self._args, kwargs=self._kwargs, response=self._agent_run
                )
        finally:
            if exc_type:
                self._dd_span.set_exc_info(exc_type, exc_val, exc_tb)
            self._dd_span.finish()
