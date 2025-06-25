import wrapt

def tag_request(span, instance):
    model = getattr(instance, "model", None)
    if model:
        span.set_tag("pydantic_ai.request.model", getattr(model, "model_name", ""))
        provider = getattr(model, "_provider", None)
        system = getattr(model, "system", None)
        # different model providers have different model classes and ways of accessing the provider name
        if provider or system:
            span.set_tag("pydantic_ai.request.provider", getattr(provider, "name", "") or system)


class TracedPydanticAsyncContextManager(wrapt.ObjectProxy):
    def __init__(self, wrapped, span, instance):
        super().__init__(wrapped)
        self._dd_span = span
        self._dd_instance = instance
    
    async def __aenter__(self):
        tag_request(self._dd_span, self._dd_instance)
        return await self.__wrapped__.__aenter__()
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            return await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)
        finally:
            self._dd_span.finish()

    

