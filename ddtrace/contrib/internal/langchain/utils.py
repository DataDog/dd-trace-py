import inspect
import sys
from typing import Any
from typing import Optional
from typing import Union

import langchain
from pydantic import SecretStr

from ddtrace.internal.utils.version import parse_version


def get_version():
    # type: () -> str
    return getattr(langchain, "__version__", "")


# After 0.1.0, implementation split into langchain, langchain_community, and langchain_core.
# We need to check the version to determine which module to wrap, to avoid deprecation warnings
# ref: https://github.com/DataDog/dd-trace-py/issues/8212
PATCH_LANGCHAIN_V0 = parse_version(get_version()) < (0, 1, 0)


class BaseTracedLangChainStreamResponse:
    def __init__(self, generator, integration, span, on_span_finish):
        self._generator = generator
        self._dd_integration = integration
        self._dd_span = span
        self._on_span_finish = on_span_finish
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
            return chunk
        except StopIteration:
            self._on_span_finish(self._dd_span, self._chunks)
            self._dd_span.finish()
            raise
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            self._dd_integration.metric(self._dd_span, "incr", "request.error", 1)
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
            return chunk
        except StopAsyncIteration:
            self._on_span_finish(self._dd_span, self._chunks)
            self._dd_span.finish()
            raise
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            self._dd_integration.metric(self._dd_span, "incr", "request.error", 1)
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
        "submit_to_llmobs": True,
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


def _extract_model_name(instance: Any) -> Optional[str]:
    """Extract model name or ID from llm instance."""
    for attr in ("model", "model_name", "model_id", "model_key", "repo_id"):
        if hasattr(instance, attr):
            return getattr(instance, attr)
    return None


def _format_api_key(api_key: Union[str, SecretStr]) -> str:
    """Obfuscate a given LLM provider API key by returning the last four characters."""
    if hasattr(api_key, "get_secret_value"):
        api_key = api_key.get_secret_value()

    if not api_key or len(api_key) < 4:
        return ""
    return "...%s" % api_key[-4:]


def _extract_api_key(instance: Any) -> str:
    """
    Extract and format LLM-provider API key from instance.
    Note that langchain's LLM/ChatModel/Embeddings interfaces do not have a
    standard attribute name for storing the provider-specific API key, so make a
    best effort here by checking for attributes that end with `api_key/api_token`.
    """
    api_key_attrs = [a for a in dir(instance) if a.endswith(("api_token", "api_key"))]
    if api_key_attrs and hasattr(instance, str(api_key_attrs[0])):
        api_key = getattr(instance, api_key_attrs[0], None)
        if api_key:
            return _format_api_key(api_key)
    return ""
