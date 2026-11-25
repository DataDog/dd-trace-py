"""The stack profiler's raw contextvars.Context reader, checked against the tracer.

The reader must find the span DefaultContextProvider.active() returns for a Context and the labels
StackCollector._link_span attaches for it, except that a span id of 0 gives no labels.
"""

import contextvars
import random
import threading
from typing import Any
from typing import Optional

import pytest

from ddtrace._trace.context import Context
from ddtrace._trace.span import Span
from ddtrace.internal.datadog.profiling import context_meta
from ddtrace.internal.datadog.profiling import stack as stack_module
from ddtrace.internal.native._native import DD_CONTEXTVAR
from ddtrace.profiling.collector.stack import StackCollector
from ddtrace.trace import Tracer


pytestmark = pytest.mark.skipif(not stack_module.is_available, reason="stack profiler not available")

MASK = 2**64 - 1
Labels = Optional[tuple[int, int, Optional[str]]]


class MySpan(Span):
    pass


class MyMixin:
    pass


class MyMixedSpan(MyMixin, Span):
    pass


@pytest.fixture(scope="module")
def native() -> Any:
    from ddtrace.internal.datadog.profiling.stack import _stack

    assert _stack._context_reader_error() is None
    return _stack


@pytest.fixture
def oracle(monkeypatch: pytest.MonkeyPatch, tracer: Tracer) -> Any:
    """The span active() resolves for a Context, and the labels _link_span attaches for it."""
    calls: list = []
    monkeypatch.setattr(stack_module, "link_span", lambda *args: calls.append(args))
    monkeypatch.setattr(stack_module, "clear_span", lambda: calls.append(None))
    collector = StackCollector(tracer=tracer)
    provider = tracer.context_provider

    def expected(ctx: contextvars.Context) -> tuple[Any, Labels]:
        # active() can activate a parent, so run it on a copy.
        active = ctx.copy().run(provider.active)
        calls.clear()
        collector._link_span(provider, active)
        (call,) = calls
        if call is None:
            return active, None
        span_id, root_id, span_type = call
        labels = (span_id & MASK, root_id & MASK, span_type or None)
        return active, labels if labels[0] else None

    return expected


def holding(value: Any) -> contextvars.Context:
    ctx = contextvars.Context()
    ctx.run(DD_CONTEXTVAR.set, value)
    return ctx


def read(native: Any, ctx: contextvars.Context) -> tuple[int, Labels]:
    status, span_id, root_id, span_type, resolved = native._read_context_labels(ctx)
    assert status in ("ok", "no_labels"), status
    return resolved, ((span_id, root_id, span_type) if status == "ok" else None)


def assert_matches(native: Any, oracle: Any, ctx: contextvars.Context) -> Labels:
    active, labels = oracle(ctx)
    resolved, raw_labels = read(native, ctx)
    assert resolved == (id(active) if active is not None else 0)
    assert raw_labels == labels
    return labels


def linked(span_id: int, local_root_span_id: Optional[int] = None, span_type: Optional[str] = None) -> Context:
    ctx = Context(trace_id=1, span_id=span_id, meta={"other": "tag"})
    if local_root_span_id is not None:
        context_meta.attach_profiler_link(ctx, local_root_span_id=local_root_span_id, span_type=span_type)
    return ctx


def child_of(cls: type, parent: Span, **kwargs: Any) -> Span:
    child = cls("child", context=parent.context, parent_id=parent.span_id, **kwargs)
    child._inherit_from_parent(parent)
    return child


def scenarios(cls: type) -> dict[str, Any]:
    root = cls("root", span_type="web")
    child = child_of(cls, root, span_type="sql")
    finished_child = child_of(cls, root)
    finished_child.finish()
    grandchild = child_of(cls, child)
    finished_grandchild = child_of(cls, grandchild)
    finished_grandchild.finish()

    reactivating = linked(70, 0x77, "worker")
    reactivating._reactivate = True
    finished_root = cls("finished-root", context=reactivating, parent_id=70)
    finished_root.finish()
    finished_root_no_reactivate = cls("gone", context=Context(trace_id=8, span_id=80), parent_id=80)
    finished_root_no_reactivate.finish()

    return {
        "root": root,
        "child": child,
        "finished_child": finished_child,
        "finished_grandchild": finished_grandchild,
        "distributed": cls("entry", span_type="web", context=Context(trace_id=5, span_id=50), parent_id=50),
        "distributed_with_meta": cls("worker", span_type="custom", context=linked(60, 0xABC, "web"), parent_id=60),
        "distributed_with_root_only": cls("worker", span_type="custom", context=linked(61, 0xABD), parent_id=61),
        "finished_root_reactivates": finished_root,
        "finished_root": finished_root_no_reactivate,
        "context": Context(trace_id=5, span_id=50),
        "context_with_meta": linked(60, 0xABC, "web"),
        "context_with_root_only": linked(61, 0xABD),
        "context_without_span_id": Context(trace_id=9),
        "context_128_bit_span_id": Context(trace_id=9, span_id=2**64 + 5),
        "span_id_zero": cls("zero", span_id=0),
        "child_of_span_id_zero": child_of(cls, cls("zero-root", span_id=0)),
        "none": None,
    }


