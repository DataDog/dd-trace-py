"""Utility helpers for the LlamaIndex integration."""

import inspect
from typing import Any
from typing import Generator

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value


log = get_logger(__name__)


def get_model_name(instance: Any) -> str:
    """Extract model name from a LlamaIndex LLM instance.

    Checks ``instance.model``, ``instance.model_name``, and
    ``instance.metadata.model_name`` in order.
    """
    model = getattr(instance, "model", None) or getattr(instance, "model_name", None)
    if model:
        return str(model)
    metadata = getattr(instance, "metadata", None)
    if metadata:
        model_name = getattr(metadata, "model_name", None)
        if model_name:
            return str(model_name)
    log.debug("Could not extract model name from %s", type(instance).__name__)
    return ""


def is_generator(obj: Any) -> bool:
    return isinstance(obj, Generator)


def is_async_generator(obj: Any) -> bool:
    return inspect.isasyncgen(obj)


# ---------------------------------------------------------------------------
# Trace-kwargs builders
#
# Each builder extracts the relevant arguments from the wrapped method call
# and returns a dict of keyword arguments for the LlmRequestEvent.  The dict
# always starts as a shallow copy of **kwargs so that standard parameters
# (like ``model``, ``temperature``, etc.) are available for tag extraction.
# ---------------------------------------------------------------------------


def _get_arg(args: tuple, kwargs: dict[str, Any], pos: int, name: str) -> Any:
    """Return the argument at *pos* / *name*, or None if not provided."""
    return get_argument_value(args, kwargs, pos, name, optional=True)


def _model_info(instance: Any) -> dict[str, Any]:
    """Return model name and optional max_tokens from an LLM instance."""
    info: dict[str, Any] = {"model": get_model_name(instance)}
    max_tokens = getattr(instance, "max_tokens", None)
    if max_tokens is not None:
        info["max_tokens"] = max_tokens
    return info


def build_chat_kwargs(instance: Any, args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Build trace kwargs for ``chat()`` / ``achat()``.

    Signature: ``chat(messages, **kwargs)``
    """
    kw = dict(kwargs)
    messages = _get_arg(args, kwargs, 0, "messages")
    if messages is not None:
        kw["messages"] = messages
    kw.update(_model_info(instance))
    return kw


def build_complete_kwargs(instance: Any, args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Build trace kwargs for ``complete()`` / ``acomplete()``.

    Signature: ``complete(prompt, **kwargs)``
    """
    kw = dict(kwargs)
    prompt = _get_arg(args, kwargs, 0, "prompt")
    if prompt is not None:
        kw["prompt"] = prompt
    kw.update(_model_info(instance))
    return kw


def build_predict_kwargs(instance: Any, args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Build trace kwargs for ``predict()`` / ``apredict()``.

    Signature: ``predict(prompt, **kwargs)``
    The prompt may be a PromptTemplate; extract its ``.template`` string.
    """
    kw = dict(kwargs)
    prompt = _get_arg(args, kwargs, 0, "prompt")
    if prompt is not None:
        kw["prompt"] = str(getattr(prompt, "template", None) or prompt)
    kw.update(_model_info(instance))
    return kw


def build_query_kwargs(instance: Any, args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Build trace kwargs for ``query()`` / ``aquery()``.

    Signature: ``query(str_or_query_bundle, **kwargs)``
    The argument may be a plain string or a QueryBundle with ``.query_str``.
    """
    kw = dict(kwargs)
    query = _get_arg(args, kwargs, 0, "str_or_query_bundle")
    if query is not None:
        kw["query_str"] = getattr(query, "query_str", str(query))
    return kw


def build_retrieve_kwargs(instance: Any, args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Build trace kwargs for ``retrieve()`` / ``aretrieve()``.

    Signature: ``retrieve(str_or_query_bundle, **kwargs)``
    Same extraction logic as query.
    """
    kw = dict(kwargs)
    query = _get_arg(args, kwargs, 0, "str_or_query_bundle")
    if query is not None:
        kw["query_str"] = getattr(query, "query_str", str(query))
    return kw


def build_embedding_kwargs(instance: Any, args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Build trace kwargs for ``get_query_embedding()`` / ``aget_query_embedding()``.

    Signature: ``get_query_embedding(query)``
    """
    kw = dict(kwargs)
    query = _get_arg(args, kwargs, 0, "query")
    if query is not None:
        kw["query"] = str(query)
    return kw


def build_embedding_batch_kwargs(instance: Any, args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Build trace kwargs for ``get_text_embedding_batch()`` / ``aget_text_embedding_batch()``.

    Signature: ``get_text_embedding_batch(texts)``
    Stores a summary string rather than the full text list.
    """
    kw = dict(kwargs)
    texts = _get_arg(args, kwargs, 0, "texts")
    if texts is not None:
        kw["query"] = "[%d texts]" % len(texts) if texts else ""
    return kw


def build_agent_run_kwargs(instance: Any, args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Build trace kwargs for agent ``run()``.

    Signature: ``run(user_msg, **kwargs)``
    The message may be a ChatMessage with ``.content``, or a plain string.
    """
    kw = dict(kwargs)
    user_msg = _get_arg(args, kwargs, 0, "user_msg")
    if user_msg is not None:
        kw["input"] = str(getattr(user_msg, "content", None) or user_msg)
    return kw


def build_agent_tool_kwargs(instance: Any, args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Build trace kwargs for agent ``call_tool()``.

    Signature: ``call_tool(ctx, ev, **kwargs)``
    The tool event *ev* is at position 1 and carries ``.tool_name``.
    """
    kw = dict(kwargs)
    ev = _get_arg(args, kwargs, 1, "ev")
    if ev is not None:
        tool_name = str(getattr(ev, "tool_name", ""))
        if tool_name:
            kw["tool_name"] = tool_name
    return kw
