import sys

import wrapt


class BaseTracedGenerateContentResponse(wrapt.ObjectProxy):
    """Base wrapper class for GenerateContentResponse objects for tracing streamed responses."""

    def __init__(self, wrapped, instance, integration, span, args, kwargs):
        super().__init__(wrapped)
        self._model_instance = instance
        self._dd_integration = integration
        self._dd_span = span
        self._args = args
        self._kwargs = kwargs


class TracedGenerateContentResponse(BaseTracedGenerateContentResponse):
    def __iter__(self):
        try:
            for chunk in self.__wrapped__.__iter__():
                yield chunk
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            raise
        finally:
            self._kwargs["instance"] = self._model_instance
            self._dd_integration.llmobs_set_tags(
                self._dd_span,
                args=self._args,
                kwargs=self._kwargs,
                response=self.__wrapped__,
            )
            self._dd_span.finish()


class TracedAsyncGenerateContentResponse(BaseTracedGenerateContentResponse):
    async def __aiter__(self):
        try:
            async for chunk in self.__wrapped__.__aiter__():
                yield chunk
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            raise
        finally:
            self._kwargs["instance"] = self._model_instance
            self._dd_integration.llmobs_set_tags(
                self._dd_span,
                args=self._args,
                kwargs=self._kwargs,
                response=self.__wrapped__,
            )
            self._dd_span.finish()
