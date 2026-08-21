from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from ddtrace._trace import context as context_module
from ddtrace._trace.context import _update_otel_sampling_decision
from ddtrace._trace.sampler import DatadogSampler
from ddtrace._trace.sampling_rule import SamplingRule
from ddtrace.constants import USER_KEEP
from ddtrace.constants import USER_REJECT
from ddtrace.internal.constants import SAMPLING_DECISION_TRACE_TAG_KEY
from ddtrace.internal.constants import SamplingMechanism
from ddtrace.internal.sampling import _set_sampling_tags
from ddtrace.trace import Context
from ddtrace.trace import Span
from tests.utils import override_global_config


def _ot_fields(tracestate):
    for member in tracestate.split(","):
        if member.startswith("ot="):
            return dict(item.split(":", 1) for item in member[3:].split(";") if ":" in item)
    return {}


def test_sampling_priority_is_published_after_otel_tracestate(monkeypatch):
    span = Span("test", trace_id=1, span_id=1)
    state_update_started = Event()
    allow_state_update = Event()
    original_resolve = context_module.resolve_otel_sampling_decision

    def blocking_resolve(*args, **kwargs):
        state_update_started.set()
        assert allow_state_update.wait(2)
        return original_resolve(*args, **kwargs)

    monkeypatch.setattr(context_module, "resolve_otel_sampling_decision", blocking_resolve)

    with ThreadPoolExecutor(max_workers=1) as executor:
        sampling = executor.submit(
            _set_sampling_tags,
            span,
            True,
            0.1,
            SamplingMechanism.LOCAL_USER_TRACE_SAMPLING_RULE,
        )
        assert state_update_started.wait(2)
        lock_acquired = span.context._lock.acquire(False)
        if lock_acquired:
            span.context._lock.release()
        try:
            assert span.context.sampling_priority is None
            assert lock_acquired is False
        finally:
            allow_state_update.set()
        sampling.result()

    assert span.context.sampling_priority == USER_KEEP
    assert _ot_fields(span.context._meta["tracestate"])["th"] == "e6666666666668"


@pytest.mark.parametrize(
    "sample_rate,expected_threshold,expected_sampled",
    [
        (0.01, "fd70a3d70a3d7", False),
        (0.1, "e6666666666668", True),
        (0.2, "ccccccccccccd", True),
        (0.5, "8", True),
        (0.99, "028f5c28f5c29", True),
    ],
)
def test_probabilistic_decision_golden_vectors(sample_rate, expected_threshold, expected_sampled):
    span = Span("test", trace_id=1, span_id=1)
    sampler = DatadogSampler(rules=[SamplingRule(sample_rate=sample_rate)], rate_limit=-1)

    assert sampler.sample(span) is expected_sampled
    assert _ot_fields(span.context._tracestate) == {
        "rv": "f0948a54d43b8e",
        "th": expected_threshold,
    }


@pytest.mark.parametrize(
    "sample_rate,trace_id,expected_random_value,expected_priority",
    [
        (0.1, 0x03A93EE8B1999F00, "e6666666666668", USER_KEEP),
        (0.05, 5401449561355763072, "f333333333332f", USER_REJECT),
    ],
)
def test_probabilistic_decision_reconciles_64_bit_boundary(
    sample_rate, trace_id, expected_random_value, expected_priority
):
    span = Span("test", trace_id=trace_id, span_id=1)
    sampler = DatadogSampler(rules=[SamplingRule(sample_rate=sample_rate)], rate_limit=-1)

    sampler.sample(span)

    assert span.context.sampling_priority == expected_priority
    assert _ot_fields(span.context._tracestate)["rv"] == expected_random_value


def test_inbound_otel_fields_and_unknown_fields_are_forwarded():
    context = Context(
        trace_id=1,
        span_id=1,
        sampling_priority=1,
        meta={
            "tracestate": "ot=th:e6666666666668;rv:ef284ace7a91e1;foo:bar,congo=t61rcWkgMzE",
        },
    )

    assert context._tracestate == ("dd=s:1,ot=rv:ef284ace7a91e1;th:e6666666666668;foo:bar,congo=t61rcWkgMzE")