@pytest.mark.parametrize("name", list(scenarios(Span)))
@pytest.mark.parametrize("cls", [Span, MySpan, MyMixedSpan])
def test_reader_matches_tracer(native: Any, oracle: Any, name: str, cls: type) -> None:
    before = native._context_reader_failures()
    assert_matches(native, oracle, holding(scenarios(cls)[name]))
    assert native._context_reader_failures() == before


def test_span_id_zero_has_no_labels(native: Any) -> None:
    span = Span("zero", span_id=0)
    assert read(native, holding(span)) == (id(span), None)


def test_missing_key(native: Any) -> None:
    assert read(native, contextvars.Context()) == (0, None)
    unrelated = contextvars.ContextVar("unrelated")
    ctx = contextvars.Context()
    ctx.run(unrelated.set, Span("s"))
    assert read(native, ctx) == (0, None)


def test_other_value_types_count_as_failures(native: Any, oracle: Any) -> None:
    ctx = holding(42)
    assert oracle(ctx) == (42, None)
    before = native._context_reader_failures()["bad_value_type"]
    assert native._read_context_labels(ctx)[0] == "bad_value_type"
    assert native._context_reader_failures()["bad_value_type"] == before + 1


def test_many_context_variables(native: Any, oracle: Any) -> None:
    """The tracer's variable among enough others for array and collision-free deep nodes."""
    span = Span("s", span_type="web")
    others = [contextvars.ContextVar("v%d" % i) for i in range(300)]

    def fill() -> contextvars.Context:
        for i, var in enumerate(others):
            var.set(i)
            if i == 150:
                DD_CONTEXTVAR.set(span)
        return contextvars.copy_context()

    ctx = contextvars.Context().run(fill)
    assert assert_matches(native, oracle, ctx) == (span.span_id, span.span_id, "web")


def test_tracer_spans(native: Any, oracle: Any, tracer: Tracer) -> None:
    with tracer.trace("root", span_type="web") as root:
        with tracer.trace("child", span_type="sql") as child:
            ctx = contextvars.copy_context()
            assert assert_matches(native, oracle, ctx) == (child.span_id, root.span_id, "web")
            root.span_type = "worker"
            assert assert_matches(native, oracle, ctx) == (child.span_id, root.span_id, "worker")
        # The child's Context still holds it, finished: the reader walks to the root.
        assert assert_matches(native, oracle, ctx) == (root.span_id, root.span_id, "worker")


def test_other_threads_context(native: Any, oracle: Any, tracer: Tracer) -> None:
    captured: dict = {}

    def run() -> None:
        with tracer.trace("thread-root", span_type="worker") as span:
            captured["span"] = span
            captured["ctx"] = contextvars.copy_context()

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    span = captured["span"]
    assert span.finished
    assert_matches(native, oracle, captured["ctx"])


def random_tree(rng: random.Random) -> list[Any]:
    """Spans and Contexts with random parents, local roots, finish states, types and meta."""
    types = [None, "", "web", "sql", "worker"]
    values: list[Any] = [None]
    spans: list[Span] = []
    for _ in range(rng.randint(1, 12)):
        cls = rng.choice([Span, MySpan, MyMixedSpan])
        span_type = rng.choice(types)
        kind = rng.random()
        if spans and kind < 0.6:
            span = child_of(cls, rng.choice(spans), span_type=span_type)
        elif kind < 0.8:
            remote = Context(trace_id=3, span_id=rng.randint(1, 2**64 - 1))
            if rng.random() < 0.5:
                # A propagated local root of 0 reads as absent, so the reader falls back to the
                # Context's span id where _link_span would not. The tracer never propagates 0.
                context_meta.attach_profiler_link(
                    remote, local_root_span_id=rng.randint(1, 2**64 - 1), span_type=rng.choice(types)
                )
            remote._reactivate = rng.random() < 0.5
            values.append(remote)
            span = cls("distributed", span_type=span_type, context=remote, parent_id=remote.span_id)
        else:
            span = cls("root", span_type=span_type, span_id=rng.choice([None, None, None, 0]))
        spans.append(span)
    for span in spans:
        if rng.random() < 0.5:
            span.finish()
    return values + spans


def test_random_span_trees(native: Any, oracle: Any) -> None:
    rng = random.Random(0x5EED)
    before = native._context_reader_failures()
    for _ in range(300):
        for value in random_tree(rng):
            assert_matches(native, oracle, holding(value))
    assert native._context_reader_failures() == before
