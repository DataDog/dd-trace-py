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
            ("env", "my-env"),
            ("global", "global-tag"),
            ("version", "1.5.4"),
        ]
    )


@pytest.mark.subprocess()
def test_tracer_tags_service_from_code():
    """Ensure we collect the expected tags for the TracerTagCollector"""
    import ddtrace
    from ddtrace.filters import TraceFilter
    from ddtrace.internal.runtime import tag_collectors

    class DropFilter(TraceFilter):
        def process_trace(self, _):
            return None

    # Drop all traces so we don't get an error trying to flush
    ddtrace.tracer.configure(settings={"FILTERS": [DropFilter()]})

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
    assert set(values) == set([("service", "my-service"), ("service", "new-service")])