def test_inbound_threshold_does_not_fabricate_random_value():
    context = Context(
        trace_id=1,
        span_id=1,
        sampling_priority=1,
        meta={"tracestate": "ot=th:e6666666666668"},
    )

    assert context._tracestate == "dd=s:1,ot=th:e6666666666668"


def test_inbound_threshold_keeps_trace_id_randomness_usable():
    context = Context(
        trace_id=1,
        span_id=1,
        sampling_priority=1,
        meta={
            "traceparent": "00-00000000000000000000000000000001-0000000000000001-03",
            "tracestate": "ot=th:e6666666666668",
        },
    )

    assert context._traceparent.endswith("-03")
    assert context._tracestate == "dd=s:1,ot=th:e6666666666668"


def test_inherited_sampling_decision_without_otel_fields_does_not_fabricate_them():
    context = Context(trace_id=1, span_id=1, sampling_priority=1, meta={"tracestate": "congo=value"})

    assert context._tracestate == "dd=s:1,congo=value"


def test_raw_datadog_member_is_preserved_when_it_cannot_be_rebuilt():
    context = Context(meta={"tracestate": "congo=value,dd=t.foo:bar"})

    assert context._tracestate == "dd=t.foo:bar,congo=value"


@pytest.mark.parametrize(
    "inbound,expected",
    [
        ("ot=rv:not-hex;th:not-hex,congo=value", "dd=s:1,congo=value"),
        ("ot=rv:1234567890abcd;th:not-hex", "dd=s:1,ot=rv:1234567890abcd"),
    ],
)
def test_malformed_otel_fields_are_cleared_independently(inbound, expected):
    context = Context(trace_id=1, span_id=1, sampling_priority=1, meta={"tracestate": inbound})

    assert context._tracestate == expected


def test_non_probabilistic_decision_clears_threshold_and_preserves_inbound_random_value():
    context = Context(
        trace_id=1,
        span_id=1,
        sampling_priority=USER_KEEP,
        meta={
            "tracestate": "ot=rv:1234567890abcd;th:e6666666666668;future:value",
        },
    )
    with context:
        _update_otel_sampling_decision(context, True, 0.0, False)

    assert context._tracestate == "dd=s:2,ot=rv:1234567890abcd;future:value"


def test_rate_limiter_drop_does_not_emit_otel_threshold():
    span = Span("test", trace_id=1, span_id=1)
    sampler = DatadogSampler(rules=[SamplingRule(sample_rate=1.0)], rate_limit=0)

    sampler.sample(span)

    assert span.context.sampling_priority == USER_REJECT
    assert "ot=" not in span.context._tracestate


def test_zero_sample_rate_does_not_emit_otel_threshold():
    span = Span("test", trace_id=1, span_id=1)
    sampler = DatadogSampler(rules=[SamplingRule(sample_rate=0.0)], rate_limit=-1)

    sampler.sample(span)

    assert "ot=" not in span.context._tracestate


def test_global_sampling_rule_emits_otel_threshold():
    with override_global_config({"_trace_sampling_rules": '[{"sample_rate": 0.1}]'}):
        span = Span("test", trace_id=1, span_id=1)
        sampler = DatadogSampler(rate_limit=-1)

    sampler.sample(span)

    assert _ot_fields(span.context._tracestate) == {
        "rv": "f0948a54d43b8e",
        "th": "e6666666666668",
    }


def test_non_probabilistic_sampling_mechanism_does_not_emit_otel_fields():
    span = Span("test", trace_id=1, span_id=1)
    sampler = DatadogSampler(rate_limit=-1, rate_limit_always_on=True)

    sampler.sample(span)

    assert span.context._meta[SAMPLING_DECISION_TRACE_TAG_KEY] == "-{}".format(SamplingMechanism.APPSEC)
    assert "ot=" not in span.context._tracestate


