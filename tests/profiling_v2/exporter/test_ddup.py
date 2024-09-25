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
        ddup.config(
            env="my_env",
            service="my_service",
            version="my_version",
            tags={},
            url="http://localhost:8126",
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
    Profiler()

    ddup.config.assert_called()

    if sys.version_info >= (3, 8):
        tags = ddup.config.call_args.kwargs["tags"]
    else:
        # Until Python 3.7, call_args didn't have kwargs attribute
        tags = ddup.config.call_args[1]["tags"]

    # Profiler could add tags, so check that tags is a superset of config.tags
    for k, v in config.tags.items():
        assert tags[k] == v
