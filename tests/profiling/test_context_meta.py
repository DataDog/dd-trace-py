import contextvars
import pickle
from typing import Any
from typing import Callable
from typing import Optional

import pytest

from ddtrace._trace.context import Context
from ddtrace._trace.provider import _DD_CONTEXTVAR
from ddtrace._trace.provider import DefaultContextProvider
from ddtrace.internal.ci_visibility.context import CIContextProvider
from ddtrace.internal.datadog.profiling import context_meta
from ddtrace.trace import Tracer


def test_attach_profiler_link_copies_meta() -> None:
    parent_meta = {"existing": "tag"}
    ctx = Context(trace_id=1, span_id=2, meta=parent_meta)
    context_meta.attach_profiler_link(ctx, local_root_span_id=99, span_type="web")
    assert ctx._meta is not parent_meta
    assert parent_meta == {"existing": "tag"}
    assert ctx._meta[context_meta.PROFILING_LOCAL_ROOT_SPAN_ID_KEY] == "0000000000000063"
    assert ctx._meta[context_meta.PROFILING_SPAN_TYPE_KEY] == "web"


def test_read_profiler_link_fallback_to_span_id() -> None:
    ctx = Context(trace_id=1, span_id=42)
    local_root, span_type = context_meta.read_profiler_link(ctx)
    assert local_root == 42
    assert span_type is None


def test_read_profiler_link_from_meta() -> None:
    ctx = Context(trace_id=1, span_id=2)
    context_meta.attach_profiler_link(ctx, local_root_span_id=99, span_type="sql")
    local_root, span_type = context_meta.read_profiler_link(ctx)
    assert local_root == 99
    assert span_type == "sql"


def test_profiler_meta_round_trips_in_pickle() -> None:
    ctx = Context(trace_id=123, span_id=321)
    context_meta.attach_profiler_link(ctx, local_root_span_id=789, span_type="web")
    restored = pickle.loads(pickle.dumps(ctx))
    local_root, span_type = context_meta.read_profiler_link(restored)
    assert local_root == 789
    assert span_type == "web"


def _states(tracer: Tracer) -> dict[str, context_meta.ActiveItem]:
    """Active items in every state resolve() distinguishes, keyed by a description."""
    reactivatable = Context(trace_id=1, span_id=2)
    reactivatable._reactivate = True
    not_reactivatable = Context(trace_id=3, span_id=4)

    unfinished = tracer.start_span("unfinished", activate=False)

    parent = tracer.start_span("parent", activate=False)
    child = tracer.start_span("child", child_of=parent, activate=False)
    child.finish()

    grandparent = tracer.start_span("grandparent", activate=False)
    finished_parent = tracer.start_span("finished parent", child_of=grandparent, activate=False)
    child_of_finished = tracer.start_span("child of finished", child_of=finished_parent, activate=False)
    child_of_finished.finish()
    finished_parent.finish()

    finished_root = tracer.start_span("finished root", activate=False)
    finished_root.finish()

    continued = tracer.start_span("continued", child_of=reactivatable, activate=False)
    continued_child = tracer.start_span("continued child", child_of=continued, activate=False)
    continued_child.finish()
    continued.finish()
    not_continued = tracer.start_span("not continued", child_of=not_reactivatable, activate=False)
    not_continued.finish()

    return {
        "no item": None,
        "context": not_reactivatable,
        "unfinished span": unfinished,
        "finished span, unfinished parent": child,
        "finished span and parent, unfinished grandparent": child_of_finished,
        "finished root span": finished_root,
        "finished span, reactivatable parent context": continued,
        "finished spans up to a reactivatable parent context": continued_child,
        "finished span, parent context not reactivatable": not_continued,
    }


_STATE_NAMES = [
    "no item",
    "context",
    "unfinished span",
    "finished span, unfinished parent",
    "finished span and parent, unfinished grandparent",
    "finished root span",
    "finished span, reactivatable parent context",
    "finished spans up to a reactivatable parent context",
    "finished span, parent context not reactivatable",
]


