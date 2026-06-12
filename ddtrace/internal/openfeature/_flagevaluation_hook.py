"""
FlagEvaluationHook — OpenFeature `finally_after` hook for EVP flagevaluation emission.

Implements the frozen FANOUT-CONTRACT hook design:
- Cheap capture only in finally_after (no aggregation, no serialization, no I/O).
- Non-blocking enqueue to FlagEvaluationWriter.
- Covers success, error, and default eval paths (reviewer concern #7 3385309423).
- Does NOT replace or modify the OTel FlagEvalHook in _flageval_metrics.py (PRES-01).
"""

import time
import typing

from openfeature.flag_evaluation import FlagEvaluationDetails
from openfeature.hook import Hook
from openfeature.hook import HookContext
from openfeature.hook import HookHints

from ddtrace.internal.logger import get_logger
from ddtrace.internal.openfeature._flagevaluation_writer import (
    EVAL_TIMESTAMP_METADATA_KEY,
    METADATA_ALLOCATION_KEY,
    FlagEvaluationWriter,
    _EvalEvent,
)


logger = get_logger(__name__)


class FlagEvaluationHook(Hook):
    """
    OpenFeature Hook that enqueues cheap evaluation snapshots for EVP aggregation.

    Implements `finally_after` (covers success/error/default — reviewer concern #7).
    Does NO aggregation, serialization, or I/O on the hook thread.  All heavy work
    is deferred to FlagEvaluationWriter's background periodic worker.
    """

    def __init__(self, writer: FlagEvaluationWriter) -> None:
        self._writer = writer

    def finally_after(
        self,
        hook_context: HookContext,
        details: FlagEvaluationDetails[typing.Any],
        hints: HookHints,
    ) -> None:
        """
        Cheap capture + non-blocking enqueue.

        Extracts only scalar fields (no allocation, no serialization) and hands
        a _EvalEvent snapshot to FlagEvaluationWriter.enqueue() which is
        non-blocking (queue.Queue.put_nowait — drops on queue.Full).

        Eval-time: uses details.flag_metadata["dd.eval.timestamp_ms"] when present
        (stamped by the provider at eval entry); falls back to hook-fire time.

        Runtime-default: True when details.value is None (absent variant — reviewer
        concern #5 3395344504).

        Attrs: shallow copy of the evaluation context attributes dict so the hook
        returns immediately and the worker can safely iterate attrs off-path.
        """
        try:
            flag_key: str = hook_context.flag_key or ""

            # Extract allocation_key from flag_metadata (same key as METADATA_ALLOCATION_KEY).
            metadata: dict = details.flag_metadata or {}
            allocation_key: str = ""
            ak = metadata.get(METADATA_ALLOCATION_KEY)
            if isinstance(ak, str) and ak:
                allocation_key = ak

            # Eval-time from provider-stamped metadata; fall back to hook-fire time.
            eval_time_ms_raw = metadata.get(EVAL_TIMESTAMP_METADATA_KEY)
            if isinstance(eval_time_ms_raw, (int, float)) and eval_time_ms_raw > 0:
                eval_time_ms = int(eval_time_ms_raw)
            else:
                eval_time_ms = int(time.time() * 1000)

            # Variant: None/absent signals runtime_default (reviewer concern #5).
            variant = details.variant or ""
            runtime_default = details.variant is None

            # Reason: normalise to upper-case string.
            if details.reason is not None:
                reason_raw = (
                    details.reason.value
                    if hasattr(details.reason, "value")
                    else str(details.reason)
                )
                reason = str(reason_raw).upper()
            else:
                reason = "UNKNOWN"

            # Targeting key and attributes from the evaluation context.
            eval_ctx = hook_context.evaluation_context
            if eval_ctx is not None:
                targeting_key = eval_ctx.targeting_key or ""
                # Shallow copy so we don't hold a reference to the caller's live dict.
                attrs: typing.Dict[str, typing.Any] = dict(eval_ctx.attributes or {})
            else:
                targeting_key = ""
                attrs = {}

            # Error message (best-effort; absent on success paths).
            error_message = ""
            if details.error_message:
                error_message = str(details.error_message)

            event = _EvalEvent(
                flag_key=flag_key,
                variant=variant,
                allocation_key=allocation_key,
                reason=reason,
                targeting_key=targeting_key,
                attrs=attrs,
                runtime_default=runtime_default,
                error_message=error_message,
                eval_time_ms=eval_time_ms,
            )

            self._writer.enqueue(event)

        except Exception:
            # Never propagate hook exceptions — best-effort telemetry.
            logger.debug(
                "FlagEvaluationHook.finally_after: failed to enqueue eval snapshot",
                exc_info=True,
            )
