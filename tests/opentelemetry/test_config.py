import pytest




@pytest.mark.subprocess(
    env={
        "OTEL_SERVICE_NAME": "Test",
        "DD_SERVICE": "DD_service_test",
        "OTEL_LOG_LEVEL": "debug",
        "DD_TRACE_DEBUG": "False",
        "OTEL_PROPAGATORS": "jaegar, tracecontext, b3",
        "DD_TRACE_PROPAGATION_STYLE": "b3",
        "OTEL_TRACES_SAMPLER": "always_off",
        "DD_TRACE_SAMPLE_RATE": "1.0",
        "OTEL_TRACES_EXPORTER": "True",
        "DD_TRACE_ENABLED": "True",
        "OTEL_METRICS_EXPORTER": "none",
        "DD_RUNTIME_METRICS_ENABLED": "True",
        "OTEL_LOGS_EXPORTER": "warning",
        "OTEL_RESOURCE_ATTRIBUTES": "deployment.environment=prod,service.name=bleh,"
        "service.version=1.0,testtag1=random1,testtag2=random2,testtag3=random3,testtag4=random4",
        "DD_TAGS": "DD_ENV:staging",
        "OTEL_SDK_DISABLED": "True",
        "DD_TRACE_OTEL_ENABLED": "True",
    },
    err=b"Unsupported logs exporter detected.\n",
)
def test_dd_otel_mixxed_env_configuration():
    from ddtrace import config

    assert config.service == "DD_service_test", config.service
    assert config._debug_mode is False, config._debug_mode
    assert config._propagation_style_extract == ["b3"], config._propagation_style_extract
    assert config._trace_sample_rate == 1.0, config._trace_sample_rate
    assert config._tracing_enabled is True, config._tracing_enabled
    assert config._runtime_metrics_enabled is True, config._runtime_metrics_enabled
    assert config.tags == {
        "DD_ENV": "staging",
    }, config.tags
    assert config._otel_enabled is True, config._otel_enabled


# Passing a mix of DD variables and OTEL Variables
@pytest.mark.subprocess(
    env={
        "OTEL_SERVICE_NAME": "Test",
        "DD_SERVICE": "",
        "OTEL_LOG_LEVEL": "debug",
        "DD_TRACE_DEBUG": "False",
        "OTEL_PROPAGATORS": "jaegar, tracecontext, b3",
        "DD_TRACE_PROPAGATION_STYLE": "",
        "OTEL_TRACES_SAMPLER": "always_off",
        "DD_TRACE_SAMPLE_RATE": "1.0",
        "OTEL_TRACES_EXPORTER": "True",
        "DD_TRACE_ENABLED": "True",
        "OTEL_METRICS_EXPORTER": "none",
        "DD_RUNTIME_METRICS_ENABLED": "True",
        "OTEL_LOGS_EXPORTER": "warning",
        "OTEL_RESOURCE_ATTRIBUTES": "deployment.environment=prod,service.name=bleh,"
        "service.version=1.0,testtag1=random1,testtag2=random2,testtag3=random3,testtag4=random4",
        "DD_TAGS": "",
        "OTEL_SDK_DISABLED": "False",
        "DD_TRACE_OTEL_ENABLED": "",
    },
    err=b"Following style not supported by ddtrace: jaegar.\n" b"Unsupported logs exporter detected.\n",
)
def test_dd_otel_missing_dd_env_configuration():
    from ddtrace import config

    assert config.service == "Test", config.service
    assert config._debug_mode is False, config._debug_mode
    assert config._propagation_style_extract == ["tracecontext", "b3"], config._propagation_style_extract
    assert config._trace_sample_rate == 1.0, config._trace_sample_rate
    assert config._tracing_enabled is True, config._tracing_enabled
    assert config._runtime_metrics_enabled is True, config._runtime_metrics_enabled
    assert config.tags == {
        "DD_ENV": "prod",
        "DD_SERVICE": "bleh",
        "DD_VERSION": "1.0",
        "testtag1": "random1",
        "testtag2": "random2",
        "testtag3": "random3",
        "testtag4": "random4",
    }, config.tags
    assert config._otel_enabled is True, config._otel_enabled


