"""Count real provider emissions without sending them to an external intake."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
import logging
from threading import Barrier
from threading import Lock

from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ErrorCode
from openfeature.flag_evaluation import FlagEvaluationOptions
from openfeature.flag_evaluation import Reason
from openfeature.hook import Hook
from openfeature.provider import ProviderStatus
from openfeature.transaction_context import ContextVarsTransactionContextPropagator
from openfeature.transaction_context.no_op_transaction_context_propagator import NoOpTransactionContextPropagator
import pytest

from ddtrace.internal import logger
from ddtrace.internal.openfeature import _provider
from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._flagevaluation_writer import FlagEvaluationWriter
from ddtrace.internal.openfeature._native import process_ffe_configuration
from ddtrace.internal.openfeature.writer import ExposureWriter
from ddtrace.openfeature import DataDogProvider
from ddtrace.trace import tracer
from tests.openfeature.config_helpers import create_boolean_flag
from tests.openfeature.config_helpers import create_config
from tests.openfeature.config_helpers import create_float_flag
from tests.openfeature.config_helpers import create_integer_flag
from tests.openfeature.config_helpers import create_json_flag
from tests.openfeature.config_helpers import create_string_flag
from tests.openfeature.conftest import set_openfeature_provider
from tests.utils import DummyWriter
from tests.utils import override_global_config


PARAMETER = "dd_evaluation_telemetry_opt_out"
ACTIVATION_ENV = "DD_FEATURE_FLAGS_EVALUATION_TELEMETRY_PER_SUBJECT_OPT_OUT_ENABLED"
FLAG = "telemetry-opt-out"


def context(subject, opted_out=False):
    return EvaluationContext(targeting_key=subject, attributes={PARAMETER: opted_out})


class RecordingCounter:
    def __init__(self):
        self.measurements = []
        self.lock = Lock()

    def add(self, value, attributes):
        with self.lock:
            self.measurements.append((value, attributes))


class Emissions:
    def __init__(self, provider, client, exposures, counter):
        self.provider = provider
        self.client = client
        self.exposures = exposures
        self.counter = counter
        self.rows = []
        self.spans = []

    def capture(self, payload, num_events):
        self.rows.extend(json.loads(payload)["flagEvaluations"])

    def evaluate(self, context, flag_key=FLAG, default=False, options=None, kind="boolean"):
        with tracer.trace("evaluation-opt-out") as span:
            details = getattr(self.client, "get_%s_details" % kind)(flag_key, default, context, options)
        self.spans.append(span)
        return details

    def counts(self):
        self.provider._flag_eval_evp_writer.periodic()
        exposures = json.loads(self.exposures._encode(self.exposures._buffer))["exposures"]
        return {
            "evaluations": sum(row["evaluation_count"] for row in self.rows),
            "metrics": sum(value for value, _ in self.counter.measurements),
            "annotated_spans": sum(
                any(
                    span.get_tag(key) is not None
                    for key in ("ffe_flags_enc", "ffe_subjects_enc", "ffe_runtime_defaults")
                )
                for span in self.spans
            ),
            "exposures": len(exposures),
        }


@pytest.fixture
def emission_factory(monkeypatch):
    monkeypatch.delenv(ACTIVATION_ENV, raising=False)
    _set_ffe_config(None)
    api.clear_hooks()
    api.set_evaluation_context(EvaluationContext())
    api.set_transaction_context_propagator(ContextVarsTransactionContextPropagator())
    api.set_transaction_context(EvaluationContext())
    monkeypatch.setattr(tracer._span_aggregator, "writer", DummyWriter())
    monkeypatch.setattr(DataDogProvider, "_start_configuration_source", lambda self: None)
    monkeypatch.setattr(_provider, "start_exposure_writer", lambda: None)
    monkeypatch.setattr(_provider, "stop_exposure_writer", lambda: None)
    monkeypatch.setattr(FlagEvaluationWriter, "start", lambda self: None)
    providers = []
    exposures = ExposureWriter(enabled=True)
    monkeypatch.setattr(_provider, "get_exposure_writer", lambda: exposures)

    def make(observe_full=False, **options):
        flag = create_boolean_flag(FLAG)
        flag["allocations"][0]["splits"][0]["serialId"] = 340132
        config = create_config(flag)
        config["observeFullEvaluationData"] = observe_full
        assert process_ffe_configuration(config)
        with override_global_config(
            {
                "experimental_flagging_provider_enabled": True,
                "experimental_flagging_provider_span_enrichment_enabled": True,
            }
        ):
            provider = DataDogProvider(**options)
        counter = RecordingCounter()
        provider._flag_eval_metrics._counter = counter
        provider._flag_eval_metrics._enabled = True
        domain = "telemetry-opt-out-%d" % len(providers)
        set_openfeature_provider(provider, domain)
        emissions = Emissions(provider, api.get_client(domain), exposures, counter)
        monkeypatch.setattr(provider._flag_eval_evp_writer, "_send_payload", emissions.capture)
        providers.append(provider)
        return emissions

    try:
        yield make
        assert all(provider._evaluation_telemetry.get() is None for provider in providers)
    finally:
        api.clear_hooks()
        api.set_evaluation_context(EvaluationContext())
        api.set_transaction_context(EvaluationContext())
        api.shutdown()
        api.set_transaction_context_propagator(NoOpTransactionContextPropagator())
        _set_ffe_config(None)


def test_boolean_true_suppresses_all_evaluation_signals(emission_factory):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True)
    details = emissions.evaluate(EvaluationContext(targeting_key="opted-out", attributes={PARAMETER: True}))
    assert details.value is True
    assert details.variant == "true"
    assert details.error_code is None
    assert emissions.counts() == {"evaluations": 0, "metrics": 0, "annotated_spans": 0, "exposures": 1}


@pytest.mark.parametrize("options", [{}, {"enable_evaluation_telemetry_opt_out_parameter": False}])
def test_parameter_requires_provider_activation(emission_factory, options):
    emissions = emission_factory(**options)
    assert emissions.evaluate(context("control", True)).value is True
    assert emissions.counts() == {"evaluations": 1, "metrics": 1, "annotated_spans": 1, "exposures": 1}


def configure_attribute_targeting(observe_full=False):
    flag = create_boolean_flag(FLAG)
    flag["allocations"][0]["splits"][0]["serialId"] = 340132
    flag["allocations"][0]["rules"] = [
        {"conditions": [{"attribute": PARAMETER, "operator": "ONE_OF", "value": ["true"]}]}
    ]
    flag["allocations"].append(
        {
            "key": "fallback",
            "splits": [{"variationKey": "false", "shards": [], "serialId": 340133}],
            "doLog": True,
        }
    )
    config = create_config(flag)
    config["observeFullEvaluationData"] = observe_full
    assert process_ffe_configuration(config)


@pytest.mark.parametrize("activation", [False, True])
@pytest.mark.parametrize("observe_full", [False, True])
@pytest.mark.parametrize("value", [False, True, "true"])
def test_ordinary_attribute_retains_targeting_and_exposure_input(emission_factory, activation, observe_full, value):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=activation, observe_full=observe_full)
    configure_attribute_targeting(observe_full)
    for _ in range(2):
        details = emissions.evaluate(context("subject", value))
        assert details.value is (value is not False)
        assert details.variant == ("false" if value is False else "true")
        assert details.reason == (Reason.STATIC if value is False else Reason.TARGETING_MATCH)
        assert details.error_code is None

    count = 0 if activation and value is True else 2
    assert emissions.counts() == {"evaluations": count, "metrics": count, "annotated_spans": count, "exposures": 1}
    exposures = json.loads(emissions.exposures._encode(emissions.exposures._buffer))["exposures"]
    assert exposures[0]["subject"] == {"id": "subject", "attributes": {PARAMETER: value}}
    for row in emissions.rows:
        if observe_full:
            assert row["context"]["evaluation"] == {PARAMETER: value}
            assert row["targeting_key"] == "subject"
        else:
            assert "context" not in row
            assert row["targeting_key"] == "sha256_" + hashlib.sha256(b"subject").hexdigest()


@pytest.mark.parametrize("activation", [False, True])
def test_ordinary_attribute_changes_follow_exposure_deduplication(emission_factory, activation):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=activation)
    configure_attribute_targeting()
    for value in [False, True, False]:
        assert emissions.evaluate(context("same-subject", value)).value is value

    count = 2 if activation else 3
    assert emissions.counts() == {"evaluations": count, "metrics": count, "annotated_spans": count, "exposures": 3}
    exposures = json.loads(emissions.exposures._encode(emissions.exposures._buffer))["exposures"]
    assert [exposure["subject"]["attributes"] for exposure in exposures] == [
        {PARAMETER: value} for value in [False, True, False]
    ]


@pytest.mark.parametrize("env_value,enabled", [(None, False), ("true", True), ("false", False), ("invalid", False)])
def test_environment_controls_activation(emission_factory, monkeypatch, env_value, enabled):
    if env_value is not None:
        monkeypatch.setenv(ACTIVATION_ENV, env_value)
    emissions = emission_factory()
    details = emissions.evaluate(context("environment", True))
    assert details.value is True
    assert details.variant == "true"
    assert details.error_code is None
    count = int(not enabled)
    assert emissions.counts() == {"evaluations": count, "metrics": count, "annotated_spans": count, "exposures": 1}


@pytest.mark.parametrize(
    "env_value,option,enabled",
    [("true", False, False), ("false", True, True), ("true", None, True), ("false", None, False)],
)
def test_environment_and_constructor_precedence(emission_factory, monkeypatch, env_value, option, enabled):
    monkeypatch.setenv(ACTIVATION_ENV, env_value)
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=option)
    details = emissions.evaluate(context("precedence", True))
    assert details.value is True
    assert details.variant == "true"
    assert details.error_code is None
    count = int(not enabled)
    assert emissions.counts() == {"evaluations": count, "metrics": count, "annotated_spans": count, "exposures": 1}


@pytest.mark.parametrize("attributes", [{}, {PARAMETER: False}, {PARAMETER: None}, {PARAMETER: "true"}, {PARAMETER: 1}])
def test_environment_activation_still_requires_subject_boolean_true(emission_factory, monkeypatch, attributes):
    monkeypatch.setenv(ACTIVATION_ENV, "true")
    emissions = emission_factory()
    details = emissions.evaluate(EvaluationContext(targeting_key="allowed", attributes=attributes))
    assert details.value is True
    assert details.variant == "true"
    assert details.error_code is None
    assert emissions.counts() == {"evaluations": 1, "metrics": 1, "annotated_spans": 1, "exposures": 1}


@pytest.mark.parametrize("enabled", [False, True])
def test_environment_activation_is_captured_at_provider_creation(emission_factory, monkeypatch, enabled):
    monkeypatch.setenv(ACTIVATION_ENV, str(enabled).lower())
    original = emission_factory()
    monkeypatch.setenv(ACTIVATION_ENV, str(not enabled).lower())
    assert original.evaluate(context("original", True)).value is True
    count = int(not enabled)
    assert original.counts() == {"evaluations": count, "metrics": count, "annotated_spans": count, "exposures": 1}
    updated = emission_factory()
    assert updated.evaluate(context("updated", True)).value is True
    count = int(enabled)
    assert updated.counts() == {"evaluations": count, "metrics": count, "annotated_spans": count, "exposures": 2}


@pytest.mark.parametrize("value", [False, None, "true", "TRUE", 1, 0, 1.0, {}, []])
def test_only_typed_boolean_true_suppresses(emission_factory, value):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True)
    details = emissions.evaluate(context("allowed", value))
    # Native evaluation already rejects structured attribute values. The
    # telemetry option must preserve this error, not remove the attribute.
    invalid = isinstance(value, (dict, list))
    assert details.value is (not invalid)
    assert details.error_code == (ErrorCode.INVALID_CONTEXT if invalid else None)
    assert emissions.counts() == {"evaluations": 1, "metrics": 1, "annotated_spans": 1, "exposures": int(not invalid)}


@pytest.mark.parametrize("attributes", [{}, {"dd_telemetry_opt_out": True}, {PARAMETER.upper(): True}])
def test_only_exact_parameter_suppresses(emission_factory, attributes):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True)
    assert emissions.evaluate(EvaluationContext(targeting_key="allowed", attributes=attributes)).value is True
    assert emissions.counts() == {"evaluations": 1, "metrics": 1, "annotated_spans": 1, "exposures": 1}


@pytest.mark.parametrize("observe_full", [False, True])
def test_opt_out_preserves_prior_data_and_exposure_deduplication(emission_factory, observe_full):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True, observe_full=observe_full)
    allowed = emissions.evaluate(context("same-subject", False))
    opted_out = emissions.evaluate(context("same-subject", True))
    assert (allowed.value, allowed.variant, allowed.reason, allowed.error_code) == (
        opted_out.value,
        opted_out.variant,
        opted_out.reason,
        opted_out.error_code,
    )
    assert emissions.counts() == {"evaluations": 1, "metrics": 1, "annotated_spans": 1, "exposures": 1}


class ReturnContext(Hook):
    def __init__(self, value):
        self.value = value

    def before(self, hook_context, hints):
        return EvaluationContext(attributes={PARAMETER: self.value})


@pytest.mark.parametrize("returned", [False, True])
def test_before_returned_context_controls_final_decision(emission_factory, returned):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True)
    api.set_evaluation_context(EvaluationContext(attributes={PARAMETER: not returned}))
    options = FlagEvaluationOptions(hooks=[ReturnContext(returned)])
    details = emissions.evaluate(EvaluationContext(targeting_key="before-return"), options=options)
    assert details.value is True
    count = int(not returned)
    assert emissions.counts() == {"evaluations": count, "metrics": count, "annotated_spans": count, "exposures": 1}


@pytest.mark.parametrize("returned", [False, True])
def test_before_returned_context_controls_resolver_error_path(emission_factory, returned):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True)
    api.set_evaluation_context(EvaluationContext(attributes={PARAMETER: not returned}))
    options = FlagEvaluationOptions(hooks=[ReturnContext(returned)])
    details = emissions.evaluate(EvaluationContext(targeting_key="before-error"), flag_key="missing", options=options)
    assert details.error_code == ErrorCode.FLAG_NOT_FOUND
    count = int(not returned)
    assert emissions.counts() == {"evaluations": count, "metrics": count, "annotated_spans": count, "exposures": 0}


class ReturnNonContext(Hook):
    def before(self, hook_context, hints):
        # The SDK fails while merging this, after the gate's before stage ran.
        return {"unrelated": "value"}


@pytest.mark.parametrize("opted_out", [False, True])
def test_failure_after_gate_before_releases_handoff(emission_factory, opted_out):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True)
    details = emissions.evaluate(context("bad", opted_out), options=FlagEvaluationOptions(hooks=[ReturnNonContext()]))
    assert (details.value, details.error_code) == (False, ErrorCode.GENERAL)
    assert emissions.provider._evaluation_telemetry.get() is None
    count = int(not opted_out)
    assert emissions.counts() == {"evaluations": count, "metrics": count, "annotated_spans": count, "exposures": 0}


class MutateAfter(Hook):
    def __init__(self, value):
        self.value = value
        self.calls = []

    def after(self, hook_context, details, hints):
        self.calls.append("after")
        hook_context.evaluation_context.attributes[PARAMETER] = self.value

    def finally_after(self, hook_context, details, hints):
        self.calls.append("finally")


@pytest.mark.parametrize("initial", [False, True])
def test_after_mutations_cannot_restore_or_start_telemetry(emission_factory, initial):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True)
    hook = MutateAfter(not initial)
    assert emissions.evaluate(context("mutated", initial), options=FlagEvaluationOptions(hooks=[hook])).value is True
    assert hook.calls == ["after", "finally"]
    assert emissions.counts() == {"evaluations": 0, "metrics": 0, "annotated_spans": 0, "exposures": 1}


class FailBefore(Hook):
    def before(self, hook_context, hints):
        raise ValueError("application hook failure")

    def error(self, hook_context, exception, hints):
        hook_context.evaluation_context.attributes[PARAMETER] = False


def test_before_error_keeps_snapshot_and_does_not_leak(emission_factory):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True)
    failed = emissions.evaluate(context("failed", True), options=FlagEvaluationOptions(hooks=[FailBefore()]))
    assert failed.value is False
    assert failed.error_code is not None
    assert emissions.counts() == {"evaluations": 0, "metrics": 0, "annotated_spans": 0, "exposures": 0}
    assert emissions.evaluate(context("next")).value is True
    assert emissions.counts() == {"evaluations": 1, "metrics": 1, "annotated_spans": 1, "exposures": 1}


@pytest.mark.parametrize("opted_out", [False, True])
def test_missing_flag_defaults_respect_opt_out(emission_factory, opted_out):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True)
    details = emissions.evaluate(context("missing", opted_out), flag_key="missing")
    assert details.value is False
    assert details.error_code == ErrorCode.FLAG_NOT_FOUND
    count = int(not opted_out)
    assert emissions.counts() == {"evaluations": count, "metrics": count, "annotated_spans": count, "exposures": 0}


@pytest.mark.parametrize("opted_out", [False, True])
@pytest.mark.parametrize(
    "kind,flag,default,expected",
    [
        ("boolean", create_boolean_flag(FLAG), False, True),
        ("string", create_string_flag(FLAG, "variant"), "default", "variant"),
        ("integer", create_integer_flag(FLAG, 42), 0, 42),
        ("float", create_float_flag(FLAG, 4.25), 0.0, 4.25),
        ("object", create_json_flag(FLAG, {"enabled": True}), {}, {"enabled": True}),
    ],
)
def test_all_flag_types_keep_native_results_and_exposures(emission_factory, kind, flag, default, expected, opted_out):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True)
    flag = copy.deepcopy(flag)
    flag["allocations"][0]["splits"][0]["serialId"] = 340132
    assert process_ffe_configuration(create_config(flag))
    details = emissions.evaluate(context("typed", opted_out), kind=kind, default=default)
    assert details.value == expected
    assert details.error_code is None
    count = int(not opted_out)
    assert emissions.counts() == {"evaluations": count, "metrics": count, "annotated_spans": count, "exposures": 1}


@pytest.mark.parametrize("outer_opted_out", [False, True])
def test_nested_evaluations_have_independent_decisions(emission_factory, outer_opted_out):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True)
    nested = []

    class Nested(Hook):
        def after(self, hook_context, details, hints):
            nested.append(emissions.client.get_boolean_details(FLAG, False, context("inner", not outer_opted_out)))

    assert emissions.evaluate(context("outer", outer_opted_out), options=FlagEvaluationOptions(hooks=[Nested()])).value
    assert nested[0].value is True
    assert emissions.counts() == {"evaluations": 1, "metrics": 1, "annotated_spans": 1, "exposures": 2}


def test_concurrent_evaluations_do_not_share_decisions(emission_factory):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True)
    barrier = Barrier(8)

    class Interleave(Hook):
        def after(self, hook_context, details, hints):
            barrier.wait(timeout=10)

    options = FlagEvaluationOptions(hooks=[Interleave()])
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(lambda i: emissions.evaluate(context(str(i), i % 2 == 0), options=options), range(8))
        )
    assert all(result.value is True and result.variant == "true" for result in results)
    assert emissions.counts() == {"evaluations": 4, "metrics": 4, "annotated_spans": 4, "exposures": 8}


@pytest.mark.parametrize("source", ["api", "transaction", "client"])
def test_final_context_includes_all_context_sources(emission_factory, source):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True)
    opted_out = context("merged", True)
    if source == "api":
        api.set_evaluation_context(opted_out)
    elif source == "transaction":
        api.set_transaction_context(opted_out)
    else:
        emissions.client.context = opted_out
    assert emissions.evaluate(EvaluationContext()).value is True
    assert emissions.counts() == {"evaluations": 0, "metrics": 0, "annotated_spans": 0, "exposures": 1}


def test_before_and_invocation_precedence_matches_sdk(emission_factory):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True)
    options = FlagEvaluationOptions(hooks=[ReturnContext(False)])
    assert emissions.evaluate(context("precedence", True), options=options).value is True
    assert emissions.counts() == {"evaluations": 1, "metrics": 1, "annotated_spans": 1, "exposures": 1}


def test_randomization_and_exposure_cache_are_unchanged(emission_factory):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True)
    flag = create_boolean_flag(FLAG)
    flag["allocations"][0]["splits"] = [
        {
            "variationKey": "true",
            "serialId": 11,
            "shards": [{"salt": "test", "totalShards": 10000, "ranges": [{"start": 0, "end": 5000}]}],
        },
        {
            "variationKey": "false",
            "serialId": 12,
            "shards": [{"salt": "test", "totalShards": 10000, "ranges": [{"start": 5000, "end": 10000}]}],
        },
    ]
    assert process_ffe_configuration(create_config(flag))
    variants = set()
    for i in range(20):
        allowed = emissions.evaluate(context(str(i), False))
        opted_out = emissions.evaluate(context(str(i), True))
        assert (allowed.value, allowed.variant, allowed.reason, allowed.error_code) == (
            opted_out.value,
            opted_out.variant,
            opted_out.reason,
            opted_out.error_code,
        )
        variants.add(allowed.variant)
    assert variants == {"true", "false"}
    assert emissions.counts() == {"evaluations": 20, "metrics": 20, "annotated_spans": 20, "exposures": 20}


def test_activation_is_provider_local(emission_factory):
    enabled = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True)
    disabled = emission_factory()
    assert enabled.evaluate(context("enabled", True)).value is True
    assert disabled.evaluate(context("disabled", True)).value is True
    assert enabled.counts() == {"evaluations": 0, "metrics": 0, "annotated_spans": 0, "exposures": 2}
    assert disabled.counts() == {"evaluations": 1, "metrics": 1, "annotated_spans": 1, "exposures": 2}


def test_async_evaluations_have_independent_decisions(emission_factory):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True)

    async def evaluate(i):
        with tracer.trace("async-opt-out") as span:
            details = await emissions.client.get_boolean_details_async(FLAG, False, context(str(i), i % 2 == 0))
        emissions.spans.append(span)
        return details

    async def run():
        return await asyncio.gather(*(evaluate(i) for i in range(8)))

    assert all(details.value is True for details in asyncio.run(run()))
    assert emissions.counts() == {"evaluations": 4, "metrics": 4, "annotated_spans": 4, "exposures": 8}


@pytest.mark.parametrize("opted_out", [False, True])
def test_resolver_in_copied_context_keeps_flag_result(emission_factory, monkeypatch, opted_out):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True)
    provider = emissions.provider

    async def resolve_boolean_details_async(flag_key, default_value, evaluation_context=None):
        # asyncio.to_thread runs the resolver in a copy of the caller's context.
        return await asyncio.to_thread(provider.resolve_boolean_details, flag_key, default_value, evaluation_context)

    async def evaluate():
        with tracer.trace("copied-context") as span:
            details = await emissions.client.get_boolean_details_async(FLAG, False, context("off-loop", opted_out))
        emissions.spans.append(span)
        assert (details.value, details.error_code) == (True, None)
        assert provider._evaluation_telemetry.get() is None
        return details

    monkeypatch.setattr(provider, "resolve_boolean_details_async", resolve_boolean_details_async)
    details = asyncio.run(evaluate())
    assert (details.value, details.error_code) == (True, None)
    count = int(not opted_out)
    assert emissions.counts() == {"evaluations": count, "metrics": count, "annotated_spans": count, "exposures": 1}


def test_wrapped_hooks_only_implement_finally_after(emission_factory):
    # The gate forwards only finally_after; other stages would be dropped.
    (gate,) = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True).provider.get_provider_hooks()
    assert len(gate._hooks) == 3
    for hook in gate._hooks:
        for stage in ("before", "after", "error", "supports_flag_value_type"):
            assert getattr(type(hook), stage) is getattr(Hook, stage), (type(hook).__name__, stage)


def test_sdk_creates_one_gate_per_evaluation(emission_factory, monkeypatch):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True)
    original = emissions.provider.get_provider_hooks
    gates = []

    def get_provider_hooks():
        hooks = original()
        gates.extend(hooks)
        return hooks

    monkeypatch.setattr(emissions.provider, "get_provider_hooks", get_provider_hooks)
    for i in range(5):
        assert emissions.evaluate(context(str(i), True)).value is True
    assert len(gates) == len({id(gate) for gate in gates}) == 5
    assert emissions.counts() == {"evaluations": 0, "metrics": 0, "annotated_spans": 0, "exposures": 5}


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("failing", [None, "span", "evp", "metrics"])
def test_hook_order_and_exception_isolation_are_unchanged(emission_factory, monkeypatch, caplog, enabled, failing):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=enabled)
    calls = []
    # Each parametrized case must observe the warning from this shared call site.
    monkeypatch.setitem(logger._RATE_LIMITS, "OpenFeature evaluation telemetry hook failed", 0)

    def record(name, original):
        def finally_after(hook_context, details, hints):
            calls.append(name)
            if name == failing:
                raise ValueError("telemetry sink failure")
            original(hook_context, details, hints)

        return finally_after

    for name, hook in [
        ("span", emissions.provider._span_enrichment_hook),
        ("evp", emissions.provider._flag_eval_evp_hook),
        ("metrics", emissions.provider._flag_eval_metrics_hook),
    ]:
        monkeypatch.setattr(hook, "finally_after", record(name, hook.finally_after))
    with caplog.at_level(logging.WARNING):
        assert emissions.evaluate(context("hook-order")).value is True
    assert calls == ["span", "evp", "metrics"]
    assert emissions.counts() == {
        "evaluations": int(failing != "evp"),
        "metrics": int(failing != "metrics"),
        "annotated_spans": int(failing != "span"),
        "exposures": 1,
    }
    if enabled and failing is not None:
        assert any(
            record.name == "ddtrace.internal.openfeature._evaluation_telemetry"
            and record.levelno == logging.WARNING
            and record.exc_info is not None
            for record in caplog.records
        )


class ClearOnError(Hook):
    def error(self, hook_context, exception, hints):
        # Runs after the gate's error stage and clears the opt-out.
        hook_context.evaluation_context.attributes[PARAMETER] = False


@pytest.mark.parametrize("status", [ProviderStatus.NOT_READY, ProviderStatus.FATAL])
@pytest.mark.parametrize("source", ["invocation", "api"])
@pytest.mark.parametrize("opted_out", [False, True])
def test_provider_status_short_circuit_respects_opt_out(emission_factory, monkeypatch, status, source, opted_out):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True)
    monkeypatch.setattr(emissions.client, "get_provider_status", lambda: status)
    if source == "api":
        api.set_evaluation_context(context("short-circuit", opted_out))
        subject = EvaluationContext(targeting_key="short-circuit")
    else:
        subject = context("short-circuit", opted_out)
    details = emissions.evaluate(subject, options=FlagEvaluationOptions(hooks=[ClearOnError()]))
    expected = ErrorCode.PROVIDER_NOT_READY if status == ProviderStatus.NOT_READY else ErrorCode.PROVIDER_FATAL
    assert (details.value, details.error_code) == (False, expected)
    assert emissions.provider._evaluation_telemetry.get() is None
    count = int(not opted_out)
    assert emissions.counts() == {"evaluations": count, "metrics": count, "annotated_spans": count, "exposures": 0}


FINAL_TAGS = ("ffe_flags_enc", "ffe_subjects_enc", "ffe_runtime_defaults")


def _options(i):
    return FlagEvaluationOptions(hooks=[ReturnContext(False)] if i % 2 == 0 else [])


def _annotated(span):
    return any(span.get_tag(key) is not None for key in FINAL_TAGS)


def test_concurrent_handoffs_capture_their_own_final_context(emission_factory, monkeypatch):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True)
    provider = emissions.provider
    original = provider.resolve_boolean_details
    barrier = Barrier(8)

    def resolve_boolean_details(flag_key, default_value, evaluation_context=None):
        # Every gate's before stage has run, and no resolver has captured yet.
        barrier.wait(timeout=10)
        return original(flag_key, default_value, evaluation_context)

    monkeypatch.setattr(provider, "resolve_boolean_details", resolve_boolean_details)

    def evaluate(i):
        with tracer.trace("thread-handoff") as span:
            details = emissions.client.get_boolean_details(FLAG, False, context(str(i), True), _options(i))
        emissions.spans.append(span)
        return details, span

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(evaluate, range(8)))
    assert all((d.value, d.error_code) == (True, None) for d, _ in results)
    assert [_annotated(span) for _, span in results] == [i % 2 == 0 for i in range(8)]
    assert emissions.counts() == {"evaluations": 4, "metrics": 4, "annotated_spans": 4, "exposures": 8}


def test_async_handoffs_capture_their_own_final_context(emission_factory, monkeypatch):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True)
    provider = emissions.provider
    original = provider.resolve_boolean_details

    async def resolve_boolean_details_async(flag_key, default_value, evaluation_context=None):
        await asyncio.sleep(0)  # let every task run its before stage first
        return original(flag_key, default_value, evaluation_context)

    monkeypatch.setattr(provider, "resolve_boolean_details_async", resolve_boolean_details_async)

    async def evaluate(i):
        with tracer.trace("async-handoff") as span:
            details = await emissions.client.get_boolean_details_async(FLAG, False, context(str(i), True), _options(i))
        emissions.spans.append(span)
        return details, span

    async def run():
        return await asyncio.gather(*(evaluate(i) for i in range(8)))

    results = asyncio.run(run())
    assert all((d.value, d.error_code) == (True, None) for d, _ in results)
    assert [_annotated(span) for _, span in results] == [i % 2 == 0 for i in range(8)]
    assert emissions.counts() == {"evaluations": 4, "metrics": 4, "annotated_spans": 4, "exposures": 8}


def test_handoff_ends_at_resolver_entry(emission_factory):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True)

    class DirectResolve(Hook):
        def after(self, hook_context, details, hints):
            # No SDK call, so no gate: this resolver must not see the outer handoff.
            emissions.provider.resolve_boolean_details(FLAG, False, context("direct", True))

    assert emissions.evaluate(context("outer"), options=FlagEvaluationOptions(hooks=[DirectResolve()])).value is True
    assert emissions.counts() == {"evaluations": 1, "metrics": 1, "annotated_spans": 1, "exposures": 2}


def test_opt_out_preserves_local_debug_logging(emission_factory, caplog):
    with caplog.at_level(logging.DEBUG, logger="ddtrace.internal.openfeature._debug_logging_hook"):
        emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True)
        details = emissions.evaluate(context("debug-opted-out", True))
    assert (details.value, details.error_code) == (True, None)
    assert emissions.counts() == {"evaluations": 0, "metrics": 0, "annotated_spans": 0, "exposures": 1}
    messages = [record.getMessage() for record in caplog.records]
    assert any("Feature Flags: provider resolved" in message for message in messages)
    assert any("Feature Flags: evaluated" in message for message in messages)
