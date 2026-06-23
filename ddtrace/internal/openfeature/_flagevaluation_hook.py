"""
FlagEvaluationHook — OpenFeature `finally_after` hook for EVP flagevaluation emission.

Hook design:
- Cheap capture only in finally_after (no aggregation, no serialization, no I/O).
- Non-blocking enqueue to FlagEvaluationWriter.
- The finally_after stage covers success, error, and default eval paths.
- Does NOT replace or modify the OTel FlagEvalHook in _flageval_metrics.py (the existing
  feature_flag.evaluations OTel path is preserved unchanged).
"""

import time
import typing

from openfeature.exception import ErrorCode
from openfeature.flag_evaluation import FlagEvaluationDetails
from openfeature.hook import Hook
from openfeature.hook import HookContext
from openfeature.hook import HookHints

from ddtrace.internal.logger import get_logger
from ddtrace.internal.openfeature._flagevaluation_writer import EVAL_TIMESTAMP_METADATA_KEY
from ddtrace.internal.openfeature._flagevaluation_writer import METADATA_ALLOCATION_KEY
from ddtrace.internal.openfeature._flagevaluation_writer import FlagEvaluationWriter
from ddtrace.internal.openfeature._flagevaluation_writer import _EvalEvent


logger = get_logger(__name__)


class FlagEvaluationHook(Hook):
    """
    OpenFeature Hook that enqueues cheap evaluation snapshots for EVP aggregation.

    Implements `finally_after` (covers the success, error, and default eval paths).
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

        Runtime-default: True when the variant is absent (details.variant is None).

        Attrs: shallow copy of the evaluation context attributes dict so the hook
        returns immediately and the worker can safely iterate attrs off-path.
        """
        try:
            flag_key: str = hook_context.flag_key or ""

            # Extract allocation_key from flag_metadata (same key as METADATA_ALLOCATION_KEY).
            metadata: typing.Mapping[str, typing.Any] = details.flag_metadata or {}
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

            # Variant: absent or type mismatch signals a runtime default.
            variant = ""
            if details.variant and details.error_code != ErrorCode.TYPE_MISMATCH:
                variant = details.variant
            runtime_default = variant == ""

            # Targeting key and attributes from the evaluation context.
            eval_ctx = hook_context.evaluation_context
            targeting_key = eval_ctx.targeting_key or ""
            # Shallow copy so we don't hold a reference to the caller's live dict.
            attrs: dict[str, typing.Any] = dict(eval_ctx.attributes or {})

            # Error message (best-effort; absent on success paths).
            error_message = ""
            if details.error_message:
                error_message = str(details.error_message)
            elif details.error_code:
                error_message = (
                    str(details.error_code.value) if hasattr(details.error_code, "value") else str(details.error_code)
                )

            event = _EvalEvent(
                flag_key=flag_key,
                variant=variant,
                allocation_key=allocation_key,
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
