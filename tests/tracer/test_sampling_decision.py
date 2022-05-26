import pytest

from ddtrace.context import Context
from ddtrace.internal.sampling import SamplingMechanism
from ddtrace.internal.sampling import _hash_service
from ddtrace.internal.sampling import update_sampling_decision
from tests.utils import override_global_config


@pytest.fixture()
def context():
    yield Context()


@pytest.mark.parametrize(
    "service,expected",
    [
        ("service", "9df6b026a8"),
        (u"\x80service", "069dd5df76"),
        (b"\x80service", None),
    ],
)
def test_hash_service(service, expected):
    assert expected == _hash_service(service)


@pytest.mark.parametrize(
    "propagate_service,service,sampling_mechanism,sampled,expected",
    [
        (True, None, SamplingMechanism.DEFAULT, True, "-0"),
        (True, None, SamplingMechanism.AGENT_RATE, True, "-1"),
        (True, None, SamplingMechanism.TRACE_SAMPLING_RULE, True, "-3"),
        (True, None, SamplingMechanism.MANUAL, True, "-4"),
        (True, "service", SamplingMechanism.DEFAULT, True, "9df6b026a8-0"),
        (True, "service", SamplingMechanism.AGENT_RATE, True, "9df6b026a8-1"),
        (True, "service", SamplingMechanism.TRACE_SAMPLING_RULE, True, "9df6b026a8-3"),
        (True, "service", SamplingMechanism.MANUAL, True, "9df6b026a8-4"),
        (False, "service", SamplingMechanism.DEFAULT, True, "-0"),
        (True, "service", SamplingMechanism.DEFAULT, False, None),
    ],
)
def test_trace_tag(propagate_service, context, service, sampling_mechanism, sampled, expected):
    with override_global_config(
        dict(
            _propagate_service=propagate_service,
        )
    ):
        update_sampling_decision(context, service, sampling_mechanism, sampled)
        if sampled:
            assert context._meta["_dd.p.dm"] == expected
        else:
            assert "_dd.p.dm" not in context._meta
