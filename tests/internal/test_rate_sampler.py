import pytest

from ddtrace.internal.rate_sampler import RateSampler


@pytest.mark.parametrize(
    "sampling_id, expected",
    [
        (6645412625895797507, False),
        (8696342848850656916, True),
        (14629469446186818297, False),
        (13794769880582338323, True),
        (18444899399302180861, False),
        (18446744073709551615, False),
        (10, False),
    ],
)
def test_sample_integer(sampling_id, expected):
    assert RateSampler(0.5).sample(sampling_id) is expected