# OTEL_SERVICE_NAME -> DD_SERVICE Tests
@pytest.mark.subprocess(env={"OTEL_SERVICE_NAME": "Test"})
def test_otel_service_configuration():
    from ddtrace import config

    assert config.service == "Test", config.service


# OTEL_LOG_LEVEL -> DD_TRACE_DEBUG
@pytest.mark.subprocess(env={"OTEL_LOG_LEVEL": "debug"})
def test_otel_log_level_configuration_debug():
    from ddtrace import config

    assert config._debug_mode is True, config._debug_mode


@pytest.mark.subprocess(env={"OTEL_LOG_LEVEL": "info"})
def test_otel_log_level_configuration_info():
    from ddtrace import config

    assert config._debug_mode is False, config._debug_mode


@pytest.mark.subprocess(
    env={"OTEL_LOG_LEVEL": "warning"},
    err=b"ddtrace does not support otel log level 'warning'. setting ddtrace to log level info.\n",
)
def test_otel_log_level_configuration_unsupported():
    from ddtrace import config

    assert config._debug_mode is False, config._debug_mode


# OTEL_PROPAGATORS -> DD_TRACE_PROPAGATION_STYLE Tests
@pytest.mark.subprocess(env={"OTEL_PROPAGATORS": "b3single, tracecontext, b3"})
def test_otel_propagation_style_configuration():
    from ddtrace import config

    assert config._propagation_style_extract == ["b3", "tracecontext"], config._propagation_style_extract


@pytest.mark.subprocess(
    env={"OTEL_PROPAGATORS": "jaegar, tracecontext, b3"}, err=b"Following style not supported by ddtrace: jaegar.\n"
)
def test_otel_propagation_style_configuration_unsupportedwarning():
    from ddtrace import config

    assert config._propagation_style_extract == ["tracecontext", "b3"], config._propagation_style_extract


# OTEL_TRACES_SAMPLER -> DD_TRACE_SAMPLE_RATE Tests
@pytest.mark.subprocess(env={"OTEL_TRACES_SAMPLER": "always_on"})
def test_otel_traces_sampler_configuration_alwayson():
    from ddtrace import config

    assert config._trace_sample_rate == 1.0, config._trace_sample_rate


@pytest.mark.subprocess(env={"OTEL_TRACES_SAMPLER": "always_off"})
def test_otel_traces_sampler_configuration_alwaysoff():
    from ddtrace import config

    assert config._trace_sample_rate == 0.0, config._trace_sample_rate


# Trace sampler arg value should be picked up correctly here
@pytest.mark.subprocess(
    env={
        "OTEL_TRACES_SAMPLER": "traceidratio",
        "OTEL_TRACES_SAMPLER_ARG": "0.5",
    }
)
def test_otel_traces_sampler_configuration_traceidratio():
    from ddtrace import config

    assert config._trace_sample_rate == 0.5, config._trace_sample_rate


# OTEL_TRACES_EXPORTER -> DD_TRACE_ENABLED Tests
@pytest.mark.subprocess(env={"OTEL_TRACES_EXPORTER": "none"})
def test_otel_traces_exporter_configuration():
    from ddtrace import config

    assert config._tracing_enabled is True, config._tracing_enabled


@pytest.mark.subprocess(
    env={"OTEL_TRACES_EXPORTER": "true"},
    err=b"An unrecognized trace exporter 'true' is being used; setting dd_trace_enabled to false.\n",
)
def test_otel_traces_exporter_configuration_unsupported_exporter():
    from ddtrace import config

    assert config._tracing_enabled is False, config._tracing_enabled


# OTEL_METRICS_EXPORTER -> DD_RUNTIME_METRICS_ENABLED Tests
@pytest.mark.subprocess(env={"OTEL_METRICS_EXPORTER": "none"})
def test_otel_metrics_exporter_configuration():
    from ddtrace import config

    assert config._runtime_metrics_enabled is False, config._runtime_metrics_enabled


