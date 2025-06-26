import wrapt


class TracedPydanticAsyncContextManager(wrapt.ObjectProxy):
    def __init__(self, wrapped, span):
        super().__init__(wrapped)
        self._dd_span = span

    async def __aenter__(self):
        return await self.__wrapped__.__aenter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            return await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)
        finally:
            if exc_type:
                self._dd_span.set_exc_info(exc_type, exc_val, exc_tb)
            self._dd_span.finish()
