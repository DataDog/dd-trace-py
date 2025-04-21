import inspect
import sys

from ddtrace.settings.asm import config as asm_config


class BaseTracedLangChainStreamResponse:
    def __init__(self, generator, integration, span, on_span_finish, chunk_callback):
        self._generator = generator
        self._dd_integration = integration
        self._dd_span = span
        self._on_span_finish = on_span_finish
        self._chunk_callback = chunk_callback
        self._chunks = []


class TracedLangchainStreamResponse(BaseTracedLangChainStreamResponse):
    def __enter__(self):
        self._generator.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._generator.__exit__(exc_type, exc_val, exc_tb)

    def __iter__(self):
        return self

    def __next__(self):
        try:
            chunk = self._generator.__next__()
            self._chunks.append(chunk)
            self._chunk_callback(chunk)
            return chunk
        except StopIteration:
            self._on_span_finish(self._dd_span, self._chunks)
            self._dd_span.finish()
            raise
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            self._dd_span.finish()
            raise


class TracedLangchainAsyncStreamResponse(BaseTracedLangChainStreamResponse):
    async def __aenter__(self):
        await self._generator.__enter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._generator.__exit__(exc_type, exc_val, exc_tb)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            chunk = await self._generator.__anext__()
            self._chunks.append(chunk)
            self._chunk_callback(chunk)
            return chunk
        except StopAsyncIteration:
            self._on_span_finish(self._dd_span, self._chunks)
            self._dd_span.finish()
            raise
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            self._dd_span.finish()
            raise


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
        # submit to llmobs only if we detect an LLM request that is not being sent to a proxy
        "submit_to_llmobs": (
            integration.has_default_base_url(instance) if interface_type in ("chat_model", "llm") else True
        ),
    }

    options.update(extra_options)

    span = integration.trace(**options)
    span.set_tag("langchain.request.stream", True)
    on_span_started(span)

    try:
        resp = func(*args, **kwargs)
        cls = TracedLangchainAsyncStreamResponse if inspect.isasyncgen(resp) else TracedLangchainStreamResponse
        chunk_callback = _iast_create_chunk_callback(args, kwargs)
        return cls(resp, integration, span, on_span_finished, chunk_callback)
    except Exception:
        # error with the method call itself
        span.set_exc_info(*sys.exc_info())
        span.finish()
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
            role = inp.get("role")
            if role is not None:
                span.set_tag_str(
                    "langchain.request.messages.%d.message_type" % (input_idx),
                    str(inp.get("role", "")),
                )
        elif langchain_core and isinstance(inp, langchain_core.messages.BaseMessage):
            content = inp.content
            role = inp.__class__.__name__
            span.set_tag_str("langchain.request.messages.%d.content" % (input_idx), integration.trunc(str(content)))
            span.set_tag_str("langchain.request.messages.%d.message_type" % (input_idx), str(role))
        else:
            span.set_tag_str("langchain.request.messages.%d.content" % (input_idx), integration.trunc(str(inp)))


def _iast_chunk_no_op(chunk):
    pass


def _iast_create_taint_chunk_callback(source):
    def _iast_chunk_taint(chunk):
        _iast_taint_chunk(source, chunk)
        return chunk

    return _iast_chunk_taint


def _iast_create_chunk_callback(args, kwargs):
    from ddtrace.internal.utils import get_argument_value

    chat_messages = get_argument_value(args, kwargs, 0, "input", optional=True)
    if not chat_messages:
        return _iast_chunk_no_op
    source = _iast_get_tainted_source_from_chat_prompt_value(chat_messages)
    if not source:
        return _iast_chunk_no_op
    return _iast_create_taint_chunk_callback(source)


def _iast_get_tainted_source_from_chat_prompt_value(chat_prompt_value):
    if not asm_config._iast_enabled:
        return None
    if not hasattr(chat_prompt_value, "messages"):
        return None
    messages = chat_prompt_value.messages
    if not isinstance(messages, (tuple, list)):
        return None

    from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges

    for message in messages:
        if not hasattr(message, "content"):
            continue
        content = message.content
        if not isinstance(content, str):
            continue
        tainted_ranges = get_tainted_ranges(content)
        if tainted_ranges:
            return tainted_ranges[0].source
    return None


def _iast_taint_chunk(source, chunk):
    """
    Taints a chunk (type BaseMessageChunk, typically an AIMessageChunk) given a source.
    """
    # Relevant attributes to taint are:
    #  content: Union[str, list[Union[str, dict]]]
    #  additional_kwargs: dict
    if not asm_config._iast_enabled:
        return
    if not source:
        return
    message = chunk
    if not hasattr(message, "content"):
        return
    content = message.content
    if isinstance(content, str):
        setattr(message, "content", _iast_taint_if_str(source, content))
    elif isinstance(content, list):
        setattr(message, "content", [_iast_taint_if_str(source, c) for c in content])
    elif isinstance(content, dict):
        setattr(message, "content", {k: _iast_taint_if_str(source, v) for k, v in message.items()})
    if hasattr(message, "additional_kwargs"):
        additional_kwargs = message.additional_kwargs
        if isinstance(additional_kwargs, dict) and "function_call" in additional_kwargs:
            # OpenAI-style tool call, arguments are passed serialized in JSON.
            function_call = additional_kwargs["function_call"]
            if isinstance(function_call, dict) and "arguments" in function_call:
                arguments = function_call["arguments"]
                if isinstance(arguments, str):
                    function_call["arguments"] = _iast_taint_if_str(source, arguments)


def _iast_taint_if_str(source, obj):
    if not isinstance(obj, str):
        return obj
    from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject

    return taint_pyobject(
        pyobject=obj,
        source_name=source.name,
        source_value=source.value,
        source_origin=source.origin,
    )
