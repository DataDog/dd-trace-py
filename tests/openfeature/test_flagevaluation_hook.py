"""
Tests for FlagEvaluationHook — finally_after cheap capture + non-blocking enqueue.

Validates the hook design:
- finally_after does cheap capture only (no aggregation, no I/O)
- variant=None → runtime_default_used True
- eval_time_ms from metadata["dd.eval.timestamp_ms"] when present; fallback to hook-fire time
- DD_FLAGGING_EVALUATION_COUNTS_ENABLED killswitch gates the EVP path only
"""

import json
import os
import time
import typing
from unittest import mock

from openfeature.evaluation_context import EvaluationContext
from openfeature.flag_evaluation import FlagEvaluationDetails
from openfeature.flag_evaluation import FlagType
from openfeature.flag_evaluation import Reason
from openfeature.hook import HookContext
import pytest


def _make_hook_context(
    flag_key: str = "my-flag",
    targeting_key: str = "user-1",
    attrs: dict = None,
) -> HookContext:
    ctx = EvaluationContext(targeting_key=targeting_key, attributes=attrs or {})
    return HookContext(
        flag_key=flag_key,
        flag_type=FlagType.BOOLEAN,
        default_value=False,
        evaluation_context=ctx,
    )


def _make_details(
    flag_key: str = "my-flag",
    value: typing.Any = True,
    variant: typing.Optional[str] = "on",
    reason: typing.Optional[Reason] = Reason.TARGETING_MATCH,
    flag_metadata: dict = None,
    error_message: str = None,
) -> FlagEvaluationDetails:
    return FlagEvaluationDetails(
        flag_key=flag_key,
        value=value,
        variant=variant,
        reason=reason,
        flag_metadata=flag_metadata or {},
        error_message=error_message,
    )


@pytest.fixture
def writer():
    from ddtrace.internal.openfeature._flagevaluation_writer import FlagEvaluationWriter

    return mock.MagicMock(spec=FlagEvaluationWriter)


@pytest.fixture
def hook(writer):
    from ddtrace.internal.openfeature._flagevaluation_hook import FlagEvaluationHook

    return FlagEvaluationHook(writer=writer)


class TestFlagEvaluationHook:
    def test_finally_after_calls_writer_enqueue_once(self, hook, writer):
        """finally_after must call writer.enqueue exactly once per evaluation."""
        hc = _make_hook_context()
        details = _make_details()
        hook.finally_after(hc, details, {})
        writer.enqueue.assert_called_once()

    def test_finally_after_enqueues_correct_flag_key(self, hook, writer):
        hc = _make_hook_context(flag_key="test-flag")
        details = _make_details(flag_key="test-flag")
        hook.finally_after(hc, details, {})
        event = writer.enqueue.call_args[0][0]
        assert event.flag_key == "test-flag"

    def test_finally_after_enqueues_correct_variant(self, hook, writer):
        hc = _make_hook_context()
        details = _make_details(variant="control")
        hook.finally_after(hc, details, {})
        event = writer.enqueue.call_args[0][0]
        assert event.variant == "control"

    def test_finally_after_none_variant_sets_runtime_default(self, hook, writer):
        """None variant → runtime_default=True."""
        hc = _make_hook_context()
        details = _make_details(variant=None)
        hook.finally_after(hc, details, {})
        event = writer.enqueue.call_args[0][0]
        assert event.runtime_default is True
        assert event.variant == ""

    def test_finally_after_present_variant_not_runtime_default(self, hook, writer):
        hc = _make_hook_context()
        details = _make_details(variant="on")
        hook.finally_after(hc, details, {})
        event = writer.enqueue.call_args[0][0]
        assert event.runtime_default is False

    def test_finally_after_reason_normalized_to_upper(self, hook, writer):
        """Reason must be upper-case string in the enqueued event."""
        hc = _make_hook_context()
        details = _make_details(reason=Reason.TARGETING_MATCH)
        hook.finally_after(hc, details, {})
        event = writer.enqueue.call_args[0][0]
        assert event.reason == "TARGETING_MATCH"

    def test_finally_after_eval_time_from_metadata(self, hook, writer):
        """Eval-time must come from metadata["dd.eval.timestamp_ms"] when present."""
        stamp = int(time.time() * 1000) - 500  # 500 ms in the past
        hc = _make_hook_context()
        details = _make_details(flag_metadata={"dd.eval.timestamp_ms": stamp})
        hook.finally_after(hc, details, {})
        event = writer.enqueue.call_args[0][0]
        assert event.eval_time_ms == stamp

    def test_finally_after_eval_time_fallback_to_hook_fire(self, hook, writer):
        """When metadata key absent, fallback to hook-fire time (within 1 second)."""
        before = int(time.time() * 1000)
        hc = _make_hook_context()
        details = _make_details(flag_metadata={})
        hook.finally_after(hc, details, {})
        after = int(time.time() * 1000)
        event = writer.enqueue.call_args[0][0]
        assert before <= event.eval_time_ms <= after + 100

    def test_finally_after_extracts_targeting_key(self, hook, writer):
        hc = _make_hook_context(targeting_key="user-99")
        details = _make_details()
        hook.finally_after(hc, details, {})
        event = writer.enqueue.call_args[0][0]
        assert event.targeting_key == "user-99"

    def test_finally_after_extracts_attrs_shallow_copy(self, hook, writer):
        attrs = {"tier": "premium", "region": "us-west"}
        hc = _make_hook_context(attrs=attrs)
        details = _make_details()
        hook.finally_after(hc, details, {})
        event = writer.enqueue.call_args[0][0]
        assert event.attrs == attrs
        # Must be a copy, not the same object.
        assert event.attrs is not attrs

    def test_finally_after_extracts_allocation_key(self, hook, writer):
        hc = _make_hook_context()
        details = _make_details(flag_metadata={"allocation_key": "alloc-xyz"})
        hook.finally_after(hc, details, {})
        event = writer.enqueue.call_args[0][0]
        assert event.allocation_key == "alloc-xyz"

    def test_finally_after_does_no_aggregation_on_hook_thread(self, hook, writer):
        """The hook must call enqueue only — not build payloads or aggregate."""
        # writer.enqueue is a mock; the only call from finally_after must be enqueue.
        hc = _make_hook_context()
        details = _make_details()
        hook.finally_after(hc, details, {})
        # Confirm ONLY enqueue was called on the writer (no periodic, no aggregate, no send).
        called_methods = {c[0] for c in writer.method_calls}
        assert called_methods == {"enqueue"}, f"Expected only enqueue to be called, got: {called_methods}"

    def test_finally_after_does_not_propagate_exceptions(self, hook, writer):
        """Hook must swallow exceptions — best-effort telemetry."""
        writer.enqueue.side_effect = RuntimeError("boom")
        hc = _make_hook_context()
        details = _make_details()
        # Must not raise.
        hook.finally_after(hc, details, {})


