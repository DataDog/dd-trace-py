"""Commit exposures only after the complete OpenFeature evaluation succeeds."""

from collections.abc import Callable
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import copy
from dataclasses import dataclass
from dataclasses import field
import typing

from openfeature.evaluation_context import EvaluationContext
from openfeature.flag_evaluation import FlagEvaluationDetails
from openfeature.flag_evaluation import Reason
from openfeature.hook import Hook
from openfeature.hook import HookContext
from openfeature.hook import HookHints

from ddtrace.internal.openfeature._flageval_metrics import FlagEvalMetrics
from ddtrace.internal.openfeature._flageval_metrics import FlagEvalMetricsHook
from ddtrace.internal.openfeature.writer import ExposureEvent


@dataclass
class _ExposureBatch:
    context: HookContext
    events: list[ExposureEvent] = field(default_factory=list)
    resolved: bool = False


class ExposureHook(Hook):
    def __init__(self, publish: Callable[[ExposureEvent], None]) -> None:
        self._publish = publish
        self._pending: ContextVar[tuple[_ExposureBatch, ...]] = ContextVar("ffe_pending_exposures", default=())
        self._resolving: ContextVar[typing.Optional[_ExposureBatch]] = ContextVar(
            "ffe_resolving_exposures", default=None
        )

    def before(self, hook_context: HookContext, hints: HookHints) -> typing.Optional[EvaluationContext]:
        self._pending.set((*self._pending.get(), _ExposureBatch(hook_context)))
        return None

    @contextmanager
    def resolution(self, flag_key: str) -> Iterator[None]:
        pending = self._pending.get()
        batch = pending[-1] if pending else None
        if batch is not None and (batch.resolved or batch.context.flag_key != flag_key):
            batch = None
        if batch is not None:
            batch.resolved = True
        # Direct provider calls have no OpenFeature lifecycle. Mask any outer
        # resolution so a nested direct call cannot join its exposure batch.
        token = self._resolving.set(batch)
        try:
            yield
        finally:
            self._resolving.reset(token)

    def stage(self, event: ExposureEvent) -> None:
        # The event dictionaries are fresh; only attributes refer to application
        # data that hooks or callers can mutate after resolution.
        event["subject"]["attributes"] = copy.deepcopy(event["subject"]["attributes"])
        batch = self._resolving.get()
        if batch is None:
            self._publish(event)
        else:
            batch.events.append(event)

    def finally_after(
        self,
        hook_context: HookContext,
        details: FlagEvaluationDetails[typing.Any],
        hints: HookHints,
    ) -> None:
        pending = self._pending.get()
        batch = next((item for item in reversed(pending) if item.context is hook_context), None)
        if batch is None:
            return
        self._pending.set(tuple(item for item in pending if item is not batch))
        if details.error_code is not None or details.reason == Reason.ERROR:
            return
        for event in batch.events:
            self._publish(event)


class ExposureMetricsHook(FlagEvalMetricsHook):
    """Share the existing metrics hook dispatch with exposure finalization."""

    def __init__(self, metrics: FlagEvalMetrics, exposures: ExposureHook) -> None:
        super().__init__(metrics)
        self._exposures = exposures

    def before(self, hook_context: HookContext, hints: HookHints) -> typing.Optional[EvaluationContext]:
        return self._exposures.before(hook_context, hints)

    def finally_after(
        self,
        hook_context: HookContext,
        details: FlagEvaluationDetails[typing.Any],
        hints: HookHints,
    ) -> None:
        try:
            super().finally_after(hook_context, details, hints)
        finally:
            self._exposures.finally_after(hook_context, details, hints)
