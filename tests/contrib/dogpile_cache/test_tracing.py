import os

import dogpile
import pytest

from ddtrace.contrib.internal.dogpile_cache.patch import patch
from ddtrace.contrib.internal.dogpile_cache.patch import unpatch
from ddtrace.trace import Pin
from tests.conftest import DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME
from tests.utils import DummyTracer
from tests.utils import TracerSpanContainer
from tests.utils import assert_is_measured


@pytest.fixture
def tracer():
    return DummyTracer()


@pytest.fixture
def test_spans(tracer):
    container = TracerSpanContainer(tracer)
    yield container
    container.reset()


@pytest.fixture
def region(tracer):
    patch()
    # Setup a simple dogpile cache region for testing.
    # The backend is trivial so we can use memory to simplify test setup.
    test_region = dogpile.cache.make_region(name="TestRegion", key_mangler=lambda x: x)
    test_region.configure("dogpile.cache.memory")
    Pin._override(dogpile.cache, tracer=tracer)
    return test_region


@pytest.fixture(autouse=True)
def cleanup():
    yield
    unpatch()


@pytest.fixture
def single_cache(region):
    @region.cache_on_arguments()
    def fn(x):
        return x * 2

    return fn


@pytest.fixture
def multi_cache(region):
    @region.cache_multi_on_arguments()
    def fn(*x):
        return [i * 2 for i in x]

    return fn


def test_doesnt_trace_with_no_pin(tracer, single_cache, multi_cache, test_spans):
    # No pin is set
    unpatch()

    assert single_cache(1) == 2
    assert test_spans.pop_traces() == []

    assert multi_cache(2, 3) == [4, 6]
    assert test_spans.pop_traces() == []


def test_doesnt_trace_with_disabled_pin(tracer, single_cache, multi_cache, test_spans):
    tracer.enabled = False

    assert single_cache(1) == 2
    assert test_spans.pop_traces() == []

    assert multi_cache(2, 3) == [4, 6]
    assert test_spans.pop_traces() == []


def test_traces_get_or_create(tracer, single_cache, test_spans):
    assert single_cache(1) == 2
    traces = test_spans.pop_traces()
    assert len(traces) == 1
    spans = traces[0]
    assert len(spans) == 1
    span = spans[0]

    assert_is_measured(span)
    assert span.name == "dogpile.cache"
    assert span.span_type == "cache"
    assert span.resource == "get_or_create"
    assert span.get_tag("key") == "tests.contrib.dogpile_cache.test_tracing:fn|1"
    assert span.get_tag("hit") == "False"
    assert span.get_tag("expired") == "True"
    assert span.get_tag("backend") == "MemoryBackend"
    assert span.get_tag("region") == "TestRegion"
    assert span.get_tag("component") == "dogpile_cache"
    assert span.get_metric("db.row_count") == 1

    # Now the results should be cached.
    assert single_cache(1) == 2
    traces = test_spans.pop_traces()
    assert len(traces) == 1
    spans = traces[0]
    assert len(spans) == 1
    span = spans[0]

    assert_is_measured(span)
    assert span.name == "dogpile.cache"
    assert span.span_type == "cache"
    assert span.resource == "get_or_create"
    assert span.get_tag("key") == "tests.contrib.dogpile_cache.test_tracing:fn|1"
    assert span.get_tag("hit") == "True"
    assert span.get_tag("expired") == "False"
    assert span.get_tag("backend") == "MemoryBackend"
    assert span.get_tag("region") == "TestRegion"
    assert span.get_tag("component") == "dogpile_cache"
    assert span.get_metric("db.row_count") == 1


