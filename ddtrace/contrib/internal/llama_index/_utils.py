"""Utility helpers for the LlamaIndex integration.

Data extraction from LlamaIndex objects (model names, kwargs building)
lives here, keeping patch.py focused on the wrapping/unwrapping logic.
"""

import asyncio
from typing import Any
from typing import Generator


def get_model_name(instance: Any) -> str:
    """Extract model name from a LlamaIndex LLM instance."""
    for attr in ("model", "model_name"):
        val = getattr(instance, attr, None)
        if val:
            return str(val)
    metadata = getattr(instance, "metadata", None)
    if metadata:
        model_name = getattr(metadata, "model_name", None)
        if model_name:
            return str(model_name)
    return ""


def is_generator(obj: Any) -> bool:
    return isinstance(obj, Generator)


def is_async_generator(obj: Any) -> bool:
    return hasattr(obj, "__aiter__") and hasattr(obj, "__anext__") and not asyncio.isfuture(obj)


# ---------------------------------------------------------------------------
# Kwargs builders — extract operation-specific data for the integration layer.
#
# All builders have the same signature: (instance, args, kwargs) -> dict.
# Non-LLM builders ignore ``instance``.
# ---------------------------------------------------------------------------


def _add_model_info(trace_kwargs: dict, instance: Any) -> None:
    """Add model name and max_tokens from an LLM instance into *trace_kwargs* (in-place)."""
    trace_kwargs["model"] = get_model_name(instance)
    max_tokens = getattr(instance, "max_tokens", None)
    if max_tokens is not None:
        trace_kwargs["max_tokens"] = max_tokens


def build_chat_kwargs(instance: Any, args: tuple, kwargs: dict) -> dict:
    trace_kwargs = dict(kwargs)
    if args:
        trace_kwargs["messages"] = args[0]
    elif "messages" in kwargs:
        trace_kwargs["messages"] = kwargs["messages"]
    _add_model_info(trace_kwargs, instance)
    return trace_kwargs


def build_complete_kwargs(instance: Any, args: tuple, kwargs: dict) -> dict:
    trace_kwargs = dict(kwargs)
    if args:
        trace_kwargs["prompt"] = args[0]
    elif "prompt" in kwargs:
        trace_kwargs["prompt"] = kwargs["prompt"]
    _add_model_info(trace_kwargs, instance)
    return trace_kwargs


def build_predict_kwargs(instance: Any, args: tuple, kwargs: dict) -> dict:
    """The first arg is a PromptTemplate; capture the template text as the prompt string."""
    trace_kwargs = dict(kwargs)
    if args:
        prompt_template = args[0]
        template = getattr(prompt_template, "template", None)
        trace_kwargs["prompt"] = str(template) if template else str(prompt_template)
    _add_model_info(trace_kwargs, instance)
    return trace_kwargs


def build_query_kwargs(instance: Any, args: tuple, kwargs: dict) -> dict:
    trace_kwargs = dict(kwargs)
    if args:
        query = args[0]
        # QueryBundle has a query_str attribute; plain strings are used directly
        trace_kwargs["query_str"] = getattr(query, "query_str", str(query))
    elif "str_or_query_bundle" in kwargs:
        query = kwargs["str_or_query_bundle"]
        trace_kwargs["query_str"] = getattr(query, "query_str", str(query))
    return trace_kwargs


def build_retrieve_kwargs(instance: Any, args: tuple, kwargs: dict) -> dict:
    trace_kwargs = dict(kwargs)
    if args:
        trace_kwargs["query_str"] = str(args[0])
    elif "str_or_query_bundle" in kwargs:
        query = kwargs["str_or_query_bundle"]
        trace_kwargs["query_str"] = getattr(query, "query_str", str(query))
    return trace_kwargs


def build_embedding_kwargs(instance: Any, args: tuple, kwargs: dict) -> dict:
    trace_kwargs = dict(kwargs)
    if args:
        trace_kwargs["query"] = str(args[0])
    elif "query" in kwargs:
        trace_kwargs["query"] = str(kwargs["query"])
    return trace_kwargs


def build_embedding_batch_kwargs(instance: Any, args: tuple, kwargs: dict) -> dict:
    trace_kwargs = dict(kwargs)
    if args:
        texts = args[0]
        trace_kwargs["query"] = "[%d texts]" % len(texts) if texts else ""
    elif "texts" in kwargs:
        texts = kwargs["texts"]
        trace_kwargs["query"] = "[%d texts]" % len(texts) if texts else ""
    return trace_kwargs


def build_agent_run_kwargs(instance: Any, args: tuple, kwargs: dict) -> dict:
    trace_kwargs = dict(kwargs)
    if args:
        user_msg = args[0]
        if user_msg is not None:
            content = getattr(user_msg, "content", None)
            trace_kwargs["input"] = str(content) if content else str(user_msg)
    elif "user_msg" in kwargs and kwargs["user_msg"] is not None:
        user_msg = kwargs["user_msg"]
        content = getattr(user_msg, "content", None)
        trace_kwargs["input"] = str(content) if content else str(user_msg)
    return trace_kwargs


def build_agent_tool_kwargs(instance: Any, args: tuple, kwargs: dict) -> dict:
    trace_kwargs = dict(kwargs)
    # call_tool receives (ctx, ev) where ev is a ToolCall event
    if len(args) >= 2:
        tool_call_ev = args[1]
        tool_name = getattr(tool_call_ev, "tool_name", None)
        if tool_name:
            trace_kwargs["tool_name"] = str(tool_name)
    elif "ev" in kwargs:
        tool_call_ev = kwargs["ev"]
        tool_name = getattr(tool_call_ev, "tool_name", None)
        if tool_name:
            trace_kwargs["tool_name"] = str(tool_name)
    return trace_kwargs
