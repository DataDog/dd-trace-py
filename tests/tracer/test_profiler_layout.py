"""The profiler layout descriptor lets a reader find the active span from raw memory.

The reader below uses only the descriptor and ctypes memory reads, the way the stack profiler
will without the GIL, and is checked against the values Python sees.
"""

import contextvars
import copy
import ctypes
from pathlib import Path
import pickle
import re
import sys
from typing import Optional

import pytest

from ddtrace._trace.context import Context
from ddtrace._trace.span import Span
from ddtrace.internal.datadog.profiling import context_meta
from ddtrace.internal.native import _native
from ddtrace.internal.native._native import DD_CONTEXTVAR
from ddtrace.internal.native._native import DefaultContextProvider
from ddtrace.internal.native._native import SpanData


CAPSULE_NAME = b"ddtrace.internal.native._native._profiler_layout"
PY_TP_CLEAR = 51  # Include/typeslots.h


class _SpanOffsets(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint32)
        for name in (
            "copy_offset",
            "copy_size",
            "span_id",
            "parent_id",
            "parent",
            "local_root",
            "parent_context",
            "span_type_id",
            "finished",
            "_reserved",
        )
    ]


class _ContextOffsets(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint32)
        for name in ("copy_offset", "copy_size", "span_id", "local_root_span_id", "span_type_id", "reactivate")
    ]


class _SpanTypeEntry(ctypes.Structure):
    _fields_ = [("data", ctypes.c_void_p), ("len", ctypes.c_uint32), ("_reserved", ctypes.c_uint32)]


class _SpanTypeTable(ctypes.Structure):
    _fields_ = [
        ("entries", ctypes.POINTER(_SpanTypeEntry)),
        ("count", ctypes.POINTER(ctypes.c_uint32)),
        ("capacity", ctypes.c_uint32),
        ("unavailable_id", ctypes.c_uint32),
    ]


class _Layout(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_uint32),
        ("size", ctypes.c_uint32),
        ("contextvar", ctypes.c_void_p),
        ("contextvar_hash", ctypes.c_ssize_t),
        ("contextvar_hamt_hash", ctypes.c_uint32),
        ("_reserved", ctypes.c_uint32),
        ("span_data_type", ctypes.c_void_p),
        ("span_type", ctypes.c_void_p),
        ("context_type", ctypes.c_void_p),
        ("span", _SpanOffsets),
        ("context", _ContextOffsets),
        ("span_types", _SpanTypeTable),
    ]


def _layout() -> _Layout:
    get_pointer = ctypes.pythonapi.PyCapsule_GetPointer
    get_pointer.restype = ctypes.c_void_p
    get_pointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
    return _Layout.from_address(get_pointer(_native._profiler_layout, CAPSULE_NAME))


LAYOUT = _layout()
OB_TYPE_OFFSET = ctypes.sizeof(ctypes.c_ssize_t)


def _uint(data: bytes, offset: int, size: int) -> int:
    return int.from_bytes(data[offset : offset + size], sys.byteorder)


class Fields:
    """Fields of one object, read with one copy of its descriptor range."""

    def __init__(self, addr: int, offsets):
        self._base = offsets.copy_offset
        self._data = ctypes.string_at(addr + offsets.copy_offset, offsets.copy_size)
        self._offsets = offsets

    def get(self, name: str, size: int) -> int:
        return _uint(self._data, getattr(self._offsets, name) - self._base, size)


PTR = ctypes.sizeof(ctypes.c_void_p)


class RawSpan:
    def __init__(self, addr: int):
        f = Fields(addr, LAYOUT.span)
        self.span_id = f.get("span_id", 8)
        self.parent_id = f.get("parent_id", 8)
        self.parent = f.get("parent", PTR)
        self.local_root = f.get("local_root", PTR)
        self.parent_context = f.get("parent_context", PTR)
        self.span_type_id = f.get("span_type_id", 4)
        self.finished = f.get("finished", 1)


class RawContext:
    def __init__(self, addr: int):
        f = Fields(addr, LAYOUT.context)
        self.span_id = f.get("span_id", 8)
        self.local_root_span_id = f.get("local_root_span_id", 8)
        self.span_type_id = f.get("span_type_id", 4)
        self.reactivate = f.get("reactivate", 1)


