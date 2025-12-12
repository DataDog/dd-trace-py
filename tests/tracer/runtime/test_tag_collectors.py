import pytest

from ddtrace.internal.runtime import tag_collectors


def test_values():
    ptc = tag_collectors.PlatformTagCollector()
    values = dict(ptc.collect())
    assert set(["lang", "lang_interpreter", "lang_version", "tracer_version"]) == set(values.keys())


@pytest.mark.subprocess(
    env=dict(
        DD_TAGS="key_1:from_env,key_2:from_env",
        DD_ENV="test-env",
        DD_SERVICE="test-service",
        DD_VERSION="2.5.4",
    )
)
def test_tracer_tags():
    """Ensure we collect the expected tags for the TracerTagCollector"""
    import ddtrace
    from ddtrace.internal.runtime import tag_collectors

    ddtrace.tracer.set_tags({"global": "tag"})

    ttc = tag_collectors.TracerTagCollector()
    values = ttc.collect()

    assert values is not None
    assert set(values) == set(
        [
            ("service", "test-service"),
            ("key_1", "from_env"),
            ("key_2", "from_env"),
            ("env", "test-env"),
            ("global", "tag"),
            ("version", "2.5.4"),
        ]
    )


@pytest.mark.subprocess()
def test_tracer_tags_config():
    """Ensure we collect the expected tags for the TracerTagCollector"""
    import ddtrace
    from ddtrace.internal.runtime import tag_collectors
    from tests.conftest import DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME

    # DEV: setting `config.tags` does not work, they get copied to `tracer._tags`
    #      when the tracer is created
    # ddtrace.config.tags["key_1"] = "from_code"
    # ddtrace.config.tags["key_2"] = "from_code"
    # DEV: setting `config.service` does not work, since it gets added to `tracer._services`
    #      when the tracer is created. we'd need a span created to pick it up
    # ddtrace.config.service = "my-service"

    ddtrace.config.version = "1.5.4"
    ddtrace.config.env = "my-env"
    ddtrace.tracer.set_tags({"global": "global-tag"})

    ttc = tag_collectors.TracerTagCollector()
    values = ttc.collect()

    assert values is not None
    assert set(values) == set(
        [
            ("service", DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME),
            ("env", "my-env"),
            ("version", "1.5.4"),
            ("global", "global-tag"),
        ]
    )


@pytest.mark.subprocess()
def test_tracer_tags_service_from_code():
    """Ensure we collect the expected tags for the TracerTagCollector"""
    import ddtrace
    from ddtrace.internal.runtime import tag_collectors
    from ddtrace.trace import TraceFilter

    class DropFilter(TraceFilter):
        def process_trace(self, _):
            return None

    # Drop all traces so we don't get an error trying to flush
    ddtrace.tracer.configure(trace_processors=[DropFilter()])

    ddtrace.config.service = "my-service"

    # create a trace using the default service name
    with ddtrace.tracer.trace("root_span"):
        pass

    # create a trace with an explicit service name
    with ddtrace.tracer.trace("root_span", service="new-service"):
        pass

    ttc = tag_collectors.TracerTagCollector()
    values = ttc.collect()

    assert values is not None
    assert values == [
        ("service", "my-service"),
    ], values


def test_process_tags_disabled_by_default():
    ptc = tag_collectors.ProcessTagCollector()
    tags = list(ptc.collect())
    assert len(tags) == 0, "Process tags should be empty when not enabled"


@pytest.mark.subprocess(env={"DD_EXPERIMENTAL_PROPAGATE_PROCESS_TAGS_ENABLED": "true"})
def test_process_tags_enabled():
    from unittest.mock import patch

    from ddtrace.internal.process_tags import ENTRYPOINT_BASEDIR_TAG
    from ddtrace.internal.process_tags import ENTRYPOINT_NAME_TAG
    from ddtrace.internal.process_tags import ENTRYPOINT_TYPE_TAG
    from ddtrace.internal.process_tags import ENTRYPOINT_WORKDIR_TAG
    from ddtrace.internal.runtime import tag_collectors
    from tests.utils import process_tag_reload

    with patch("sys.argv", ["/path/to/test_script.py"]), patch("os.getcwd", return_value="/path/to/workdir"):
        process_tag_reload()

        ptc = tag_collectors.ProcessTagCollector()
        tags: list[str] = ptc.collect()
        assert len(tags) == 4, f"Expected 4 process tags, got {len(tags)}: {tags}"

        tags_dict = {k: v for k, v in (s.split(":") for s in tags)}
        assert ENTRYPOINT_NAME_TAG in tags_dict
        assert ENTRYPOINT_WORKDIR_TAG in tags_dict
        assert ENTRYPOINT_BASEDIR_TAG in tags_dict
        assert ENTRYPOINT_TYPE_TAG in tags_dict

        assert tags_dict[ENTRYPOINT_NAME_TAG] == "test_script"
        assert tags_dict[ENTRYPOINT_WORKDIR_TAG] == "workdir"
        assert tags_dict[ENTRYPOINT_BASEDIR_TAG] == "to"
        assert tags_dict[ENTRYPOINT_TYPE_TAG] == "script"


@pytest.mark.subprocess(env={"DD_EXPERIMENTAL_PROPAGATE_PROCESS_TAGS_ENABLED": "true"})
def test_process_tag_class():
    from typing import List

    from ddtrace.internal.runtime.runtime_metrics import ProcessTags

    process_tags: List[str] = list(ProcessTags())
    assert len(process_tags) >= 4
