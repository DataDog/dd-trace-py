import pickle

from ddtrace._trace.context import Context
from ddtrace.internal.datadog.profiling import context_meta


def test_attach_profiler_link_copies_meta():
    parent_meta = {"existing": "tag"}
    ctx = Context(trace_id=1, span_id=2, meta=parent_meta)
    context_meta.attach_profiler_link(ctx, local_root_span_id=99, span_type="web")
    assert ctx._meta is not parent_meta
    assert parent_meta == {"existing": "tag"}
    assert ctx._meta[context_meta.PROFILING_LOCAL_ROOT_SPAN_ID_KEY] == "0000000000000063"
    assert ctx._meta[context_meta.PROFILING_SPAN_TYPE_KEY] == "web"


def test_read_profiler_link_fallback_to_span_id():
    ctx = Context(trace_id=1, span_id=42)
    local_root, span_type = context_meta.read_profiler_link(ctx)
    assert local_root == 42
    assert span_type is None


def test_read_profiler_link_from_meta():
    ctx = Context(trace_id=1, span_id=2)
    context_meta.attach_profiler_link(ctx, local_root_span_id=99, span_type="sql")
    local_root, span_type = context_meta.read_profiler_link(ctx)
    assert local_root == 99
    assert span_type == "sql"


def test_profiler_meta_round_trips_in_pickle():
    ctx = Context(trace_id=123, span_id=321)
    context_meta.attach_profiler_link(ctx, local_root_span_id=789, span_type="web")
    restored = pickle.loads(pickle.dumps(ctx))
    local_root, span_type = context_meta.read_profiler_link(restored)
    assert local_root == 789
    assert span_type == "web"
