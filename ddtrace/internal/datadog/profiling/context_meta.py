"""Compatibility names for the LP-owned propagated span linkage contract."""

from ddtrace.internal.span_lifecycle import LOCAL_ROOT_SPAN_ID_KEY as PROFILING_LOCAL_ROOT_SPAN_ID_KEY
from ddtrace.internal.span_lifecycle import SPAN_TYPE_KEY as PROFILING_SPAN_TYPE_KEY
from ddtrace.internal.span_lifecycle import attach_span_link as attach_profiler_link
from ddtrace.internal.span_lifecycle import read_span_link as read_profiler_link


__all__ = ["PROFILING_LOCAL_ROOT_SPAN_ID_KEY", "PROFILING_SPAN_TYPE_KEY", "attach_profiler_link", "read_profiler_link"]
