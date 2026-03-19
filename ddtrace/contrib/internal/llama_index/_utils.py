"""Utility helpers for the LlamaIndex integration."""

from typing import Any

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value


log = get_logger(__name__)


def get_model_name(instance: Any) -> str:
    """Extract model name from a LlamaIndex LLM or embedding instance.

    Different LlamaIndex classes store the model name under different attributes:
    ``instance.model`` (OpenAI LLMs), ``instance.model_name`` (embeddings), or
    ``instance.metadata.model_name`` (some providers).  We check all three.
    """
    try:
        model = getattr(instance, "model", None) or getattr(instance, "model_name", None)
        if model:
            return str(model)
        metadata = getattr(instance, "metadata", None)
        if metadata:
            model_name = getattr(metadata, "model_name", None)
            if model_name:
                return str(model_name)
    except Exception:
        log.warning("Failed to extract model name from %s", type(instance).__name__, exc_info=True)
        return ""
    log.warning("Could not extract model name from %s", type(instance).__name__)
    return ""


def _get_max_tokens(instance: Any) -> Any:
    """Extract max_tokens from an LLM instance, if set.

    Not all LlamaIndex LLMs define ``max_tokens``; returns None when absent.
    """
    try:
        return getattr(instance, "max_tokens", None)
    except Exception:
        log.debug("Failed to read max_tokens from %s", type(instance).__name__, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Request-kwargs builders
# ---------------------------------------------------------------------------


def build_chat_request_kwargs(
    instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    """Build trace kwargs for ``chat()`` / ``achat()``.

    Wraps: ``BaseLLM.chat(messages, **kwargs)`` (llama_index.core >= 0.11)
    """
    request_kwargs = kwargs.copy()
    messages = get_argument_value(args, kwargs, 0, "messages", optional=True)
    if messages is not None:
        request_kwargs["messages"] = messages
    model = get_model_name(instance)
    request_kwargs["model"] = model
    max_tokens = _get_max_tokens(instance)
    if max_tokens is not None:
        request_kwargs["max_tokens"] = max_tokens
    return request_kwargs, model


def build_complete_request_kwargs(
    instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    """Build trace kwargs for ``complete()`` / ``acomplete()``.

    Wraps: ``BaseLLM.complete(prompt, formatted=False, **kwargs)`` (llama_index.core >= 0.11)
    """
    request_kwargs = kwargs.copy()
    prompt = get_argument_value(args, kwargs, 0, "prompt", optional=True)
    if prompt is not None:
        request_kwargs["prompt"] = prompt
    model = get_model_name(instance)
    request_kwargs["model"] = model
    max_tokens = _get_max_tokens(instance)
    if max_tokens is not None:
        request_kwargs["max_tokens"] = max_tokens
    return request_kwargs, model


def build_predict_request_kwargs(
    instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    """Build trace kwargs for ``predict()`` / ``apredict()``.

    Wraps: ``BaseLLM.predict(prompt, **prompt_args)`` (llama_index.core >= 0.11)
    The prompt may be a PromptTemplate (has ``.template``) or a plain string.
    """
    request_kwargs = kwargs.copy()
    prompt = get_argument_value(args, kwargs, 0, "prompt", optional=True)
    if prompt is not None:
        template = getattr(prompt, "template", None)
        request_kwargs["prompt"] = str(template) if template else str(prompt)
    model = get_model_name(instance)
    request_kwargs["model"] = model
    max_tokens = _get_max_tokens(instance)
    if max_tokens is not None:
        request_kwargs["max_tokens"] = max_tokens
    return request_kwargs, model


def build_query_request_kwargs(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Build trace kwargs for ``query()`` / ``aquery()`` and ``retrieve()`` / ``aretrieve()``.

    Wraps: ``BaseQueryEngine.query(str_or_query_bundle)`` (llama_index.core >= 0.11)
    The argument may be a plain string or a QueryBundle with ``.query_str``.
    """
    request_kwargs = kwargs.copy()
    query = get_argument_value(args, kwargs, 0, "str_or_query_bundle", optional=True)
    if query is not None:
        request_kwargs["query_str"] = getattr(query, "query_str", str(query))
    return request_kwargs


def build_query_embedding_request_kwargs(
    instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    """Build trace kwargs for ``get_query_embedding()`` / ``aget_query_embedding()``.

    Wraps: ``BaseEmbedding.get_query_embedding(query)`` (llama_index.core >= 0.11)
    """
    request_kwargs = kwargs.copy()
    query = get_argument_value(args, kwargs, 0, "query", optional=True)
    if query is not None:
        request_kwargs["query"] = str(query)
    model = get_model_name(instance)
    if model:
        request_kwargs["model"] = model
    return request_kwargs, model


def build_text_embedding_batch_request_kwargs(
    instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    """Build trace kwargs for ``get_text_embedding_batch()`` / ``aget_text_embedding_batch()``.

    Wraps: ``BaseEmbedding.get_text_embedding_batch(texts, **kwargs)`` (llama_index.core >= 0.11)
    Stores a summary string rather than the full text list.
    """
    request_kwargs = kwargs.copy()
    texts = get_argument_value(args, kwargs, 0, "texts", optional=True)
    if texts is not None:
        request_kwargs["query"] = f"[{len(texts)} texts]" if texts else ""
    model = get_model_name(instance)
    if model:
        request_kwargs["model"] = model
    return request_kwargs, model


def build_agent_run_request_kwargs(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Build trace kwargs for agent ``run()``.

    Wraps: ``BaseWorkflowAgent.run(user_msg, **kwargs)`` (llama_index.core >= 0.11)
    The message may be a ChatMessage (has ``.content``) or a plain string.
    """
    request_kwargs = kwargs.copy()
    user_msg = get_argument_value(args, kwargs, 0, "user_msg", optional=True)
    if user_msg is not None:
        content = getattr(user_msg, "content", None)
        request_kwargs["input"] = str(content) if content else str(user_msg)
    return request_kwargs


def build_agent_call_tool_request_kwargs(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Build trace kwargs for agent ``call_tool()``.

    Wraps: ``BaseWorkflowAgent.call_tool(ctx, ev, **kwargs)`` (llama_index.core >= 0.11)
    The tool event *ev* is at position 1 and carries ``.tool_name``.
    """
    request_kwargs = kwargs.copy()
    ev = get_argument_value(args, kwargs, 1, "ev", optional=True)
    if ev is not None:
        try:
            tool_name = str(ev.tool_name)
        except AttributeError:
            log.warning("Expected tool event to have tool_name attribute, got %s", type(ev).__name__)
            tool_name = ""
        if tool_name:
            request_kwargs["tool_name"] = tool_name
    return request_kwargs
