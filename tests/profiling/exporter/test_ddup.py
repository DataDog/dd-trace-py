import sys

import pytest

from ddtrace.internal.datadog.profiling import ddup


class MockSpan:
    """Mock span object for testing"""

    def __init__(self, span_id=None, local_root=None):
        if span_id is not None:
            self.span_id = span_id
        if local_root is not None:
            self._local_root = local_root


class MockLocalRoot:
    """Mock local root span object for testing"""

    def __init__(self, span_id=None, span_type=None):
        if span_id is not None:
            self.span_id = span_id
        if span_type is not None:
            self.span_type = span_type


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_libdd_available():
    """
    Tests that the libdd module can be loaded
    """

    assert ddup.is_available


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_ddup_start():
    """
    Tests that the the libdatadog exporter can be enabled
    """

    try:
        ddup.config(
            env="my_env",
            service="my_service",
            version="my_version",
            tags={},
        )
        ddup.start()
    except Exception as e:
        pytest.fail(str(e))


@pytest.mark.subprocess(
    env=dict(
        DD_TAGS="hello:world",
        DD_PROFILING_TAGS="foo:bar,hello:python",
    )
)
def test_tags_propagated():
    import sys
    from unittest.mock import Mock

    sys.modules["ddtrace.internal.datadog.profiling.ddup"] = Mock()

    from ddtrace.profiling.profiler import Profiler  # noqa: I001
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.internal.settings.profiling import config

    # DD_PROFILING_TAGS should override DD_TAGS
    assert config.tags["hello"] == "python"
    assert config.tags["foo"] == "bar"

    # When Profiler is instantiated and libdd is enabled, it should call ddup.config
    Profiler()

    ddup.config.assert_called()

    tags = ddup.config.call_args.kwargs["tags"]

    # Profiler could add tags, so check that tags is a superset of config.tags
    for k, v in config.tags.items():
        assert tags[k] == v


@pytest.mark.skipif(not ddup.is_available, reason="ddup not available")
def test_push_span_without_span_id():
    """
    Test that push_span handles span objects without span_id attribute gracefully.
    This can happen when profiling collector encounters mock span objects in tests.
    Regression test for issue where AttributeError was raised when accessing span.span_id.
    """

    # Create a sample handle
    handle = ddup.SampleHandle()

    # Test 1: Span without span_id attribute
    span_no_id = MockSpan()
    # Should not raise AttributeError
    handle.push_span(span_no_id)

    # Test 2: Span without _local_root attribute
    span_no_local_root = MockSpan(span_id=12345)
    # Should not raise AttributeError
    handle.push_span(span_no_local_root)

    # Test 3: Span with _local_root but local_root without span_id
    local_root_no_id = MockLocalRoot()
    span_with_incomplete_root = MockSpan(span_id=12345, local_root=local_root_no_id)
    # Should not raise AttributeError
    handle.push_span(span_with_incomplete_root)

    # Test 4: Span with _local_root but local_root without span_type
    local_root_no_type = MockLocalRoot(span_id=67890)
    span_with_root_no_type = MockSpan(span_id=12345, local_root=local_root_no_type)
    # Should not raise AttributeError
    handle.push_span(span_with_root_no_type)

    # Test 5: Complete span (should work as before)
    complete_local_root = MockLocalRoot(span_id=67890, span_type="web")
    complete_span = MockSpan(span_id=12345, local_root=complete_local_root)
    # Should not raise AttributeError
    handle.push_span(complete_span)

    # Test 6: None span (should handle gracefully)
    handle.push_span(None)