def span_type_name(type_id: int) -> Optional[str]:
    """Turn a span type id back into its string, as the profiler's renderer will."""
    table = LAYOUT.span_types
    if type_id in (0, table.unavailable_id):
        return None
    assert type_id < table.count[0] <= table.capacity
    entry = table.entries[type_id]
    return ctypes.string_at(entry.data, entry.len).decode("utf-8")


def type_of(addr: int) -> int:
    return _uint(ctypes.string_at(addr + OB_TYPE_OFFSET, PTR), 0, PTR)


def _span_types(extra=()):
    return {LAYOUT.span_data_type, LAYOUT.span_type, *(id(t) for t in extra)}


def resolve(addr: int, span_types) -> int:
    """Section 3 of the design: mirror DefaultContextProvider._update_active without activating."""
    for _ in range(64):
        if addr == 0 or addr == id(None):
            return 0
        if type_of(addr) == LAYOUT.context_type:
            return addr
        assert type_of(addr) in span_types
        span = RawSpan(addr)
        if not span.finished:
            return addr
        if span.parent == 0 and span.parent_context and RawContext(span.parent_context).reactivate:
            return span.parent_context
        addr = span.parent
    raise AssertionError("too many hops")


def raw_labels(addr: int):
    """Section 3 of the design: the labels StackCollector._link_span attaches, from memory."""
    if addr == 0:
        return None
    if type_of(addr) == LAYOUT.context_type:
        ctx = RawContext(addr)
        if ctx.span_id == 0:
            return None
        return (
            ctx.span_id,
            ctx.local_root_span_id or ctx.span_id,
            span_type_name(ctx.span_type_id),
        )
    span = RawSpan(addr)
    if span.span_id == 0:
        return None
    root = RawSpan(span.local_root or addr)
    if span.parent == 0 and span.parent_id != 0 and span.parent_context != 0:
        ctx = RawContext(span.parent_context)
        root_id = ctx.local_root_span_id or ctx.span_id or root.span_id
        span_type = span_type_name(ctx.span_type_id) or span_type_name(root.span_type_id)
        return span.span_id, root_id, span_type
    return span.span_id, root.span_id, span_type_name(root.span_type_id)


def python_labels(value):
    """StackCollector._link_span and read_profiler_link, plus the rule that span id 0 gives none."""
    if isinstance(value, SpanData):
        local_root = value._local_root
        if value._parent is None and value.parent_id is not None and value._parent_context is not None:
            root_id, root_type = context_meta.read_profiler_link(value._parent_context)
            root_id = root_id or local_root.span_id
            root_type = root_type or local_root.span_type
        else:
            root_id, root_type = local_root.span_id, local_root.span_type
        labels = (value.span_id, root_id, root_type or None)
    elif isinstance(value, Context) and value.span_id is not None:
        root_id, root_type = context_meta.read_profiler_link(value)
        labels = (value.span_id, root_id, root_type or None)
    else:
        return None
    labels = (labels[0] & (2**64 - 1), labels[1] & (2**64 - 1), labels[2])
    return labels if labels[0] else None


def python_active(value):
    """What DefaultContextProvider.active() returns for value, without touching this Context."""

    def run():
        DD_CONTEXTVAR.set(value)
        return DefaultContextProvider().active()

    return contextvars.Context().run(run)


class MySpan(Span):
    pass


class MyMixin:
    pass


class MyMixedSpan(MyMixin, Span):
    pass


def test_descriptor_header():
    assert LAYOUT.version == 1
    assert LAYOUT.size == ctypes.sizeof(_Layout)
    assert LAYOUT.contextvar == id(DD_CONTEXTVAR)
    assert LAYOUT.contextvar_hash == hash(DD_CONTEXTVAR)
    h = hash(DD_CONTEXTVAR) & (2**64 - 1)
    folded = ((h & 0xFFFFFFFF) ^ (h >> 32)) if PTR == 8 else h
    assert LAYOUT.contextvar_hamt_hash == (0xFFFFFFFE if folded == 0xFFFFFFFF else folded)
    assert LAYOUT.span_data_type == id(SpanData)
    assert LAYOUT.span_type == id(Span)
    assert LAYOUT.context_type == id(Context)
    assert _native._profiler_layout_set_span_type(Span) is True
    assert _native._profiler_layout_set_span_type(MySpan) is False
    assert LAYOUT.span_type == id(Span)
    with pytest.raises(TypeError):
        _native._profiler_layout_set_span_type(int)


