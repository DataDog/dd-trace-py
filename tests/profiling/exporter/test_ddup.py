import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_EXPORT_LIBDD_ENABLED="true",
        # this is necessary to force enable libdd exporter
        DD_PROFILING__FORCE_LEGACY_EXPORTER="false",
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
    assert "python" == config.tags["hello"]
    assert "bar" == config.tags["foo"]

    Profiler()

    tags = ddup.config.call_args.kwargs["tags"]

    for k, v in config.tags.items():
        assert k in tags
        assert tags[k] == v
