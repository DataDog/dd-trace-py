"""Thread-local current consumed-message context — one mechanism for classic DSM and the pipelines reporter.

This is the single place that tracks "the message this thread is currently
processing". Classic DSM stores the current
:class:`~ddtrace.internal.datastreams.processor.DataStreamsCtx` here
(``decode_pathway``/``new_pathway`` write it, ``set_checkpoint`` reads it as
the pathway parent); the pipelines reporter (DSM2-531) stores the current
consume side here (the consume hooks write it, ``on_produce`` reads it as the
link parent). One slot serves the DSM-on and the pipelines-on cases, so the
two features' in-process context rules cannot diverge.

The rule is classic DSM's, exactly: the current message is **replaced by the
next consume on the thread** and is never cleared otherwise. There is no
scope-exit clear path.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING
from typing import Optional


if TYPE_CHECKING:
    from ddtrace.internal.datastreams.pipelines import SideKey
    from ddtrace.internal.datastreams.processor import DataStreamsCtx


class CurrentMessageContext:
    """The consumed message a thread is currently processing.

    ``dsm_ctx`` is the classic DSM pathway context (set on every consume by
    ``decode_pathway``/``new_pathway``, and advanced in place by
    ``DataStreamsCtx.set_checkpoint`` on every produce). ``pipelines_side`` is
    the pipelines reporter's current consume side key (set by the consume
    hooks, read at produce time). Both fields follow the same
    replace-on-next-consume rule.
    """

    __slots__ = ("dsm_ctx", "pipelines_side")

    def __init__(self) -> None:
        self.dsm_ctx: Optional[DataStreamsCtx] = None
        self.pipelines_side: Optional[SideKey] = None


_local = threading.local()


def get_current_message() -> Optional[CurrentMessageContext]:
    """The thread's current consumed-message context, or ``None``."""
    return getattr(_local, "value", None)


def _ensure_current_message() -> CurrentMessageContext:
    message = getattr(_local, "value", None)
    if message is None:
        message = CurrentMessageContext()
        _local.value = message
    return message


def set_dsm_pathway(ctx: DataStreamsCtx) -> None:
    """Set the current DSM pathway context (processor.decode_pathway / new_pathway)."""
    _ensure_current_message().dsm_ctx = ctx


def set_pipelines_side(side: SideKey) -> None:
    """Set the current pipelines consume side (PipelinesReporter.set_current_consume)."""
    _ensure_current_message().pipelines_side = side


def reset_pipelines_side() -> None:
    """Unconditionally clear the pipelines side (forked child, fresh window)."""
    message = get_current_message()
    if message is not None:
        message.pipelines_side = None


def clear_current_message() -> None:
    """Drop the thread's current-message context entirely.

    Called by ``DataStreamsProcessor.__init__`` so a fresh processor starts
    with a fresh context, exactly as the processor's own thread-local did
    before this slot was shared.
    """
    _local.value = None


def current_dsm_pathway() -> Optional[DataStreamsCtx]:
    """The current DSM pathway context, or ``None`` (processor.set_checkpoint reads this as its parent)."""
    message = get_current_message()
    return message.dsm_ctx if message is not None else None


def current_pipelines_side() -> Optional[SideKey]:
    """The current pipelines consume side, or ``None`` (PipelinesReporter.on_produce links to this)."""
    message = get_current_message()
    return message.pipelines_side if message is not None else None