@pytest.mark.parametrize("offsets", [LAYOUT.span, LAYOUT.context])
def test_one_copy_holds_every_field(offsets):
    names = [name for name, _ in offsets._fields_ if name not in ("copy_offset", "copy_size", "_reserved")]
    assert offsets.copy_size <= 64
    for name in names:
        assert offsets.copy_offset <= getattr(offsets, name) < offsets.copy_offset + offsets.copy_size


@pytest.mark.parametrize("cls", [SpanData, Span, MySpan, MyMixedSpan])
def test_span_fields(cls):
    root_context = Context(trace_id=1, span_id=2)
    root = cls("root", span_type="web", span_id=11, context=root_context)
    child = cls("child", span_type="sql", span_id=12, parent_id=11, context=root_context)
    child._inherit_from_parent(root)

    raw_root = RawSpan(id(root))
    assert (raw_root.span_id, raw_root.parent_id) == (11, 0)
    assert (raw_root.parent, raw_root.local_root, raw_root.parent_context) == (0, 0, id(root_context))
    assert span_type_name(raw_root.span_type_id) == "web"
    assert raw_root.finished == 0

    raw_child = RawSpan(id(child))
    assert (raw_child.span_id, raw_child.parent_id) == (12, 11)
    assert (raw_child.parent, raw_child.local_root) == (id(root), id(root))
    assert span_type_name(raw_child.span_type_id) == "sql"

    untyped = cls("untyped")
    raw = RawSpan(id(untyped))
    assert (raw.parent, raw.local_root, raw.parent_context, raw.span_type_id) == (0, 0, 0, 0)
    assert raw.span_id == untyped.span_id


def _tp_clear(obj):
    get_slot = ctypes.pythonapi.PyType_GetSlot
    get_slot.restype = ctypes.c_void_p
    get_slot.argtypes = [ctypes.py_object, ctypes.c_int]
    clear = ctypes.PYFUNCTYPE(ctypes.c_int, ctypes.py_object)(get_slot(SpanData, PY_TP_CLEAR))
    assert clear(obj) == 0


def test_finished_flag_tracks_every_duration_setter():
    span = Span("s")

    def finished():
        assert RawSpan(id(span)).finished == int(span.finished)
        return span.finished

    assert not finished()
    span.duration_ns = 5
    assert finished()
    span.duration_ns = None
    assert not finished()
    span.duration = 1.5
    assert finished()
    span.duration_ns = "not a number"
    assert not finished()
    span.duration = 2
    assert finished()
    _tp_clear(span)
    assert not finished()

    span = Span("s")
    span.finish()
    assert finished()


@pytest.mark.parametrize(
    "value",
    ["web", "sql", "", None, "x" * 5000, "spän-type-ü-日本語-🙂", b"redis", 42],
)
def test_span_type_id_tracks_span_type(value):
    span = Span("s", span_type=value)
    expected = span.span_type
    if isinstance(expected, bytes):
        expected = expected.decode()
    assert span_type_name(RawSpan(id(span)).span_type_id) == (expected or None)

    span = Span("s", span_type="before")
    span.span_type = value
    assert span_type_name(RawSpan(id(span)).span_type_id) == (expected or None)


def test_span_type_ids_are_stable():
    a = RawSpan(id(Span("a", span_type="stable-type"))).span_type_id
    b = RawSpan(id(Span("b", span_type="".join(["stable", "-type"])))).span_type_id
    other = RawSpan(id(Span("c", span_type="other-type"))).span_type_id
    assert a == b != other
    entry = LAYOUT.span_types.entries[a]
    assert ctypes.string_at(entry.data, entry.len + 1).endswith(b"\0")


@pytest.mark.subprocess()
def test_span_type_table_full():
    from ddtrace._trace.span import Span
    from tests.tracer.test_profiler_layout import LAYOUT
    from tests.tracer.test_profiler_layout import RawSpan
    from tests.tracer.test_profiler_layout import span_type_name

    table = LAYOUT.span_types
    first = Span("s", span_type="kept-type")
    for i in range(table.capacity + 10):
        Span("s", span_type="filler-%d" % i)
    assert table.count[0] == table.capacity
    late = Span("s", span_type="late-type")
    assert RawSpan(id(late)).span_type_id == table.unavailable_id
    assert span_type_name(RawSpan(id(late)).span_type_id) is None
    # The Python value is unaffected, and known types still resolve.
    assert late.span_type == "late-type"
    assert span_type_name(RawSpan(id(first)).span_type_id) == "kept-type"
    assert span_type_name(RawSpan(id(Span("s", span_type="kept-type"))).span_type_id) == "kept-type"


