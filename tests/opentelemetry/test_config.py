import pytest


@pytest.mark.subprocess(env={"OTEL_SERVICE_NAME": "blah"})
def test_otel_service_configuration():
    from ddtrace import config

    assert config.service == "blah", config.service


@pytest.mark.subprocess(env={"OTEL_PROPAGATORS": "b3single, tracecontext, b3"})
def test_otel_propagation_style_configuration():
    from ddtrace import config

    assert config._propagation_style_extract == ["b3", "tracecontext"], config._propagation_style_extract


@pytest.mark.subprocess(
    env={"OTEL_PROPAGATORS": "jaegar, tracecontext, b3"}, err=b"Following style not supported by ddtrace: jaegar\n"
)
def test_otel_propagation_style_configuration_unsupportedwarning():
    from ddtrace import config

    assert config._propagation_style_extract == ["tracecontext", "b3"], config._propagation_style_extract


@pytest.mark.subprocess(env={"OTEL_TRACES_SAMPLER": "always_on"})
def test_otel_traces_sampler_configuration_alwayson():
    from ddtrace import config

    assert config._trace_sample_rate == 1.0, config._trace_sample_rate


@pytest.mark.subprocess(env={"OTEL_TRACES_SAMPLER": "always_off"})
def test_otel_traces_sampler_configuration_alwaysoff():
    from ddtrace import config

    assert config._trace_sample_rate == 0.0, config._trace_sample_rate


@pytest.mark.subprocess(env={"OTEL_TRACES_EXPORTER": "none"})
def test_otel_traces_exporter_configuration():
    from ddtrace import config

    assert config._tracing_enabled is False, config._tracing_enabled


@pytest.mark.subprocess(env={"OTEL_TRACES_EXPORTER": "false"}, err=b"An unrecognized exporter 'false' is being used.\n")
def test_otel_traces_exporter_configuration_unsupported_exporter():
    from ddtrace import config

    assert config._tracing_enabled is False, config._tracing_enabled
