import mock
import pytest

import ddtrace
from ddtrace.constants import AUTO_KEEP
from ddtrace.ext import ci
from ddtrace.internal.ci_visibility import CIVisibility
from ddtrace.internal.ci_visibility.filters import TraceCiVisibilityFilter
from ddtrace.internal.ci_visibility.recorder import _extract_repository_name_from_url
from ddtrace.span import Span
from tests.utils import DummyCIVisibilityWriter
from tests.utils import DummyTracer
from tests.utils import override_env


def test_filters_test_spans():
    trace_filter = TraceCiVisibilityFilter(tags={"hello": "world"}, service="test-service")
    root_test_span = Span(name="span1", span_type="test")
    root_test_span._local_root = root_test_span
    # Root span in trace is a test
    trace = [root_test_span]
    assert trace_filter.process_trace(trace) == trace
    assert root_test_span.service == "test-service"
    assert root_test_span.get_tag(ci.LIBRARY_VERSION) == ddtrace.__version__
    assert root_test_span.get_tag("hello") == "world"
    assert root_test_span.context.dd_origin == ci.CI_APP_TEST_ORIGIN
    assert root_test_span.context.sampling_priority == AUTO_KEEP


def test_filters_non_test_spans():
    trace_filter = TraceCiVisibilityFilter(tags={"hello": "world"}, service="test-service")
    root_span = Span(name="span1")
    root_span._local_root = root_span
    # Root span in trace is not a test
    trace = [root_span]
    assert trace_filter.process_trace(trace) is None


def test_ci_visibility_service_enable():
    with override_env(dict(DD_API_KEY="foobar.baz")):
        dummy_tracer = DummyTracer()
        dummy_tracer.configure(writer=DummyCIVisibilityWriter("https://citestcycle-intake.banana"))
        CIVisibility.enable(tracer=dummy_tracer, service="test-service")
        ci_visibility_instance = CIVisibility._instance
        assert ci_visibility_instance is not None
        assert CIVisibility.enabled
        assert ci_visibility_instance.tracer == dummy_tracer
        assert ci_visibility_instance._service == "test-service"
        assert any(isinstance(tracer_filter, TraceCiVisibilityFilter) for tracer_filter in dummy_tracer._filters)
        CIVisibility.disable()


def test_ci_visibility_service_disable():
    with override_env(dict(DD_API_KEY="foobar.baz")):
        dummy_tracer = DummyTracer()
        dummy_tracer.configure(writer=DummyCIVisibilityWriter("https://citestcycle-intake.banana"))
        CIVisibility.enable(tracer=dummy_tracer, service="test-service")
        CIVisibility.disable()
        ci_visibility_instance = CIVisibility._instance
        assert ci_visibility_instance is None
        assert not CIVisibility.enabled


@pytest.mark.parametrize(
    "repository_url,repository_name",
    [
        ("https://github.com/DataDog/dd-trace-py.git", "dd-trace-py"),
        ("https://github.com/DataDog/dd-trace-py", "dd-trace-py"),
        ("git@github.com:DataDog/dd-trace-py.git", "dd-trace-py"),
        ("git@github.com:DataDog/dd-trace-py", "dd-trace-py"),
        ("dd-trace-py", "dd-trace-py"),
        ("git@hostname.com:org/repo-name.git", "repo-name"),
        ("git@hostname.com:org/repo-name", "repo-name"),
        ("ssh://git@hostname.com:org/repo-name", "repo-name"),
        ("git+git://github.com/org/repo-name.git", "repo-name"),
        ("git+ssh://github.com/org/repo-name.git", "repo-name"),
        ("git+https://github.com/org/repo-name.git", "repo-name"),
    ],
)
def test_repository_name_extracted(repository_url, repository_name):
    assert _extract_repository_name_from_url(repository_url) == repository_name


def test_repository_name_not_extracted_warning():
    """If provided an invalid repository url, should raise warning and return original repository url"""
    repository_url = "https://github.com:organ[ization/repository-name"
    with mock.patch("ddtrace.internal.ci_visibility.recorder.log") as mock_log:
        extracted_repository_name = _extract_repository_name_from_url(repository_url)
        assert extracted_repository_name == repository_url
    mock_log.warning.assert_called_once_with("Repository name cannot be parsed from repository_url: %s", repository_url)