def _raw_context_matches(ctx):
    raw = RawContext(id(ctx))
    assert raw.span_id == (ctx.span_id or 0) & (2**64 - 1)
    assert raw.reactivate == int(ctx._reactivate)
    root_hex = ctx._meta.get(context_meta.PROFILING_LOCAL_ROOT_SPAN_ID_KEY)
    assert raw.local_root_span_id == (int(root_hex, 16) if root_hex else 0)
    assert span_type_name(raw.span_type_id) == (ctx._meta.get(context_meta.PROFILING_SPAN_TYPE_KEY) or None)
    return raw


def test_context_span_id_mirror_tracks_every_setter():
    ctx = Context()
    assert _raw_context_matches(ctx).span_id == 0
    for value in (5, "123", 2**64 + 7, None, 0, 2**64 - 1):
        ctx.span_id = value
        _raw_context_matches(ctx)
    assert _raw_context_matches(Context(span_id=9)).span_id == 9
    assert _raw_context_matches(Context(trace_id=1, span_id=3).copy(1, 4)).span_id == 4
    span = Span("root", span_id=77)
    assert _raw_context_matches(span.context).span_id == 77
    assert _raw_context_matches(Span("child", span_id=78, context=span.context).context).span_id == 78


def test_context_reactivate_mirror():
    ctx = Context(trace_id=1, span_id=2)
    for value in (True, False, True):
        ctx._reactivate = value
        _raw_context_matches(ctx)
    assert RawContext(id(pickle.loads(pickle.dumps(ctx)))).reactivate == 1


def _linked_context():
    ctx = Context(trace_id=1, span_id=2, meta={"other": "tag"})
    context_meta.attach_profiler_link(ctx, local_root_span_id=0xABC, span_type="web")
    return ctx


def _nested_executor_copy(ctx):
    # ddtrace/contrib/internal/futures/threading.py copies the submitting Context, without
    # calling attach_profiler_link when no span is active.
    return ctx.copy(ctx.trace_id, ctx.span_id)


@pytest.mark.parametrize(
    "make",
    [
        pytest.param(_linked_context, id="attach_profiler_link"),
        pytest.param(lambda: Context(trace_id=1, span_id=2, meta=dict(_linked_context()._meta)), id="constructor"),
        pytest.param(lambda: _linked_context().copy(1, 3), id="copy"),
        pytest.param(lambda: _nested_executor_copy(_nested_executor_copy(_linked_context())), id="nested_executor"),
        pytest.param(lambda: Span("child", context=_linked_context()).context, id="span_context"),
        pytest.param(lambda: _linked_context()._with_baggage_item("k", "v"), id="with_baggage_item"),
        pytest.param(lambda: pickle.loads(pickle.dumps(_linked_context())), id="pickle"),
        pytest.param(lambda: copy.copy(_linked_context()), id="copy.copy"),
        pytest.param(lambda: copy.deepcopy(_linked_context()), id="copy.deepcopy"),
    ],
)
def test_profiler_link_survives_every_context_path(make):
    ctx = make()
    raw = _raw_context_matches(ctx)
    assert raw.local_root_span_id == 0xABC
    assert span_type_name(raw.span_type_id) == "web"
    assert raw_labels(id(ctx)) == python_labels(ctx) == (ctx.span_id, 0xABC, "web")


def test_profiler_link_follows_meta_replacement():
    ctx = _linked_context()
    ctx._meta = {}
    assert _raw_context_matches(ctx).local_root_span_id == 0
    ctx._meta = {context_meta.PROFILING_LOCAL_ROOT_SPAN_ID_KEY: "not hex"}
    assert RawContext(id(ctx)).local_root_span_id == 0
    ctx._meta = {context_meta.PROFILING_SPAN_TYPE_KEY: "worker"}
    assert span_type_name(RawContext(id(ctx)).span_type_id) == "worker"


