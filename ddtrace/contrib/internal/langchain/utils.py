import inspect
import sys


class BaseTracedLangChainStreamResponse:
    def __init__(self, generator, integration, span, on_span_finish):
        self._generator = generator
        self._dd_integration = integration
        self._dd_span = span
        self._on_span_finish = on_span_finish
        self._chunks = []


class TracedLangchainStreamResponse(BaseTracedLangChainStreamResponse):
    def __iter__(self):
        try:
            for chunk in self._generator.__iter__():
                self._chunks.append(chunk)
                yield chunk
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            self._dd_integration.metric(self._dd_span, "incr", "request.error", 1)
            raise
        finally:
            self._on_span_finish(self._dd_span, self._chunks)
            self._dd_span.finish()


class TracedLangchainAsyncStreamResponse(BaseTracedLangChainStreamResponse):
    async def __aiter__(self):
        try:
            async for chunk in self._generator.__aiter__():
                self._chunks.append(chunk)
                yield chunk
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            self._dd_integration.metric(self._dd_span, "incr", "request.error", 1)
            raise
        finally:
            self._on_span_finish(self._dd_span, self._chunks)
            self._dd_span.finish()


def shared_stream(
    integration,
    pin,
    func,
    instance,
    args,
    kwargs,
    interface_type,
    on_span_started,
    on_span_finished,
    **extra_options,
):
    options = {
        "pin": pin,
        "operation_id": f"{instance.__module__}.{instance.__class__.__name__}",
        "interface_type": interface_type,
    }

    options.update(extra_options)

    span = integration.trace(**options)
    span.set_tag("langchain.request.stream", True)
    on_span_started(span)

    try:
        resp = func(*args, **kwargs)
        cls = TracedLangchainAsyncStreamResponse if inspect.isasyncgen(resp) else TracedLangchainStreamResponse

        return cls(resp, integration, span, on_span_finished)
    except Exception:
        # error with the method call itself
        span.set_exc_info(*sys.exc_info())
        span.finish()
        integration.metric(span, "incr", "request.error", 1)
        integration.metric(span, "dist", "request.duration", span.duration_ns)
        raise


def tag_general_message_input(span, inputs, integration, langchain_core):
    if langchain_core and isinstance(inputs, langchain_core.prompt_values.PromptValue):
        inputs = inputs.to_messages()
    elif not isinstance(inputs, list):
        inputs = [inputs]
    for input_idx, inp in enumerate(inputs):
        if isinstance(inp, dict):
            span.set_tag_str(
                "langchain.request.messages.%d.content" % (input_idx),
                integration.trunc(str(inp.get("content", ""))),
            )
            span.set_tag_str(
                "langchain.request.messages.%d.role" % (input_idx),
                str(inp.get("role", "")),
            )
        elif langchain_core and isinstance(inp, langchain_core.messages.BaseMessage):
            content = inp.content
            role = inp.__class__.__name__
            span.set_tag_str("langchain.request.messages.%d.content" % (input_idx), integration.trunc(str(content)))
            span.set_tag_str("langchain.request.messages.%d.role" % (input_idx), str(role))
        else:
            span.set_tag_str("langchain.request.messages.%d.content" % (input_idx), integration.trunc(str(inp)))