def test_traces_get_or_create_multi(tracer, multi_cache, test_spans):
    assert multi_cache(2, 3) == [4, 6]
    traces = test_spans.pop_traces()
    assert len(traces) == 1
    spans = traces[0]
    assert len(spans) == 1
    span = spans[0]

    assert_is_measured(span)
    assert span.name == "dogpile.cache"
    assert span.span_type == "cache"
    assert span.get_tag("keys") == (
        "['tests.contrib.dogpile_cache.test_tracing:fn|2', " + "'tests.contrib.dogpile_cache.test_tracing:fn|3']"
    )
    assert span.get_tag("hit") == "False"
    assert span.get_tag("expired") == "True"
    assert span.get_tag("backend") == "MemoryBackend"
    assert span.get_tag("region") == "TestRegion"
    assert span.get_tag("component") == "dogpile_cache"
    assert span.get_metric("db.row_count") == 2

    # Partial hit
    assert multi_cache(2, 4) == [4, 8]
    traces = test_spans.pop_traces()
    assert len(traces) == 1
    spans = traces[0]
    assert len(spans) == 1
    span = spans[0]
    assert_is_measured(span)
    assert span.name == "dogpile.cache"
    assert span.span_type == "cache"
    assert span.get_tag("keys") == (
        "['tests.contrib.dogpile_cache.test_tracing:fn|2', " + "'tests.contrib.dogpile_cache.test_tracing:fn|4']"
    )
    assert span.get_tag("hit") == "False"
    assert span.get_tag("expired") == "True"
    assert span.get_tag("backend") == "MemoryBackend"
    assert span.get_tag("region") == "TestRegion"
    assert span.get_tag("component") == "dogpile_cache"
    assert span.get_metric("db.row_count") == 2

    # Full hit
    assert multi_cache(2, 4) == [4, 8]
    traces = test_spans.pop_traces()
    assert len(traces) == 1
    spans = traces[0]
    assert len(spans) == 1
    span = spans[0]
    assert_is_measured(span)
    assert span.name == "dogpile.cache"
    assert span.span_type == "cache"
    assert span.get_tag("keys") == (
        "['tests.contrib.dogpile_cache.test_tracing:fn|2', " + "'tests.contrib.dogpile_cache.test_tracing:fn|4']"
    )
    assert span.get_tag("hit") == "True"
    assert span.get_tag("expired") == "False"
    assert span.get_tag("backend") == "MemoryBackend"
    assert span.get_tag("region") == "TestRegion"
    assert span.get_tag("component") == "dogpile_cache"
    assert span.get_metric("db.row_count") == 2


class TestInnerFunctionCalls(object):
    def single_cache(self, x):
        return x * 2

    def multi_cache(self, *x):
        return [i * 2 for i in x]

    def test_calls_inner_functions_correctly(self, region, mocker):
        """This ensures the get_or_create behavior of dogpile is not altered."""
        spy_single_cache = mocker.spy(self, "single_cache")
        spy_multi_cache = mocker.spy(self, "multi_cache")

        single_cache = region.cache_on_arguments()(self.single_cache)
        multi_cache = region.cache_multi_on_arguments()(self.multi_cache)

        assert 2 == single_cache(1)
        spy_single_cache.assert_called_once_with(1)

        # It's now cached - shouldn't need to call the inner function.
        spy_single_cache.reset_mock()
        assert 2 == single_cache(1)
        assert spy_single_cache.call_count == 0

        assert [6, 8] == multi_cache(3, 4)
        spy_multi_cache.assert_called_once_with(3, 4)

        # Partial hit. Only the "new" key should be passed to the inner function.
        spy_multi_cache.reset_mock()
        assert [6, 10] == multi_cache(3, 5)
        spy_multi_cache.assert_called_once_with(5)

        # Full hit. No call to inner function.
        spy_multi_cache.reset_mock()
        assert [6, 10] == multi_cache(3, 5)
        assert spy_single_cache.call_count == 0


def test_get_or_create_kwarg_only(region):
    """
    When get_or_create is called with only kwargs
        The arguments should be handled correctly
    """
    assert region.get_or_create(key="key", creator=lambda: 3) == 3
    assert region.get_or_create_multi(keys="keys", creator=lambda *args: [1, 2])


@pytest.mark.parametrize(
    "schema_tuples",
    [
        (None, None, DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME, "dogpile.cache"),
        (None, "v0", DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME, "dogpile.cache"),
        (None, "v1", DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME, "dogpile.command"),
        ("mysvc", None, "mysvc", "dogpile.cache"),
        ("mysvc", "v0", "mysvc", "dogpile.cache"),
        ("mysvc", "v1", "mysvc", "dogpile.command"),
    ],
)
def test_schematization(ddtrace_run_python_code_in_subprocess, schema_tuples):
    service_override, schema_version, expected_service, expected_operation = schema_tuples
    code = """
import pytest
import sys

# Import early to avoid circular dependencies
try:
    import dogpile.cache as dogpile_cache
    import dogpile.lock as dogpile_lock
except AttributeError:
    from dogpile import cache as dogpile_cache
    from dogpile import lock as dogpile_lock

# Required fixtures
from tests.contrib.dogpile_cache.test_tracing import tracer
from tests.contrib.dogpile_cache.test_tracing import test_spans
from tests.contrib.dogpile_cache.test_tracing import region
from tests.contrib.dogpile_cache.test_tracing import cleanup
from tests.contrib.dogpile_cache.test_tracing import single_cache

def test(tracer, single_cache, test_spans):
    assert single_cache(1) == 2
    traces = test_spans.pop_traces()
    spans = traces[0]
    span = spans[0]

    assert str(span.service) == "{}"
    assert str(span.name) == "{}"

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """.format(
        expected_service, expected_operation
    )
    env = os.environ.copy()
    if service_override:
        env["DD_SERVICE"] = service_override
    if schema_version:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema_version
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, out.decode()
    assert err == b"", err.decode()
