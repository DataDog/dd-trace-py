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


def build_chat_request_kwargs(instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[dict[str, Any], str]:
    request_kwargs = dict(kwargs)
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
    request_kwargs = dict(kwargs)
    prompt = get_argument_value(args, kwargs, 0, "prompt", optional=True)
    if prompt is not None:
        request_kwargs["prompt"] = prompt
    model = get_model_name(instance)
    request_kwargs["model"] = model
    max_tokens = getattr(instance, "max_tokens", None)
    if max_tokens is not None:
        request_kwargs["max_tokens"] = max_tokens
    return request_kwargs, model


def build_predict_request_kwargs(instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[dict[str, Any], str]:
    request_kwargs = dict(kwargs)
    prompt = get_argument_value(args, kwargs, 0, "prompt", optional=True)
    if prompt is not None:
        request_kwargs["prompt"] = str(getattr(prompt, "template", None) or prompt)
    model = get_model_name(instance)
    request_kwargs["model"] = model
    max_tokens = getattr(instance, "max_tokens", None)
    if max_tokens is not None:
        request_kwargs["max_tokens"] = max_tokens
    return request_kwargs, model


def build_query_request_kwargs(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    request_kwargs = dict(kwargs)
    query = get_argument_value(args, kwargs, 0, "str_or_query_bundle", optional=True)
    if query is not None:
        request_kwargs["query_str"] = getattr(query, "query_str", str(query))
    return request_kwargs


def build_query_embedding_request_kwargs(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    request_kwargs = dict(kwargs)
    query = get_argument_value(args, kwargs, 0, "query", optional=True)
    if query is not None:
        request_kwargs["query"] = str(query)
    return request_kwargs


def build_text_embedding_batch_request_kwargs(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    request_kwargs = dict(kwargs)
    texts = get_argument_value(args, kwargs, 0, "texts", optional=True)
    if texts is not None:
        request_kwargs["query"] = "[%d texts]" % len(texts) if texts else ""
    return request_kwargs


def build_agent_run_request_kwargs(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    request_kwargs = dict(kwargs)
    user_msg = get_argument_value(args, kwargs, 0, "user_msg", optional=True)
    if user_msg is not None:
        request_kwargs["input"] = str(getattr(user_msg, "content", None) or user_msg)
    return request_kwargs


def build_agent_call_tool_request_kwargs(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    request_kwargs = dict(kwargs)
    ev = get_argument_value(args, kwargs, 1, "ev", optional=True)
    if ev is not None:
        tool_name = str(getattr(ev, "tool_name", ""))
        if tool_name:
            request_kwargs["tool_name"] = tool_name
    return request_kwargs