@pytest.mark.subprocess(
    env={"OTEL_METRICS_EXPORTER": "true"},
    err=b"An unrecognized runtime metrics exporter 'true' is being used;"
    b" setting dd_runtime_metrics_enabled to false.\n",
)
def test_otel_metrics_exporter_configuration_unsupported_exporter():
    from ddtrace import config

    assert config._runtime_metrics_enabled is False, config._runtime_metrics_enabled


# OTEL_LOGS_EXPORTER Test (Should be ignored and print a warning if not 'none'.)
@pytest.mark.subprocess(env={"otel_LOGS_EXPORTER": "true"}, err=b"Unsupported logs exporter detected.\n")
def test_otel_logs_exporter_configuration_unsupported():
    from ddtrace import config  # noqa: F401


@pytest.mark.subprocess(env={"OTEL_LOGS_EXPORTER": "none"}, err=b"")
def test_otel_logs_exporter_configuration():
    """
    Testing that a warning is not logged when 'none' value is found.
    """
    from ddtrace import config  # noqa: F401


# OTEL_RESOURCE ATTRIBUTES -> DD_TAGS Tests
@pytest.mark.subprocess(
    env={"OTEL_RESOURCE_ATTRIBUTES": "deployment.environment=prod,service.name=bleh,service.version=1.0"}
)
def test_otel_resource_attributes_unified_tags():
    from ddtrace import config

    assert config.tags == {
        "DD_ENV": "prod",
        "DD_SERVICE": "bleh",
        "DD_VERSION": "1.0",
    }, config.tags


@pytest.mark.subprocess(
    env={
        "OTEL_RESOURCE_ATTRIBUTES": "deployment.environment=prod,service.name=bleh,"
        "service.version=1.0,testtag1=random1,testtag2=random2,testtag3=random3,testtag4=random4"
    }
)
def test_otel_resource_attributes_mixed_tags():
    from ddtrace import config

    assert config.tags == {
        "DD_ENV": "prod",
        "DD_SERVICE": "bleh",
        "DD_VERSION": "1.0",
        "testtag1": "random1",
        "testtag2": "random2",
        "testtag3": "random3",
        "testtag4": "random4",
    }, config.tags


@pytest.mark.subprocess(
    env={
        "OTEL_RESOURCE_ATTRIBUTES": "deployment.environment=prod,service.name=bleh,"
        "service.version=1.0,testtag1=random1,testtag2=random2,testtag3=random3,testtag4=random4,testtag5=random5,"
        "testtag6=random6,testtag7=random7,testtag8=random8"
    },
    err=b"To preserve metrics cardinality, only the following first 10"
    b" tags have been processed ['DD_ENV:prod', 'DD_SERVICE:bleh', 'DD_VERSION:1.0', 'testtag1:random1', "
    b"'testtag2:random2', 'testtag3:random3', 'testtag4:random4', 'testtag5:random5', 'testtag6:random6', "
    b"'testtag7:random7']\n",
)
def test_otel_resource_attributes_tags_warning():
    from ddtrace import config

    assert config.tags == {
        "DD_ENV": "prod",
        "DD_SERVICE": "bleh",
        "DD_VERSION": "1.0",
        "testtag1": "random1",
        "testtag2": "random2",
        "testtag3": "random3",
        "testtag4": "random4",
        "testtag5": "random5",
        "testtag6": "random6",
        "testtag7": "random7",
        "testtag8": "random8",
    }, config.tags


# OTEL_SDK_DISABLED -> !DD_TRACE_OTEL_ENABLED Test
@pytest.mark.subprocess(env={"OTEL_SDK_DISABLED": "false"})
def test_otel_sdk_disabled_configuration():
    from ddtrace import config

    assert config._otel_enabled is True


@pytest.mark.subprocess(env={"OTEL_SDK_DISABLED": "true"})
def test_otel_sdk_disabled_configuration_true():
    from ddtrace import config

    assert config._otel_enabled is False
