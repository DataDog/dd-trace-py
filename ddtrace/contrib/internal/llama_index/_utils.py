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
    return ""


def is_generator(obj: Any) -> bool:
    return isinstance(obj, Generator)


def is_async_generator(obj: Any) -> bool:
    return inspect.isasyncgen(obj)


def _extract_arg(args: tuple, kwargs: dict[str, Any], pos: int, kw: str) -> Any:
    """Extract a single argument by position or keyword, returning None if absent."""
    return get_argument_value(args, kwargs, pos, kw, optional=True)


def _build_kwargs(instance: Any, args: tuple, kwargs: dict[str, Any], extractions: dict) -> dict[str, Any]:
    """Generic trace-kwargs builder.

    *extractions* maps output key -> (pos, kw_name, transform) where transform
    is an optional callable applied to the extracted value before storing it.
    """
    trace_kwargs: dict[str, Any] = dict(kwargs)
    for output_key, (pos, kw_name, transform) in extractions.items():
        val = _extract_arg(args, kwargs, pos, kw_name)
        if val is not None:
            trace_kwargs[output_key] = transform(val) if transform else val
    return trace_kwargs


def _add_model_info(trace_kwargs: dict[str, Any], instance: Any) -> None:
    """Add model name and max_tokens from an LLM instance into *trace_kwargs* (in-place)."""
    trace_kwargs["model"] = get_model_name(instance)
    max_tokens = getattr(instance, "max_tokens", None)
    if max_tokens is not None:
        trace_kwargs["max_tokens"] = max_tokens


def build_chat_kwargs(instance: Any, args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
    kw = _build_kwargs(instance, args, kwargs, {"messages": (0, "messages", None)})
    _add_model_info(kw, instance)
    return kw


def build_complete_kwargs(instance: Any, args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
    kw = _build_kwargs(instance, args, kwargs, {"prompt": (0, "prompt", None)})
    _add_model_info(kw, instance)
    return kw


def build_predict_kwargs(instance: Any, args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
    kw = _build_kwargs(
        instance, args, kwargs, {"prompt": (0, "prompt", lambda v: str(getattr(v, "template", None) or v))}
    )
    _add_model_info(kw, instance)
    return kw


def build_query_kwargs(instance: Any, args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
    return _build_kwargs(
        instance, args, kwargs, {"query_str": (0, "str_or_query_bundle", lambda v: getattr(v, "query_str", str(v)))}
    )


def build_retrieve_kwargs(instance: Any, args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
    return _build_kwargs(
        instance, args, kwargs, {"query_str": (0, "str_or_query_bundle", lambda v: getattr(v, "query_str", str(v)))}
    )


def build_embedding_kwargs(instance: Any, args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
    return _build_kwargs(instance, args, kwargs, {"query": (0, "query", str)})


def build_embedding_batch_kwargs(instance: Any, args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
    return _build_kwargs(instance, args, kwargs, {"query": (0, "texts", lambda v: "[%d texts]" % len(v) if v else "")})


def build_agent_run_kwargs(instance: Any, args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
    return _build_kwargs(
        instance, args, kwargs, {"input": (0, "user_msg", lambda v: str(getattr(v, "content", None) or v))}
    )


def build_agent_tool_kwargs(instance: Any, args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
    kw = _build_kwargs(
        instance, args, kwargs, {"tool_name": (1, "ev", lambda v: str(getattr(v, "tool_name", "")) or None)}
    )
    if kw.get("tool_name") is None:
        kw.pop("tool_name", None)
    return kw
