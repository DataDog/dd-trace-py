import base64

import pytest

from ddtrace.constants import AUTO_KEEP
from ddtrace.constants import AUTO_REJECT
from ddtrace.constants import USER_KEEP
from ddtrace.context import Context
from ddtrace.internal.compat import PY3
from ddtrace.internal.constants import SamplingMechanism
from ddtrace.internal.constants import UPSTREAM_SERVICES_KEY
from ddtrace.internal.utils import upstream_services
from ddtrace.span import Span


_padding_char = "=" if PY3 else b"="


@pytest.mark.parametrize(
    "service_name,expected,decoded_expected",
    [
        # b64encode(unnamed_python_service)
        (None, "dW5uYW1lZF9weXRob25fc2VydmljZQ==", b"unnamed_python_service"),
        ("my-service", "bXktc2VydmljZQ==", b"my-service"),
        (b"my-service", "bXktc2VydmljZQ==", b"my-service"),
        (u"my-service", "bXktc2VydmljZQ==", b"my-service"),
        (b"my-\x80-service", "bXktgC1zZXJ2aWNl", b"my-\x80-service"),
        (u"my-\x80-service", "bXktwoAtc2VydmljZQ==", b"my-\xc2\x80-service"),
    ],
)
def test_encode_service(service_name, expected, decoded_expected):
    """Verify that we encode service names to the correct base64 encoding"""
    encoded = upstream_services._encode_service_name(service_name)
    assert expected == encoded

    # Validate we get back out what we put in
    missing_padding = len(encoded) % 4
    if missing_padding:
        encoded += _padding_char * missing_padding

    assert decoded_expected == base64.b64decode(encoded)


@pytest.mark.parametrize(
    "sample_rate,expected",
    [
        (None, ""),
        (1.0, "1.0000"),
        (0.8976, "0.8976"),
        (0.0000000056, "0.0000"),
        (0.0, "0.0000"),
    ],
)
def test_encode_sample_rate(sample_rate, expected):
    """Verify we properly convert floats to strings"""
    assert expected == upstream_services._encode_sample_rate(sample_rate)


@pytest.mark.parametrize(
    "service,sampling_priority,sampling_mechanism,sample_rate,expected",
    [
        (None, AUTO_KEEP, SamplingMechanism.DEFAULT, 1.0, "dW5uYW1lZF9weXRob25fc2VydmljZQ==|1|0|1.0000"),
        (None, AUTO_REJECT, SamplingMechanism.DEFAULT, 1.0, "dW5uYW1lZF9weXRob25fc2VydmljZQ==|0|0|1.0000"),
        (None, AUTO_KEEP, SamplingMechanism.AGENT_RATE, 0.5, "dW5uYW1lZF9weXRob25fc2VydmljZQ==|1|1|0.5000"),
        (None, AUTO_KEEP, SamplingMechanism.USER_RULE, 0.23455689, "dW5uYW1lZF9weXRob25fc2VydmljZQ==|1|3|0.2346"),
        ("service", AUTO_KEEP, SamplingMechanism.DEFAULT, 1.0, "c2VydmljZQ==|1|0|1.0000"),
        (b"\x80service", AUTO_KEEP, SamplingMechanism.DEFAULT, 1.0, "gHNlcnZpY2U=|1|0|1.0000"),
        (u"\x80service", AUTO_KEEP, SamplingMechanism.DEFAULT, 1.0, "woBzZXJ2aWNl|1|0|1.0000"),
        (u"\x80service", USER_KEEP, SamplingMechanism.MANUAL, None, "woBzZXJ2aWNl|2|4|"),
    ],
)
def test_encode_upstream_services_entry(service, sampling_priority, sampling_mechanism, sample_rate, expected):
    assert expected == upstream_services.encode_upstream_service_entry(
        service, sampling_priority, sampling_mechanism, sample_rate
    )


def test_context_update_upstream_services():
    # Helper to ensure:
    #  - Context.upstream_services value is correct
    #  - Context._meta[UPSTREAM_SERVICES_KEY] is correct
    #  - That we properly pass the tag along to the span via Context._update_tags
    def assert_upstream_services(c, e):
        # type: (Context, str) -> None
        assert c.upstream_services == e
        assert c._meta[UPSTREAM_SERVICES_KEY] == e

        s = Span(name="assertion")
        assert s._get_tags() == {}
        c._update_tags(s)
        assert s.get_tag(UPSTREAM_SERVICES_KEY) == e

    # When we have a fresh context
    #  - We do not have any default entry
    c1 = Context()
    assert c1.upstream_services is None

    # When we set an upstream service
    #  - We properly create and set the entry
    s1 = Span(name="test", service="example")
    c1._update_upstream_services(s1, USER_KEEP, SamplingMechanism.USER_RULE, 0.657)
    assert_upstream_services(c1, "ZXhhbXBsZQ==|2|3|0.6570")

    # When we update the upstream service multiple times
    #   - We only keep the most recent entry
    s2 = Span(name="test", service="test-service")
    c1._update_upstream_services(s2, AUTO_KEEP, SamplingMechanism.DEFAULT, 1.0)
    assert_upstream_services(c1, "dGVzdC1zZXJ2aWNl|1|0|1.0000")

    # When the context inherits an entry from an upstream service
    #   - We maintain the upstream entry
    inherited = "dGVzdC1zZXJ2aWNl|1|0|1.0000"
    c3 = Context(upstream_services=inherited)
    assert_upstream_services(c3, inherited)

    # Updating our entry when we have an inherited entry
    #   - We do not remove the upstream entry
    #   - We append our entry
    s3 = Span(name="test", service="my-service")
    c3._update_upstream_services(s3, USER_KEEP, SamplingMechanism.MANUAL, None)
    assert_upstream_services(c3, "{};bXktc2VydmljZQ==|2|4|".format(inherited))

    # Updating our entry multiple times when we have an inherited entry
    #   - We do not remove the upstream entry
    #   - We only keep our most recent entry active
    s3 = Span(name="test", service="another-service")
    c3._update_upstream_services(s3, USER_KEEP, SamplingMechanism.DEFAULT, 0.5)
    assert_upstream_services(c3, "{};YW5vdGhlci1zZXJ2aWNl|2|0|0.5000".format(inherited))
