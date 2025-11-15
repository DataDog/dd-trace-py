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


@pytest.mark.subprocess()
def test_process_tags_deactivated():
    import sys
    from unittest import mock

    sys.modules["ddtrace.internal.datadog.profiling.ddup"] = mock.Mock()

    from ddtrace.profiling.profiler import Profiler  # noqa: I001
    from ddtrace.internal.datadog.profiling import ddup

    # When Profiler is instantiated and libdd is enabled, it should call ddup.config
    Profiler()

    ddup.config.assert_called()

    tags = ddup.config.call_args.kwargs["tags"]

    assert "process_tags" not in tags


@pytest.mark.subprocess(
    env=dict(
        DD_EXPERIMENTAL_PROPAGATE_PROCESS_TAGS_ENABLED="true",
    )
)
def test_process_tags_activated():
    import sys
    from unittest import mock

    sys.modules["ddtrace.internal.datadog.profiling.ddup"] = mock.Mock()

    from ddtrace.profiling.profiler import Profiler  # noqa: I001
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.internal.process_tags import _process_tag_reload

    from ddtrace.internal.process_tags.constants import ENTRYPOINT_BASEDIR_TAG
    from ddtrace.internal.process_tags.constants import ENTRYPOINT_NAME_TAG
    from ddtrace.internal.process_tags.constants import ENTRYPOINT_TYPE_SCRIPT
    from ddtrace.internal.process_tags.constants import ENTRYPOINT_TYPE_TAG
    from ddtrace.internal.process_tags.constants import ENTRYPOINT_WORKDIR_TAG

    with mock.patch("sys.argv", ["/path/to/test_script.py"]), mock.patch("os.getcwd", return_value="/path/to/workdir"):
        _process_tag_reload()

        # When Profiler is instantiated and libdd is enabled, it should call ddup.config
        Profiler()

        ddup.config.assert_called()

        tags = ddup.config.call_args.kwargs["tags"]

        assert "process_tags" in tags
        process_tags = dict(tag.split(":", 1) for tag in tags["process_tags"].split(","))

        assert process_tags[ENTRYPOINT_BASEDIR_TAG] == "to"
        assert process_tags[ENTRYPOINT_NAME_TAG] == "test_script"
        assert process_tags[ENTRYPOINT_TYPE_TAG] == ENTRYPOINT_TYPE_SCRIPT
        assert process_tags[ENTRYPOINT_WORKDIR_TAG] == "workdir"
