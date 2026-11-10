"""Profiler span linkage: the span link entry for a tracer item, and Context._meta keys across thread boundaries."""

import typing
from typing import Any
from typing import Optional
from typing import Union

from ddtrace.internal.native._native import Context
from ddtrace.internal.native._native import DefaultContextProvider
from ddtrace.internal.native._native import SpanData


PROFILING_LOCAL_ROOT_SPAN_ID_KEY = "_dd.profiling.local_root_span_id"
PROFILING_SPAN_TYPE_KEY = "_dd.profiling.span_type"

# The kind of tracer item a span link entry was built from. The values match SPAN_LINK_KIND_SPAN and
# SPAN_LINK_KIND_CONTEXT of the stack extension. They are repeated here because this module is imported without the
# profiler (the futures integration attaches profiler links), and must not load the stack extension.
SPAN_LINK_KIND_SPAN = 0
SPAN_LINK_KIND_CONTEXT = 1

# None ("no span") or (span_id, local_root_span_id, span_type, kind), as the stack extension's link() takes it.
SpanLinkEntry = Optional[tuple[int, int, Optional[str], int]]

ActiveItem = Optional[Union[SpanData, Context]]


def attach_profiler_link(ctx: Context, local_root_span_id: int, span_type: Optional[str]) -> None:
    """Attach profiler linkage to a Context copy without mutating a shared _meta dict."""
    ctx._meta = dict(ctx._meta)
    ctx._meta[PROFILING_LOCAL_ROOT_SPAN_ID_KEY] = f"{local_root_span_id:016x}"
    if span_type is not None:
        ctx._meta[PROFILING_SPAN_TYPE_KEY] = span_type


def read_profiler_link(ctx: Context) -> tuple[int, Optional[str]]:
    """Read profiler linkage from Context._meta, falling back to span_id for local root."""
    if ctx.span_id is None:
        raise ValueError("Context.span_id is required for profiler linkage")
    local_root_hex = ctx._meta.get(PROFILING_LOCAL_ROOT_SPAN_ID_KEY)
    if local_root_hex:
        local_root_span_id = int(local_root_hex, 16)
    else:
        local_root_span_id = ctx.span_id
    span_type = ctx._meta.get(PROFILING_SPAN_TYPE_KEY)
    return local_root_span_id, span_type


def resolve(item: ActiveItem) -> ActiveItem:
    """Return the item DefaultContextProvider.active() would return for item, without activating anything.

    This mirrors _update_active of the native context provider: a finished span is replaced by its parent span, and a
    finished root span by its parent Context only if that Context is marked for reactivation. It reads the parent
    links, so it stays correct when spans finish in any order.
    """
    while isinstance(item, SpanData) and item.finished:
        if item._parent is not None:
            item = item._parent
        else:
            parent_context = item._parent_context
            item = parent_context if parent_context is not None and parent_context._reactivate else None
    return item


def span_info(item: ActiveItem) -> SpanLinkEntry:
    """Return the span link entry for a tracer item, or None for "no span".

    A span gives a SPAN-kind entry. A root span continued from a propagated Context takes its local root from that
    Context, so that work offloaded to another thread keeps the local root of the trace that offloaded it. A Context
    gives a CONTEXT-kind entry. A Context with no span id, or with span id 0 (an invalid OpenTelemetry span context),
    gives None.
    """
    if isinstance(item, SpanData):
        local_root = typing.cast(Any, item)._local_root
        local_root_span_id = local_root.span_id
        local_root_span_type = local_root.span_type
        parent_context = item._parent_context
        if item._parent is None and item.parent_id is not None and parent_context is not None:
            propagated_root_span_id, propagated_root_span_type = read_profiler_link(parent_context)
            local_root_span_id = propagated_root_span_id or local_root_span_id
            local_root_span_type = propagated_root_span_type or local_root_span_type
        return (item.span_id, local_root_span_id, local_root_span_type, SPAN_LINK_KIND_SPAN)
    if isinstance(item, Context) and item.span_id:
        local_root_span_id, span_type = read_profiler_link(item)
        return (item.span_id, local_root_span_id, span_type, SPAN_LINK_KIND_CONTEXT)
    return None


# The last provider checked and its result. A provider rarely changes, so this keeps the check off the hot path.
_inheritance_mode_cache: tuple[Optional[object], bool] = (None, False)


def inheritance_mode(provider: object) -> bool:
    """Return whether provider's active() follows DefaultContextProvider: a finished span yields its unfinished
    ancestor.

    Only then can the profiler predict, with resolve(), what the provider will return after a span finishes. A
    provider that overrides active() or _update_active(), on its class or on the instance, has its own rule, so the
    profiler erases a finished span's links instead.
    """
    global _inheritance_mode_cache
    cached_provider, cached_result = _inheritance_mode_cache
    if provider is cached_provider:
        return cached_result
    result = False
    if isinstance(provider, DefaultContextProvider):
        provider_type = type(provider)
        instance_attributes = getattr(provider, "__dict__", None) or {}
        result = (
            provider_type.active is DefaultContextProvider.active
            and provider_type._update_active is DefaultContextProvider._update_active
            and "active" not in instance_attributes
            and "_update_active" not in instance_attributes
        )
    _inheritance_mode_cache = (provider, result)
    return result