def test_datadog_and_otel_members_are_kept_leftmost_under_member_cap():
    other_members = ",".join("vendor{}=value".format(i) for i in range(32))
    context = Context(
        trace_id=1,
        span_id=1,
        sampling_priority=USER_KEEP,
        meta={
            "tracestate": other_members,
            SAMPLING_DECISION_TRACE_TAG_KEY: "-{}".format(SamplingMechanism.LOCAL_USER_TRACE_SAMPLING_RULE),
        },
    )
    with context:
        _update_otel_sampling_decision(context, True, 0.1, True)

    members = context._tracestate.split(",")

    assert len(members) == 32
    assert members[0].startswith("dd=")
    assert members[1].startswith("ot=")


def test_otel_member_is_dropped_when_leading_members_exceed_byte_cap():
    dd_member = "dd=" + ("d" * 253)
    ot_member = "ot=rv:1234567890abcd;th:8;future:" + ("x" * 223)
    assert len(dd_member) == 256
    assert len(ot_member) == 256

    context = Context(meta={"tracestate": "{},{}".format(dd_member, ot_member)})

    assert context._tracestate == dd_member


def test_inherited_otel_fields_remain_authoritative_over_local_sampling_state():
    context = Context(
        trace_id=1,
        span_id=1,
        sampling_priority=USER_KEEP,
        meta={"tracestate": "ot=rv:1234567890abcd;th:8;future:value"},
    )
    with context:
        _update_otel_sampling_decision(context, True, 0.1, True)

    assert context._tracestate == "dd=s:2,ot=rv:1234567890abcd;th:8;future:value"


def test_rebuilt_otel_member_drops_whole_unknown_fields_to_stay_within_value_cap():
    oversized_future_field = "future:" + ("x" * 220)
    context = Context(
        trace_id=1,
        span_id=1,
        sampling_priority=USER_KEEP,
        meta={"tracestate": "ot={};next:value".format(oversized_future_field)},
    )
    assert len(context._meta["tracestate"]) <= 256
    with context:
        _update_otel_sampling_decision(context, True, 0.1, True)

    ot_member = context._tracestate.split(",")[1]

    assert len(ot_member.removeprefix("ot=")) <= 256
    assert _ot_fields(ot_member) == {
        "rv": "f0948a54d43b8e",
        "th": "e6666666666668",
        "next": "value",
    }


def test_rebuilt_otel_member_allows_a_256_character_value():
    ot_value = "future:" + ("x" * 249)
    assert len(ot_value) == 256
    context = Context(meta={"tracestate": "ot={}".format(ot_value)})

    assert context._tracestate == "ot={}".format(ot_value)


def test_probability_sampling_tracestate_is_shared_with_existing_child_contexts():
    root = Context(
        trace_id=1,
        span_id=1,
        sampling_priority=USER_KEEP,
        meta={
            SAMPLING_DECISION_TRACE_TAG_KEY: "-{}".format(SamplingMechanism.LOCAL_USER_TRACE_SAMPLING_RULE),
        },
    )
    child = root.copy(trace_id=1, span_id=2)

    with root:
        _update_otel_sampling_decision(root, True, 0.1, True)

    assert _ot_fields(child._tracestate) == {
        "rv": "f0948a54d43b8e",
        "th": "e6666666666668",
    }


def test_non_probability_sampling_tracestate_is_shared_with_existing_child_contexts():
    root = Context(
        trace_id=1,
        span_id=1,
        sampling_priority=USER_KEEP,
        meta={"tracestate": "ot=rv:1234567890abcd;th:e6666666666668"},
    )
    child = root.copy(trace_id=1, span_id=2)

    with root:
        _update_otel_sampling_decision(root, True, 0.0, False)

    assert child._tracestate == "dd=s:2,ot=rv:1234567890abcd"
