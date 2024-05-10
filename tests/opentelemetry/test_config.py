import pytest


@pytest.mark.subprocess(env={"OTEL_SERVICE_NAME": "blah"})
def test_otel_service_configuration():
    from ddtrace import config

    assert config.service == "blah", config.service


@pytest.mark.subprocess(env={"OTEL_PROPAGATORS": "b3single, tracecontext, b3"})
def test_otel_propagation_style_configuration():
    from ddtrace import config

    assert config._propagation_style_extract == ["tracecontext", "b3"], config._propagation_style_extract
