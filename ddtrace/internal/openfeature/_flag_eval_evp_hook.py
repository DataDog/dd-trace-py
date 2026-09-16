"""
FlagEvalEVPHook — OpenFeature `finally_after` hook for EVP flagevaluation emission.

Hook design:
- Protected mode does not inspect attributes; full-data mode takes one bounded
  context snapshot in finally_after (no aggregation or I/O).
- Non-blocking enqueue to FlagEvaluationWriter.
- The finally_after stage covers success, error, and default eval paths.
- Does NOT replace or modify the OTel FlagEvalMetricsHook in _flageval_metrics.py (the existing
  feature_flag.evaluations OTel path is preserved unchanged).
"""

import time
import typing

from openfeature.flag_evaluation import FlagEvaluationDetails
from openfeature.hook import Hook
from openfeature.hook import HookContext
from openfeature.hook import HookHints

from ddtrace.internal.logger import get_logger
from ddtrace.internal.openfeature._flagevaluation_writer import EVAL_TIMESTAMP_METADATA_KEY
from ddtrace.internal.openfeature._flagevaluation_writer import METADATA_ALLOCATION_KEY
from ddtrace.internal.openfeature._flagevaluation_writer import METADATA_OBSERVE_FULL_EVALUATION_DATA
from ddtrace.internal.openfeature._flagevaluation_writer import FlagEvaluationWriter
from ddtrace.internal.openfeature._flagevaluation_writer import _EvalEvent


logger = get_logger(__name__)


class FlagEvalEVPHook(Hook):
    """
    OpenFeature Hook that enqueues bounded evaluation snapshots for EVP aggregation.

    Implements `finally_after` (covers the success, error, and default eval paths).
    Full-data context bounding happens synchronously before queue insertion. Protected
    mode skips attribute capture. Aggregation and I/O remain deferred to
    FlagEvaluationWriter's background periodic worker.
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
        Bounded capture + non-blocking enqueue.

        Extracts scalar fields and hands them to FlagEvaluationWriter.enqueue(),
        which performs an O(1) queue-full precheck. Full-data mode then creates a
        bounded immutable context snapshot before queue.Queue.put_nowait; protected
        mode never dereferences the context attributes.

        Eval-time: uses details.flag_metadata["dd.eval.timestamp_ms"] when present
        (stamped by the provider at eval entry); falls back to hook-fire time.

        Runtime-default: True when the variant is absent (details.variant is None).

        Attrs: borrowed only for the synchronous enqueue call. The queue never retains
        this caller-owned mapping or any mutable context leaf.
        """
        try:
            flag_key: str = hook_context.flag_key or ""

            # Extract allocation_key from flag_metadata (same key as METADATA_ALLOCATION_KEY).
            metadata: typing.Mapping[str, typing.Any] = (
                details.flag_metadata if details.flag_metadata is not None else {}
            )
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

            # Variant: absent variant signals a runtime default.
            variant = ""
            if details.variant:
                variant = details.variant
            runtime_default = variant == ""

            # Consent comes only from evaluation metadata. Anything except an
            # exact boolean True fails closed to protected mode.
            observe_full_evaluation_data = metadata.get(METADATA_OBSERVE_FULL_EVALUATION_DATA) is True

            # Targeting key and attributes from the evaluation context.
            eval_ctx = hook_context.evaluation_context
            # Preserve None versus explicit empty string for wire semantics.
            targeting_key = eval_ctx.targeting_key
            # Protected mode must not even dereference the caller's attributes.
            attrs: typing.Mapping[str, typing.Any] = (
                eval_ctx.attributes if observe_full_evaluation_data and eval_ctx.attributes is not None else {}
            )

            error_code = ""
            if details.error_code:
                error_code = (
                    str(details.error_code.value) if hasattr(details.error_code, "value") else str(details.error_code)
                )

            # AIDEV-NOTE: Raw error messages can echo evaluation-context PII.
            # Protected mode carries only the stable low-cardinality code.
            if observe_full_evaluation_data and details.error_message:
                error_message = str(details.error_message)
            else:
                error_message = error_code

            event = _EvalEvent(
                flag_key=flag_key,
                variant=variant,
                allocation_key=allocation_key,
                targeting_key=targeting_key,
                attrs=attrs,
                runtime_default=runtime_default,
                error_message=error_message,
                eval_time_ms=eval_time_ms,
                observe_full_evaluation_data=observe_full_evaluation_data,
                error_code=error_code,
            )

            self._writer.enqueue(event)

        except Exception as exc:
            # Never propagate hook exceptions — best-effort telemetry.
            logger.debug(
                "FlagEvalEVPHook.finally_after: failed to enqueue eval snapshot (%s)",
                type(exc).__name__,
            )