class TestAsyncBoundary:
    """G2: prove the hook does NOT aggregate on the eval call path.

    The hook may only enqueue a cheap snapshot; flatten/prune/canonical-key/aggregate
    must run later in the writer's background worker, never on the eval thread.
    """

    def test_aggregate_not_called_on_hook_path(self):
        """Spy on the REAL writer's _aggregate — it must NOT run during finally_after."""
        from ddtrace.internal.openfeature._flagevaluation_hook import FlagEvaluationHook
        from ddtrace.internal.openfeature._flagevaluation_writer import FlagEvaluationWriter

        real_writer = FlagEvaluationWriter(interval=10.0)
        hook = FlagEvaluationHook(writer=real_writer)

        with mock.patch.object(real_writer, "_aggregate", wraps=real_writer._aggregate) as spy_aggregate:
            hc = _make_hook_context(attrs={"tier": "premium", "region": "us"})
            details = _make_details(flag_metadata={"allocation_key": "a1"})
            hook.finally_after(hc, details, {})

            # The event must be queued but NOT aggregated on the hook path.
            spy_aggregate.assert_not_called()
            assert real_writer._queue.qsize() == 1
            # The aggregation maps are still empty — no flatten/prune/key happened yet.
            assert real_writer._full == {}
            assert real_writer._degraded == {}

        # Aggregation only happens when the worker drains (periodic), off the hook path.
        # _aggregate fires exactly once during the drain, producing one full-tier row.
        with mock.patch.object(real_writer, "_aggregate", wraps=real_writer._aggregate) as spy_drain:
            with mock.patch.object(real_writer, "_send_payload") as mock_send:
                real_writer.periodic()
            spy_drain.assert_called_once()
        decoded = json.loads(mock_send.call_args[0][0])
        assert len(decoded["flagEvaluations"]) == 1

    def test_canonical_key_not_computed_on_hook_path(self):
        """canonical_context_key (the keying cost) must not run during finally_after."""
        from ddtrace.internal.openfeature._flagevaluation_hook import FlagEvaluationHook
        import ddtrace.internal.openfeature._flagevaluation_writer as writer_mod
        from ddtrace.internal.openfeature._flagevaluation_writer import FlagEvaluationWriter

        real_writer = FlagEvaluationWriter(interval=10.0)
        hook = FlagEvaluationHook(writer=real_writer)

        with mock.patch.object(writer_mod, "canonical_context_key", wraps=writer_mod.canonical_context_key) as spy_key:
            hc = _make_hook_context(attrs={"a": "b"})
            hook.finally_after(hc, _make_details(), {})
            spy_key.assert_not_called()


