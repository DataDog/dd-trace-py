"""LP-owned value contracts for span activation and completion."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from typing import Optional

from ddtrace.internal import core
from ddtrace.internal.compat import ensure_text
from ddtrace.internal.core.events import Event
from ddtrace.internal.native._native import Context


# Keep the existing context metadata encoding compatible across propagation boundaries.
LOCAL_ROOT_SPAN_ID_KEY = "_dd.profiling.local_root_span_id"
SPAN_TYPE_KEY = "_dd.profiling.span_type"


def attach_span_link(ctx: Context, local_root_span_id: int, span_type: Optional[str]) -> None:
    ctx._meta = dict(ctx._meta)
    ctx._meta[LOCAL_ROOT_SPAN_ID_KEY] = f"{local_root_span_id:016x}"
    if span_type is not None:
        ctx._meta[SPAN_TYPE_KEY] = span_type


def read_span_link(ctx: Context) -> tuple[int, Optional[str]]:
    if ctx.span_id is None:
        raise ValueError("Context.span_id is required for span linkage")
    root = ctx._meta.get(LOCAL_ROOT_SPAN_ID_KEY)
    return (int(root, 16) if root else ctx.span_id), ctx._meta.get(SPAN_TYPE_KEY)


@dataclass(frozen=True)
class SpanIdentity:
    """Local ownership and propagated correlation identity are deliberately separate."""

    span_id: int
    local_root_span_id: Optional[int]
    local_root_span_type: Optional[str]
    correlation_root_span_id: int
    correlation_root_span_type: Optional[str]


def span_identity(active: Any) -> Optional[SpanIdentity]:
    """Copy identity without exposing a live span or context to the consumer."""
    if active is None or getattr(active, "span_id", None) is None:
        return None
    if isinstance(active, Context):
        context_root_id, context_root_type = read_span_link(active)
        if active.span_id is None:
            return None
        return SpanIdentity(active.span_id, None, None, context_root_id, context_root_type)
    root = getattr(active, "_local_root", None)
    root_id = getattr(root, "span_id", None)
    root_type = getattr(root, "span_type", None)
    correlation_id, correlation_type = root_id or active.span_id, root_type
    parent_context = getattr(active, "_parent_context", None)
    if getattr(active, "_parent", None) is None and getattr(active, "parent_id", None) is not None and parent_context:
        correlation_id, correlation_type = read_span_link(parent_context)
        correlation_type = correlation_type or root_type
    return SpanIdentity(active.span_id, root_id, root_type, correlation_id, correlation_type)


@dataclass(frozen=True)
class CompletedSpan:
    source_id: int
    span_id: int
    local_root_span_id: Optional[int]
    span_type: Optional[str]
    resource: str
    recording: bool

    @property
    def is_local_root(self) -> bool:
        return self.span_id == self.local_root_span_id


@dataclass
class SpanCompletedEvent(Event):
    """Published after span processors, before trace aggregation/export."""

    event_name = "span.lifecycle.completed"
    span: CompletedSpan


def publish_completed(source: object, span: Any, recording: bool) -> None:
    if not core.has_listeners(SpanCompletedEvent.event_name):
        return
    core.dispatch_event(
        SpanCompletedEvent(
            CompletedSpan(
                source_id=id(source),
                span_id=span.span_id,
                local_root_span_id=getattr(span._local_root, "span_id", None),
                span_type=span.span_type,
                resource=ensure_text(span.resource, errors="backslashreplace"),
                recording=recording,
            )
        )
    )


class SpanActivationSubscription:
    """Adapt provider activation into immutable identities; close unregisters it."""

    def __init__(
        self, provider_getter: Callable[[], object], callback: Callable[[Optional[SpanIdentity]], None]
    ) -> None:
        self._provider_getter = provider_getter
        self._callback = callback
        self._started = False

    def start(self) -> None:
        if not self._started:
            core.on("ddtrace.context_provider.activate", self._activate)
            self._started = True

    def close(self) -> None:
        if self._started:
            core.reset_listeners("ddtrace.context_provider.activate", self._activate)
            self._started = False

    def _activate(self, provider: object, active: Any) -> None:
        if self._started and provider is self._provider_getter():
            self._callback(span_identity(active))
