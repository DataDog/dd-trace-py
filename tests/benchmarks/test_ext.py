from hypothesis import given

from ddtrace.ext.aws import _flatten_dict
from tests.tracer.test_ext import nested_dicts


def test_flatten_dict_hypothesis(benchmark):
    @given(nested_dicts)
    def flatten_dict(d):
        _flatten_dict(d)

    benchmark(flatten_dict)