class TestMetadataSourceMatchesOTelHook:
    """G7: EVP hook reads allocation-key/eval metadata from the SAME source as the OTel hook.

    The existing OTel FlagEvalHook reads allocation_key from
    ``details.flag_metadata[METADATA_ALLOCATION_KEY]``. The EVP hook must read from the
    identical source so the two paths agree byte-for-byte on metadata.
    """

    def test_allocation_key_metadata_key_matches_otel_hook(self):
        from ddtrace.internal.openfeature import _flageval_metrics
        from ddtrace.internal.openfeature import _flagevaluation_writer

        # Same metadata key constant in both modules.
        assert _flagevaluation_writer.METADATA_ALLOCATION_KEY == _flageval_metrics.METADATA_ALLOCATION_KEY

    def test_evp_hook_reads_allocation_from_details_flag_metadata(self, hook, writer):
        """EVP hook reads allocation_key from details.flag_metadata (not hook_context)."""
        from ddtrace.internal.openfeature._flageval_metrics import METADATA_ALLOCATION_KEY

        hc = _make_hook_context()
        details = _make_details(flag_metadata={METADATA_ALLOCATION_KEY: "alloc-from-details"})
        hook.finally_after(hc, details, {})
        event = writer.enqueue.call_args[0][0]
        assert event.allocation_key == "alloc-from-details"

    def test_otel_and_evp_hooks_extract_same_allocation_key(self):
        """Drive both hooks with identical details; both must surface the same allocation key."""
        from ddtrace.internal.openfeature._flageval_metrics import METADATA_ALLOCATION_KEY
        from ddtrace.internal.openfeature._flageval_metrics import FlagEvalHook
        from ddtrace.internal.openfeature._flageval_metrics import FlagEvalMetrics
        from ddtrace.internal.openfeature._flagevaluation_hook import FlagEvaluationHook
        from ddtrace.internal.openfeature._flagevaluation_writer import FlagEvaluationWriter

        details = _make_details(flag_metadata={METADATA_ALLOCATION_KEY: "shared-alloc"})
        hc = _make_hook_context()

        # OTel side: capture what FlagEvalMetrics.record received as allocation_key.
        metrics = mock.MagicMock(spec=FlagEvalMetrics)
        otel_hook = FlagEvalHook(metrics)
        otel_hook.finally_after(hc, details, {})
        otel_alloc = metrics.record.call_args.kwargs["allocation_key"]

        # EVP side: capture what the EVP hook enqueued as allocation_key.
        evp_writer = mock.MagicMock(spec=FlagEvaluationWriter)
        evp_hook = FlagEvaluationHook(evp_writer)
        evp_hook.finally_after(hc, details, {})
        evp_alloc = evp_writer.enqueue.call_args[0][0].allocation_key

        assert otel_alloc == evp_alloc == "shared-alloc"


class TestKillswitchGating:
    def test_default_enabled_registers_evp_hook(self):
        """Default (no env var set) must register the EVP hook + writer."""
        from ddtrace.internal.openfeature._flagevaluation_hook import FlagEvaluationHook

        env = {k: v for k, v in os.environ.items() if k != "DD_FLAGGING_EVALUATION_COUNTS_ENABLED"}
        # No env var → enabled by default.
        with mock.patch.dict(os.environ, env, clear=True):
            from tests.utils import override_global_config

            with override_global_config({"experimental_flagging_provider_enabled": True}):
                from ddtrace.internal.openfeature._provider import DataDogProvider

                provider = DataDogProvider()
                assert provider._flagevaluation_writer is not None
                assert provider._flagevaluation_hook is not None
                assert isinstance(provider._flagevaluation_hook, FlagEvaluationHook)

    def test_killswitch_false_does_not_register_evp_hook(self):
        """DD_FLAGGING_EVALUATION_COUNTS_ENABLED=false must suppress EVP hook (killswitch)."""
        with mock.patch.dict(os.environ, {"DD_FLAGGING_EVALUATION_COUNTS_ENABLED": "false"}):
            from tests.utils import override_global_config

            with override_global_config({"experimental_flagging_provider_enabled": True}):
                from ddtrace.internal.openfeature._provider import DataDogProvider

                provider = DataDogProvider()
                assert provider._flagevaluation_writer is None
                assert provider._flagevaluation_hook is None

    def test_killswitch_false_does_not_affect_otel_hook(self):
        """Killswitch must not suppress the OTel FlagEvalHook (OTel non-regression)."""
        with mock.patch.dict(os.environ, {"DD_FLAGGING_EVALUATION_COUNTS_ENABLED": "false"}):
            from tests.utils import override_global_config

            with override_global_config(
                {"experimental_flagging_provider_enabled": True, "_otel_metrics_enabled": True}
            ):
                from ddtrace.internal.openfeature._provider import DataDogProvider

                provider = DataDogProvider()
                # OTel hook still present.
                assert provider._flag_eval_hook is not None
                # EVP hook absent.
                assert provider._flagevaluation_hook is None
                # get_provider_hooks still returns the OTel hook.
                hooks = provider.get_provider_hooks()
                assert len(hooks) == 1
                assert hooks[0] is provider._flag_eval_hook

    def test_killswitch_enabled_true_registers_evp_hook(self):
        """DD_FLAGGING_EVALUATION_COUNTS_ENABLED=true must register the EVP hook."""
        with mock.patch.dict(os.environ, {"DD_FLAGGING_EVALUATION_COUNTS_ENABLED": "true"}):
            from tests.utils import override_global_config

            with override_global_config({"experimental_flagging_provider_enabled": True}):
                from ddtrace.internal.openfeature._provider import DataDogProvider

                provider = DataDogProvider()
                assert provider._flagevaluation_writer is not None
                assert provider._flagevaluation_hook is not None
