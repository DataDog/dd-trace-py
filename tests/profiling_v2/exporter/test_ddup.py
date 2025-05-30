import sys

import pytest

from ddtrace.internal.datadog.profiling import ddup


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
        ddup.config()
        ddup.start()
    except Exception as e:
        pytest.fail(str(e))


@pytest.mark.subprocess(
    env=dict(
        DD_TAGS="hello:world",
        DD_PROFILING_TAGS="foo:bar,hello:python",
        # Just to make sure that we don't call other ddup APIs
        DD_PROFILING_STACK_ENABLED="False",
        DD_PROFILING_MEMORY_ENABLED="False",
        DD_PROFILING_LOCK_ENABLED="False",
        DD_PROFILING_EXPORT_LIBDD_ENABLED="True",
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_tags_propagated_when_libdd_enabled",
    ),
    err=None,
)
def test_tags_propagated_when_libdd_enabled():
    import sys
    from unittest.mock import Mock

    sys.modules["ddtrace.internal.datadog.profiling.ddup"] = Mock()

    from ddtrace.profiling.profiler import Profiler  # noqa: I001
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.settings.profiling import config

    # DD_PROFILING_TAGS should override DD_TAGS
    assert config.tags["hello"] == "python"
    assert config.tags["foo"] == "bar"

    # When Profiler is instantiated and libdd is enabled, it should call ddup.config
    p = Profiler()
    p.start()
    p.stop()

    ddup.upload.assert_called()

    tags = ddup.upload.call_args.kwargs["tags"]

    # Profiler could add tags, so check that tags is a superset of config.tags
    for k, v in config.tags.items():
        assert tags[k] == v
