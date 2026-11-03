"""Hot-path microbenchmark for the EVP ``flagevaluation`` aggregation pipeline.

The profiles mirror the Go/Ruby OpenFeature EVP benchmark suite:

* typical/100flags_50users_10fields
* stress/10flags_1000users_250fields
* scale/2500flags_500users_20fields

The scenario drives ``FlagEvalEVPHook`` and ``FlagEvaluationWriter`` directly so the
benchmark isolates in-process EVP work: no network, no Remote Config backend, and no
native flag evaluation cost.
"""

import queue

import bm
from openfeature.evaluation_context import EvaluationContext
from openfeature.flag_evaluation import FlagEvaluationDetails
from openfeature.flag_evaluation import FlagType
from openfeature.flag_evaluation import Reason
from openfeature.hook import HookContext


class _DiscardingQueue(queue.Queue):
    """Exercise Queue.put_nowait without retaining benchmark events."""

    def _put(self, item):
        self.queue.append(item)
        self.queue.popleft()


def _make_hook_context(flag_key, targeting_key, attrs):
    ctx = EvaluationContext(targeting_key=targeting_key, attributes=attrs)
    return HookContext(
        flag_key=flag_key,
        flag_type=FlagType.BOOLEAN,
        default_value=False,
        evaluation_context=ctx,
    )


def _make_details(flag_key, variant, allocation_key, observe_full_evaluation_data):
    return FlagEvaluationDetails(
        flag_key=flag_key,
        value=True,
        variant=variant,
        reason=Reason.TARGETING_MATCH,
        flag_metadata={
            "allocation_key": allocation_key,
            "__dd_observe_full_evaluation_data": observe_full_evaluation_data,
        },
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
    # Whether the evaluated configuration permits full evaluation data.
    observe_full_evaluation_data: bool
    # provider_resolution only: whether every split carries features.
    split_features: bool = False

    def run(self):
        from ddtrace.internal.openfeature._flag_eval_evp_hook import FlagEvalEVPHook
        from ddtrace.internal.openfeature._flagevaluation_writer import FlagEvaluationWriter

        mode = self.mode
        if mode == "provider_resolution":
            yield from self._provider_resolution()
            return
        num_flags = max(1, self.num_flags)
        num_users = max(1, self.num_users)
        num_fields = max(0, self.num_context_fields)
        observe_full_evaluation_data = self.observe_full_evaluation_data
        cycle_count = max(num_flags, num_users)

        if mode == "hook_enqueue_adversarial":
            attrs = {"discarded": ["x" * 257 for _ in range(num_fields)]}
        else:
            attrs = {f"attr_{i}": f"value_{i}" for i in range(num_fields)}
        flag_keys = [f"flag-{i}" for i in range(num_flags)]
        targeting_keys = [f"user-{i}" for i in range(num_users)]
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
                variant=f"variant-{i % 4}",
                allocation_key=f"alloc-{i % num_flags}",
                observe_full_evaluation_data=observe_full_evaluation_data,
            )
            for i in range(cycle_count)
        ]

        writer = FlagEvaluationWriter(interval=3600.0)
        hook = FlagEvalEVPHook(writer)

        if mode in ("hook_enqueue", "hook_enqueue_adversarial"):
            # Preserve Queue.put_nowait locking and notification overhead without
            # periodically draining and aggregating events in this isolated mode.
            writer._queue = _DiscardingQueue(maxsize=writer._queue.maxsize)

            def _(loops):
                for i in range(loops):
                    idx = i % cycle_count
                    hook.finally_after(hook_contexts[idx], details_list[idx], {})

            yield _

        elif mode == "aggregate":
            from ddtrace.internal.openfeature._flagevaluation_writer import _EvalEvent
            from ddtrace.internal.openfeature._flagevaluation_writer import flatten_and_prune_context

            bounded_result = flatten_and_prune_context(attrs)
            if isinstance(bounded_result, tuple):
                bounded_attrs, truncation_reasons = bounded_result
                assert not truncation_reasons
            else:
                # Released versions return only the bounded mapping.
                bounded_attrs = bounded_result
            events = [
                _EvalEvent(
                    flag_key=flag_keys[i % num_flags],
                    variant=f"variant-{i % 4}",
                    allocation_key=f"alloc-{i % num_flags}",
                    targeting_key=targeting_keys[i % num_users],
                    attrs=bounded_attrs,
                    runtime_default=False,
                    error_message="",
                    eval_time_ms=1_760_000_000_000 + i,
                )
                for i in range(cycle_count)
            ]
            # The released baseline predates evaluation-time consent and represents
            # the legacy full-data behavior. Set the field only when the candidate
            # event type exposes it, so both distributions run the same scenario.
            if observe_full_evaluation_data and "observe_full_evaluation_data" in _EvalEvent._fields:
                events = [event._replace(observe_full_evaluation_data=True) for event in events]

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
            # One complete scale cycle must fit without using queue state to decide
            # when the benchmark drains. enqueue() already checks backpressure.
            assert cycle_count <= writer._queue.maxsize

            def _drain_and_reset() -> None:
                writer._drain_queue()
                writer._full.clear()
                writer._degraded.clear()
                writer._per_flag_count.clear()
                writer._global_count = 0

            def _(loops):
                for i in range(loops):
                    idx = i % cycle_count
                    hook.finally_after(hook_contexts[idx], details_list[idx], {})
                    if (i + 1) % cycle_count == 0:
                        _drain_and_reset()

                # pyperf can calibrate a loop count that is not a whole scale cycle.
                # Drain the remainder so every measured enqueue is also aggregated.
                if loops % cycle_count:
                    _drain_and_reset()

            yield _

        else:
            raise ValueError(f"unknown mode: {mode}")

    def _provider_resolution(self):
        """Native resolution plus the provider's own hooks, as one client evaluation runs them."""
        from openfeature.flag_evaluation import FlagType

        from ddtrace.internal.openfeature import _provider
        from ddtrace.internal.openfeature._native import process_ffe_configuration

        num_flags = max(1, self.num_flags)
        num_users = max(1, self.num_users)
        attrs = {f"attr_{i}": f"value_{i}" for i in range(max(0, self.num_context_fields))}
        flag_keys = [f"flag-{i}" for i in range(num_flags)]
        process_ffe_configuration(_ufc_configuration(flag_keys, self.split_features, self.observe_full_evaluation_data))

        # Nothing is sent: exposures go to a discarding writer and evaluation counts to a
        # discarding queue, so the loop measures evaluation work only.
        _provider.get_exposure_writer = lambda: _DISCARDING_EXPOSURE_WRITER
        provider = _provider.DataDogProvider()
        evp_writer = getattr(provider, "_flag_eval_evp_writer", None)
        if evp_writer is not None:
            evp_writer._queue = _DiscardingQueue(maxsize=evp_writer._queue.maxsize)
        hooks = list(reversed(provider.get_provider_hooks()))

        cycle_count = max(num_flags, num_users)
        evaluations = []
        for i in range(cycle_count):
            flag_key = flag_keys[i % num_flags]
            ctx = EvaluationContext(targeting_key=f"user-{i % num_users}", attributes=attrs)
            hook_context = HookContext(
                flag_key=flag_key, flag_type=FlagType.STRING, default_value="default", evaluation_context=ctx
            )
            evaluations.append((flag_key, ctx, hook_context))

        first = provider.resolve_string_details(flag_keys[0], "default", evaluations[0][1])
        assert first.value == "on" and first.error_code is None, first

        def _(loops):
            for i in range(loops):
                flag_key, ctx, hook_context = evaluations[i % cycle_count]
                details = provider.resolve_string_details(flag_key, "default", ctx).to_flag_evaluation_details(flag_key)
                for hook in hooks:
                    hook.finally_after(hook_context, details, {})

        yield _


