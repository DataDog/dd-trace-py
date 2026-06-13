"""Hot-path microbenchmark for the EVP ``flagevaluation`` aggregation pipeline.

This measures the cost an OpenFeature evaluation actually pays for server-side EVP
flag-evaluation counting, mirroring the Go reference benchmark
(``dd-trace-go/openfeature/flagevaluation_test.go``):

* ``hook_enqueue`` — the ``finally_after`` hook hot path: cheap scalar capture + a
  shallow context copy + a non-blocking bounded enqueue. This is what charges the
  caller's evaluation goroutine/thread directly, so it must stay flat.
* ``aggregate`` — the background worker hot path: flatten + deterministic prune +
  canonical-context key + two-tier aggregation. This runs OFF the eval path, but its
  per-event cost still bounds throughput of the writer thread.
* ``hook_plus_drain`` — end-to-end: N hook enqueues followed by a full queue drain +
  aggregate, the realistic per-flush shape.

The scenario builds its own ``FlagEvaluationWriter`` + ``FlagEvaluationHook`` and drives
them directly so the benchmark isolates the EVP pipeline (no network, no native eval).
"""

import bm
from openfeature.evaluation_context import EvaluationContext
from openfeature.flag_evaluation import FlagEvaluationDetails
from openfeature.flag_evaluation import FlagType
from openfeature.flag_evaluation import Reason
from openfeature.hook import HookContext


def _make_hook_context(flag_key, targeting_key, attrs):
    ctx = EvaluationContext(targeting_key=targeting_key, attributes=attrs)
    return HookContext(
        flag_key=flag_key,
        flag_type=FlagType.BOOLEAN,
        default_value=False,
        evaluation_context=ctx,
    )


def _make_details(flag_key, variant, allocation_key):
    return FlagEvaluationDetails(
        flag_key=flag_key,
        value=True,
        variant=variant,
        reason=Reason.TARGETING_MATCH,
        flag_metadata={"allocation_key": allocation_key},
    )


class OpenFeatureFlagEvaluation(bm.Scenario):
    # Which segment of the pipeline to exercise.
    mode: str
    # Number of distinct flags cycled through (drives aggregation-map cardinality).
    num_flags: int
    # Number of evaluation-context attributes per evaluation (drives prune/key cost).
    num_context_fields: int

    def run(self):
        from ddtrace.internal.openfeature._flagevaluation_hook import FlagEvaluationHook
        from ddtrace.internal.openfeature._flagevaluation_writer import FlagEvaluationWriter

        mode = self.mode
        num_flags = max(1, self.num_flags)
        num_fields = max(0, self.num_context_fields)

        # Pre-build the per-evaluation inputs so the measured loop only exercises the
        # hook/aggregation hot path, not fixture construction.
        attrs = {"attr_{}".format(i): "value_{}".format(i) for i in range(num_fields)}
        hook_contexts = [
            _make_hook_context(
                flag_key="flag-{}".format(i % num_flags),
                targeting_key="user-{}".format(i % num_flags),
                attrs=attrs,
            )
            for i in range(num_flags)
        ]
        details_list = [
            _make_details(
                flag_key="flag-{}".format(i % num_flags),
                variant="variant-{}".format(i % 4),
                allocation_key="alloc-{}".format(i % num_flags),
            )
            for i in range(num_flags)
        ]

        writer = FlagEvaluationWriter(interval=3600.0)
        hook = FlagEvaluationHook(writer)

        if mode == "hook_enqueue":

            def _(loops):
                n = num_flags
                for i in range(loops):
                    idx = i % n
                    hook.finally_after(hook_contexts[idx], details_list[idx], {})
                    # Keep the queue from saturating so we keep measuring the enqueue
                    # path rather than the drop-and-count path.
                    if writer._queue.full():
                        writer._drain_queue()

            yield _

        elif mode == "aggregate":
            # Pre-capture snapshots once; the measured loop only runs _aggregate
            # (flatten + prune + canonical key + two-tier insert).
            from ddtrace.internal.openfeature._flagevaluation_writer import _EvalEvent

            events = [
                _EvalEvent(
                    flag_key="flag-{}".format(i % num_flags),
                    variant="variant-{}".format(i % 4),
                    allocation_key="alloc-{}".format(i % num_flags),
                    reason="TARGETING_MATCH",
                    targeting_key="user-{}".format(i % num_flags),
                    attrs=dict(attrs),
                    runtime_default=False,
                    error_message="",
                    eval_time_ms=1_700_000_000_000 + i,
                )
                for i in range(num_flags)
            ]

            def _(loops):
                n = num_flags
                for i in range(loops):
                    writer._aggregate(events[i % n])
                    # Reset periodically so aggregation maps don't grow unbounded across
                    # a long benchmark loop (keeps the per-op cost representative).
                    if (i % 10000) == 0:
                        writer._full.clear()
                        writer._degraded.clear()
                        writer._per_flag_count.clear()
                        writer._global_count = 0

            yield _

        elif mode == "hook_plus_drain":

            def _(loops):
                n = num_flags
                for i in range(loops):
                    idx = i % n
                    hook.finally_after(hook_contexts[idx], details_list[idx], {})
                    if writer._queue.full() or (i % n) == (n - 1):
                        writer._drain_queue()
                        writer._full.clear()
                        writer._degraded.clear()
                        writer._per_flag_count.clear()
                        writer._global_count = 0

            yield _

        else:
            raise ValueError("unknown mode: {}".format(mode))
