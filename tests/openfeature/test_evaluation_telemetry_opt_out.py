"""Count real provider emissions without sending them to an external intake."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version
import json
from threading import Barrier
from threading import Lock

from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ErrorCode
from openfeature.flag_evaluation import FlagEvaluationOptions
from openfeature.hook import Hook
from openfeature.transaction_context import ContextVarsTransactionContextPropagator
from openfeature.transaction_context.no_op_transaction_context_propagator import NoOpTransactionContextPropagator
import pytest

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
from tests.utils import DummyWriter
from tests.utils import override_global_config


PARAMETER = "dd_evaluation_telemetry_opt_out"
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
        set_provider = getattr(api, "set_provider_and_wait", api.set_provider)
        set_provider(provider, domain)
        emissions = Emissions(provider, api.get_client(domain), exposures, counter)
        monkeypatch.setattr(provider._flag_eval_evp_writer, "_send_payload", emissions.capture)
        providers.append(provider)
        return emissions

    yield make
    assert all(provider._evaluation_telemetry.get() is None for provider in providers)
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
    # No invocation attribute: this also checks 0.8.0, which gives invocation
    # attributes precedence over before-hook returns.
    api.set_evaluation_context(EvaluationContext(attributes={PARAMETER: not returned}))
    options = FlagEvaluationOptions(hooks=[ReturnContext(returned)])
    details = emissions.evaluate(EvaluationContext(targeting_key="before-return"), options=options)
    assert details.value is True
    count = int(not returned)
    assert emissions.counts() == {"evaluations": count, "metrics": count, "annotated_spans": count, "exposures": 1}


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
def test_all_flag_types_keep_native_results_and_exposures(emission_factory, kind, flag, default, expected):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=True)
    assert process_ffe_configuration(create_config(flag))
    details = emissions.evaluate(context("typed", True), kind=kind, default=default)
    assert details.value == expected
    assert details.error_code is None
    assert emissions.counts() == {"evaluations": 0, "metrics": 0, "annotated_spans": 0, "exposures": 1}


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
    # 0.8.0 merges invocation attributes after before-hook returns. Later SDKs
    # give the before-hook return precedence. Use the actual final context.
    count = int(version("openfeature-sdk") != "0.8.0")
    assert emissions.counts() == {"evaluations": count, "metrics": count, "annotated_spans": count, "exposures": 1}


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


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("failing", [None, "span", "evp", "metrics"])
def test_hook_order_and_exception_isolation_are_unchanged(emission_factory, monkeypatch, enabled, failing):
    emissions = emission_factory(enable_evaluation_telemetry_opt_out_parameter=enabled)
    calls = []

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
    assert emissions.evaluate(context("hook-order")).value is True
    assert calls == ["span", "evp", "metrics"]
    assert emissions.counts() == {
        "evaluations": int(failing != "evp"),
        "metrics": int(failing != "metrics"),
        "annotated_spans": int(failing != "span"),
        "exposures": 1,
    }
