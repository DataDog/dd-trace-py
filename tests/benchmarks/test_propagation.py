import pytest

from ddtrace.propagation.http import HTTPPropagator
from ddtrace.propagation.utils import from_wsgi_header


@pytest.mark.benchmark(group="propagation-utils", min_time=0.005)
def test_from_wsgi_header(benchmark):
    benchmark(from_wsgi_header, "HTTP_TEST_HEADER")


def test_extract_header_value(benchmark):
    benchmark(HTTPPropagator().extract, {"HTTP_TEST_HEADER" + str(i): i for i in range(1000)})
