"""Internal Context._meta keys for profiler span linkage across thread boundaries."""

from typing import Optional

from ddtrace._trace.context import Context


PROFILING_LOCAL_ROOT_SPAN_ID_KEY = "_dd.profiling.local_root_span_id"
PROFILING_SPAN_TYPE_KEY = "_dd.profiling.span_type"


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
