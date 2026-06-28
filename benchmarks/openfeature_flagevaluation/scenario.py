"""Hot-path microbenchmark for the EVP ``flagevaluation`` aggregation pipeline.

The profiles mirror the Go/Ruby OpenFeature EVP benchmark suite:

* typical/100flags_50users_10fields
* stress/10flags_1000users_250fields
* scale/2500flags_500users_20fields

The scenario drives ``FlagEvalEVPHook`` and ``FlagEvaluationWriter`` directly so the
benchmark isolates in-process EVP work: no network, no Remote Config backend, and no
native flag evaluation cost.
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
    # Number of distinct flags cycled through.
    num_flags: int
    # Number of distinct targeting keys cycled through.
    num_users: int
    # Number of evaluation-context attributes per evaluation.
    num_context_fields: int

    def run(self):
        from ddtrace.internal.openfeature._flag_eval_evp_hook import FlagEvalEVPHook
        from ddtrace.internal.openfeature._flagevaluation_writer import FlagEvaluationWriter

        mode = self.mode
        num_flags = max(1, self.num_flags)
        num_users = max(1, self.num_users)
        num_fields = max(0, self.num_context_fields)
        cycle_count = max(num_flags, num_users)

        attrs = {"attr_{}".format(i): "value_{}".format(i) for i in range(num_fields)}
        flag_keys = ["flag-{}".format(i) for i in range(num_flags)]
        targeting_keys = ["user-{}".format(i) for i in range(num_users)]
        hook_contexts = [
            _make_hook_context(
                flag_key=flag_keys[i % num_flags],
                targeting_key=targeting_keys[i % num_users],
                attrs=attrs,
            )
            for i in range(cycle_count)
        ]
        details_list = [
            _make_details(
                flag_key=flag_keys[i % num_flags],
                variant="variant-{}".format(i % 4),
                allocation_key="alloc-{}".format(i % num_flags),
            )
            for i in range(cycle_count)
        ]

        writer = FlagEvaluationWriter(interval=3600.0)
        hook = FlagEvalEVPHook(writer)

        if mode == "hook_enqueue":

            def _(loops):
                for i in range(loops):
                    idx = i % cycle_count
                    hook.finally_after(hook_contexts[idx], details_list[idx], {})
                    # Keep the queue from saturating so this measures steady-state
                    # enqueue, not queue overflow accounting.
                    if writer._queue.full():
                        writer._drain_queue()

            yield _

        elif mode == "aggregate":
            from ddtrace.internal.openfeature._flagevaluation_writer import _EvalEvent
            from ddtrace.internal.openfeature._flagevaluation_writer import flatten_and_prune_context

            bounded_attrs = flatten_and_prune_context(attrs)
            events = [
                _EvalEvent(
                    flag_key=flag_keys[i % num_flags],
                    variant="variant-{}".format(i % 4),
                    allocation_key="alloc-{}".format(i % num_flags),
                    targeting_key=targeting_keys[i % num_users],
                    attrs=dict(bounded_attrs),
                    runtime_default=False,
                    error_message="",
                    eval_time_ms=1_760_000_000_000 + i,
                )
                for i in range(cycle_count)
            ]

            def _(loops):
                for i in range(loops):
                    writer._aggregate(events[i % cycle_count])
                    # Reset periodically so the measured loop is not dominated by
                    # ever-growing maps after enough unique buckets have been seen.
                    if i > 0 and (i % 10000) == 0:
                        writer._full.clear()
                        writer._degraded.clear()
                        writer._per_flag_count.clear()
                        writer._global_count = 0

            yield _

        elif mode == "hook_plus_drain":

            def _(loops):
                for i in range(loops):
                    idx = i % cycle_count
                    hook.finally_after(hook_contexts[idx], details_list[idx], {})
                    if writer._queue.full() or (i % cycle_count) == (cycle_count - 1):
                        writer._drain_queue()
                        writer._full.clear()
                        writer._degraded.clear()
                        writer._per_flag_count.clear()
                        writer._global_count = 0

            yield _

        else:
            raise ValueError("unknown mode: {}".format(mode))