@pytest.mark.parametrize("state", _STATE_NAMES)
def test_resolve_agrees_with_default_context_provider_active(tracer: Tracer, state: str) -> None:
    states = _states(tracer)
    assert list(states) == _STATE_NAMES
    item: Any = states[state]

    def compare() -> None:
        provider = DefaultContextProvider()
        provider.activate(item)
        resolved = context_meta.resolve(item)
        # resolve() activates nothing.
        assert _DD_CONTEXTVAR.get() is item
        assert provider.active() is resolved

    # active() activates what it returns, so compare in a copy of the current context.
    contextvars.copy_context().run(compare)


def test_resolve_states_cover_every_outcome(tracer: Tracer) -> None:
    states = _states(tracer)
    resolved: dict[str, Any] = {name: context_meta.resolve(item) for name, item in states.items()}
    assert resolved["no item"] is None
    assert resolved["context"] is states["context"]
    assert resolved["unfinished span"] is states["unfinished span"]
    assert resolved["finished span, unfinished parent"].name == "parent"
    assert resolved["finished span and parent, unfinished grandparent"].name == "grandparent"
    assert resolved["finished root span"] is None
    assert isinstance(resolved["finished span, reactivatable parent context"], Context)
    assert (
        resolved["finished spans up to a reactivatable parent context"]
        is resolved["finished span, reactivatable parent context"]
    )
    assert resolved["finished span, parent context not reactivatable"] is None


def test_span_info_of_a_span_uses_its_local_root(tracer: Tracer) -> None:
    with tracer.trace("root", span_type="web") as root:
        with tracer.trace("child") as child:
            assert context_meta.span_info(child) == (
                child.span_id,
                root.span_id,
                "web",
                context_meta.SPAN_LINK_KIND_SPAN,
            )


def test_span_info_of_a_span_continued_from_a_context_reads_its_profiler_link(tracer: Tracer) -> None:
    ctx = Context(trace_id=1, span_id=2)
    context_meta.attach_profiler_link(ctx, local_root_span_id=99, span_type="sql")
    span = tracer.start_span("worker", child_of=ctx, activate=False)
    assert context_meta.span_info(span) == (span.span_id, 99, "sql", context_meta.SPAN_LINK_KIND_SPAN)


def test_span_info_of_a_context() -> None:
    ctx = Context(trace_id=1, span_id=2)
    context_meta.attach_profiler_link(ctx, local_root_span_id=99, span_type="web")
    assert context_meta.span_info(ctx) == (2, 99, "web", context_meta.SPAN_LINK_KIND_CONTEXT)
    assert context_meta.span_info(Context(trace_id=1, span_id=3)) == (3, 3, None, context_meta.SPAN_LINK_KIND_CONTEXT)


@pytest.mark.parametrize("item", [None, Context(trace_id=1, span_id=0), Context(trace_id=1)])
def test_span_info_of_no_span(item: Optional[Context]) -> None:
    # Span id 0, as for an invalid OpenTelemetry span context, means no span.
    assert context_meta.span_info(item) is None


class _Subclass(DefaultContextProvider):
    pass


class _OwnActive(DefaultContextProvider):
    def active(self) -> Any:
        return DefaultContextProvider.active(self)


class _OwnUpdateActive(DefaultContextProvider):
    def _update_active(self, span: Any) -> Any:
        return DefaultContextProvider._update_active(self, span)


def _with_instance_active() -> DefaultContextProvider:
    provider = _Subclass()
    provider.active = lambda: None  # type: ignore[method-assign]
    return provider


@pytest.mark.parametrize(
    "make_provider, expected",
    [
        (DefaultContextProvider, True),
        (_Subclass, True),
        (_OwnActive, False),
        (_OwnUpdateActive, False),
        (_with_instance_active, False),
        (CIContextProvider, False),
        (object, False),
    ],
)
def test_inheritance_mode(make_provider: Callable[[], object], expected: bool) -> None:
    assert context_meta.inheritance_mode(make_provider()) is expected


def test_inheritance_mode_is_recomputed_when_the_provider_changes() -> None:
    default = DefaultContextProvider()
    custom = _OwnActive()
    assert context_meta.inheritance_mode(default) is True
    assert context_meta.inheritance_mode(custom) is False
    assert context_meta.inheritance_mode(default) is True
