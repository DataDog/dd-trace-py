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
        (True, None, SamplingMechanism.DEFAULT, True, "-0"),
        (True, None, SamplingMechanism.AGENT_RATE, True, "-1"),
        (True, None, SamplingMechanism.TRACE_SAMPLING_RULE, True, "-3"),
        (True, None, SamplingMechanism.MANUAL, True, "-4"),
        (SamplingMechanism.DEFAULT, True, "-0"),
        (SamplingMechanism.AGENT_RATE, True, "-1"),
        (SamplingMechanism.TRACE_SAMPLING_RULE, True, "-3"),
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