def _scenarios(cls=Span):
    remote = Context(trace_id=5, span_id=50)
    linked_remote = _linked_context()
    linked_remote.span_id = 60

    root = cls("root", span_type="web", context=None)
    child = cls("child", span_type="sql", context=root.context, parent_id=root.span_id)
    child._inherit_from_parent(root)
    finished_child = cls("done", context=root.context, parent_id=root.span_id)
    finished_child._inherit_from_parent(root)
    finished_child.finish()

    distributed = cls("entry", span_type="web", context=remote, parent_id=50)
    linked_distributed = cls("worker", span_type="custom", context=linked_remote, parent_id=60)

    reactivating = Context(trace_id=7, span_id=70)
    reactivating._reactivate = True
    finished_root = cls("finished-root", context=reactivating, parent_id=70)
    finished_root.finish()
    finished_root_no_reactivate = cls("gone", context=Context(trace_id=8, span_id=80), parent_id=80)
    finished_root_no_reactivate.finish()

    zero = cls("zero", span_id=0)
    return {
        "root": root,
        "child": child,
        "finished_child": finished_child,
        "distributed": distributed,
        "linked_distributed": linked_distributed,
        "finished_root_reactivates": finished_root,
        "finished_root": finished_root_no_reactivate,
        "context": remote,
        "linked_context": linked_remote,
        "context_without_span_id": Context(trace_id=9),
        "span_id_zero": zero,
        "none": None,
    }


@pytest.mark.parametrize("name", list(_scenarios()))
@pytest.mark.parametrize("cls", [Span, MySpan])
def test_raw_reader_matches_tracer(name, cls):
    value = _scenarios(cls)[name]
    active = python_active(value)
    raw_active = resolve(id(value), _span_types([MySpan]))
    assert raw_active == (id(active) if active is not None else 0)
    assert raw_labels(raw_active) == python_labels(active)


def test_raw_reader_matches_tracer_spans(tracer):
    with tracer.trace("root", span_type="web") as root:
        with tracer.trace("child", span_type="sql") as child:
            assert raw_labels(resolve(id(child), _span_types())) == (child.span_id, root.span_id, "web")
            root.span_type = "worker"
            assert raw_labels(resolve(id(child), _span_types())) == (child.span_id, root.span_id, "worker")
        assert child.finished
        assert raw_labels(resolve(id(child), _span_types())) == (root.span_id, root.span_id, "worker")


def test_parent_pointers_are_not_reassigned_after_publication(tracer):
    """The profiler follows _parent, _local_root and _parent_context without the GIL. The tracer sets
    them before a span is published and never changes them afterwards.
    """
    from ddtrace.internal import core

    seen = {}

    def snapshot(span):
        return id(span._parent), id(span._local_root), id(span._parent_context)

    def on_activate(provider, value):
        if isinstance(value, SpanData):
            seen.setdefault(id(value), (value, snapshot(value)))

    core.on("ddtrace.context_provider.activate", on_activate, "test_profiler_layout")
    try:
        remote = Context(trace_id=1, span_id=2)
        with tracer.trace("root", span_type="web"):
            with tracer.trace("child"):
                tracer.start_span("manual", child_of=tracer.current_span(), activate=True).finish()
        with tracer.start_span("distributed", child_of=remote, activate=True):
            with tracer.trace("grandchild"):
                pass
    finally:
        core.reset_listeners("ddtrace.context_provider.activate", on_activate)

    assert len(seen) == 5
    for span, at_activation in seen.values():
        assert snapshot(span) == at_activation


def test_parent_pointers_are_only_set_in_known_places():
    """Writes to _parent, _local_root and _parent_context must stay before publication. New writers
    must keep that invariant, then be added here.
    """
    root = Path(__file__).resolve().parents[2] / "ddtrace"
    pattern = re.compile(
        r"\.(_parent|_local_root|_parent_context)\s*=[^=]|setattr\([^)]*[\"'](_parent|_local_root|_parent_context)[\"']"
    )
    allowed = {
        # ExecutionContext._parent, not a span.
        "internal/core/__init__.py",
        # A test double for the profiler's span interface.
        "internal/datadog/profiling/ddup/test/interface.py",
    }
    writers = {
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8", errors="replace"))
    }
    assert writers <= allowed, writers - allowed
