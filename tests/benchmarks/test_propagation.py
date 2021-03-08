from ddtrace.propagation.http import HTTPPropagator


def test_extract_header_value(benchmark):
    benchmark(HTTPPropagator.extract, {"HTTP_TEST_HEADER" + str(i): i for i in range(1000)})
