import pytest

from ddtrace.context import Context
from ddtrace.internal.sampling import SamplingMechanism
from ddtrace.internal.sampling import update_sampling_decision


@pytest.fixture()
def context():
    yield Context()


@pytest.mark.parametrize(
    "sampling_mechanism,sampled,expected",
    [
        (SamplingMechanism.AGENT_RATE, True, "-1"),
        (SamplingMechanism.TRACE_SAMPLING_RULE, True, "-3"),
        (SamplingMechanism.DEFAULT, True, "-0"),
        (SamplingMechanism.MANUAL, True, "-4"),
        (SamplingMechanism.DEFAULT, True, "-0"),
        (SamplingMechanism.DEFAULT, False, None),
    ],
)
def test_trace_tag(context, sampling_mechanism, sampled, expected):

    update_sampling_decision(context, sampling_mechanism, sampled)
    if sampled:
        assert context._meta["_dd.p.dm"] == expected
    else:
        assert "_dd.p.dm" not in context._meta
