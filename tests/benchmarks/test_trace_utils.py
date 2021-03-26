from hypothesis import given

from ddtrace import tracer
from ddtrace.contrib.trace_utils import flatten_dict
from ddtrace.ext.aws import add_span_arg_tags
from tests.tracer.test_trace_utils import nested_dicts


def test_flatten_dict_hypothesis(benchmark):
    @given(nested_dicts)
    def _wrapper(d):
        flatten_dict(d)

    benchmark(_wrapper)


NESTED_DICT = {
    "http": {
        "url": "https://www.example.com/v2/service/123456/get?token=abf545e1ff55acd55ca&view=list#somefragment",
        "query_string": "?token=abf545e1ff55acd55ca&view=list",
        "request_headers": {
            "content_type": "plain/text",
            "http-x-my-header": "foobar",
        },
    },
    "params": {
        "Record": {
            "http": {
                "url": "https://www.example.com/v2/service/123456/get?token=abf545e1ff55acd55ca&view=list#somefragment",
                "query_string": "?token=abf545e1ff55acd55ca&view=list",
                "request_headers": {
                    "content_type": "plain/text",
                    "http-x-my-header": "foobar",
                },
            }
        },
        "Body": {
            "http": {
                "url": "https://www.example.com/v2/service/123456/get?token=abf545e1ff55acd55ca&view=list#somefragment",
                "query_string": "?token=abf545e1ff55acd55ca&view=list",
                "request_headers": {
                    "content_type": "plain/text",
                    "http-x-my-header": "foobar",
                },
            }
        },
    },
}


def test_flatten_dict_full(benchmark):
    benchmark(flatten_dict, NESTED_DICT)


def test_flatten_dict_exclude(benchmark):
    benchmark(flatten_dict, NESTED_DICT, exclude={"params.Record"})


def test_add_span_arg_tags(benchmark):
    def _(tracer):
        with tracer.trace("foo") as span:
            add_span_arg_tags(
                span, "s3", (NESTED_DICT["http"], NESTED_DICT["params"]), ("http", "params"), ("http", "params")
            )

    benchmark(_, tracer)
