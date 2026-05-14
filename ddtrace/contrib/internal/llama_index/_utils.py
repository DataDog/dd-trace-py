"""Utility helpers for the LlamaIndex integration."""

from typing import Any

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value


log = get_logger(__name__)


def get_model_provider(instance: Any) -> str:
    """Extract the LLM provider name from a LlamaIndex instance.

    Walks the class MRO to find the first ancestor defined in a LlamaIndex
    provider package (``llama_index.llms.<provider>`` or
    ``llama_index.embeddings.<provider>``).  This handles user subclasses
    that inherit from a provider class but are defined in application code.
    """
    for cls in type(instance).__mro__:
        module = getattr(cls, "__module__", "") or ""
        parts = module.split(".")
        if len(parts) >= 3 and parts[0] == "llama_index" and parts[1] in ("llms", "embeddings"):
            return parts[2]
    return "unknown"


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
        return "unknown"
    log.warning("Could not extract model name from %s", type(instance).__name__)
    return "unknown"


def build_chat_request_kwargs(
    instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    """Build trace kwargs for ``chat()`` / ``achat()``."""
    request_kwargs = kwargs.copy()
    messages = get_argument_value(args, kwargs, 0, "messages", optional=True)
    if messages is not None:
        request_kwargs["messages"] = messages
    model = get_model_name(instance)
    request_kwargs["model"] = model
    max_tokens = getattr(instance, "max_tokens", None)
    if max_tokens is not None:
        request_kwargs["max_tokens"] = max_tokens
    return request_kwargs, model


def build_complete_request_kwargs(
    instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    """Build trace kwargs for ``complete()`` / ``acomplete()``."""
    request_kwargs = kwargs.copy()
    prompt = get_argument_value(args, kwargs, 0, "prompt", optional=True)
    if prompt is not None:
        request_kwargs["prompt"] = prompt
    model = get_model_name(instance)
    request_kwargs["model"] = model
    max_tokens = getattr(instance, "max_tokens", None)
    if max_tokens is not None:
        request_kwargs["max_tokens"] = max_tokens
    return request_kwargs, model


def build_query_request_kwargs(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Build trace kwargs for ``query()`` / ``aquery()`` and ``retrieve()`` / ``aretrieve()``.

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
    """Build trace kwargs for ``get_query_embedding()`` / ``aget_query_embedding()``."""
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