class _DiscardingExposureWriter:
    def enqueue(self, event):
        pass


_DISCARDING_EXPOSURE_WRITER = _DiscardingExposureWriter()

# A few features of each value type across the three destinations, like a holdout split.
_SPLIT_FEATURES = [
    {"key": "holdout.key", "value": "06ad2f1e", "destinations": ["HOOK"]},
    {"key": "holdout.assignment_group", "value": "status_quo", "destinations": ["HOOK", "EXPOSURE"]},
    {"key": "holdout.should_include_in_holdout_analysis", "value": False, "destinations": ["HOOK"]},
    {"key": "holdout.fraction", "value": 0.5, "destinations": ["EVALUATION"]},
]


def _ufc_configuration(flag_keys, split_features, observe_full_evaluation_data):
    def split(serial_id):
        result = {"variationKey": "on", "shards": [], "serialId": serial_id}
        if split_features:
            result["features"] = _SPLIT_FEATURES
        return result

    return {
        "createdAt": "2026-10-01T00:00:00Z",
        "environment": {"name": "benchmark"},
        "observeFullEvaluationData": observe_full_evaluation_data,
        "flags": {
            key: {
                "key": key,
                "enabled": True,
                "variationType": "STRING",
                "variations": {"on": {"key": "on", "value": "on"}, "off": {"key": "off", "value": "off"}},
                "allocations": [{"key": f"alloc-{i}", "splits": [split(i)], "doLog": True}],
            }
            for i, key in enumerate(flag_keys)
        },
    }
