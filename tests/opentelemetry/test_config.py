import pytest


def _global_sampling_rule():
    from ddtrace._trace.sampling_rule import SamplingRule
    from ddtrace.trace import tracer

    for rule in tracer._sampler.rules:
        if (
            rule.service == SamplingRule.NO_RULE
            and rule.name == SamplingRule.NO_RULE
            and rule.resource == SamplingRule.NO_RULE
            and rule.tags == SamplingRule.NO_RULE
            and rule.provenance == "default"
        ):
            return rule
    assert False, "Rule not found"


@pytest.mark.subprocess(
    env={
        "OTEL_SERVICE_NAME": "Test",
        "DD_SERVICE": "DD_service_test",
        "OTEL_LOG_LEVEL": "debug",
        "DD_TRACE_DEBUG": "False",
        "OTEL_PROPAGATORS": "jaegar, tracecontext, b3",
        "DD_TRACE_PROPAGATION_STYLE": "b3",
        "OTEL_TRACES_SAMPLER": "always_off",
        "DD_TRACE_SAMPLING_RULES": '[{"sample_rate":0.1}]',
        "OTEL_TRACES_EXPORTER": "True",
        "DD_TRACE_ENABLED": "True",
        "OTEL_METRICS_EXPORTER": "none",
        "DD_RUNTIME_METRICS_ENABLED": "True",
        "OTEL_LOGS_EXPORTER": "warning",
        "OTEL_RESOURCE_ATTRIBUTES": "deployment.environment=prod,service.name=bleh,"
        "service.version=1.0,testtag1=random1,testtag2=random2,testtag3=random3,testtag4=random4",
        "DD_TAGS": "env:staging",
        "OTEL_SDK_DISABLED": "True",
        "DD_TRACE_OTEL_ENABLED": "True",
    },
    err=b"Setting OTEL_LOGS_EXPORTER to warning is not supported by ddtrace, this configuration will be ignored.\n",
)
def test_dd_otel_mixed_env_configuration():
    from ddtrace import config
    from tests.opentelemetry.test_config import _global_sampling_rule

    assert config.service == "DD_service_test", config.service
    assert config._debug_mode is False, config._debug_mode
    assert config._propagation_style_extract == ["b3"], config._propagation_style_extract
    assert _global_sampling_rule().sample_rate == 0.1
    assert config._tracing_enabled is True, config._tracing_enabled
    assert config._runtime_metrics_enabled is True, config._runtime_metrics_enabled
    assert config._otel_enabled is True, config._otel_enabled
    assert config.tags == {
        "env": "staging",
    }, config.tags


@pytest.mark.subprocess(
    env={
        "OTEL_SERVICE_NAME": "Test",
        "OTEL_LOG_LEVEL": "debug",
        "OTEL_PROPAGATORS": "jaegar, tracecontext, b3",
        "OTEL_TRACES_SAMPLER": "always_off",
        "DD_TRACE_SAMPLING_RULES": '[{"sample_rate":0.9}]',
        "OTEL_TRACES_EXPORTER": "OTLP",
        "OTEL_METRICS_EXPORTER": "none",
        "OTEL_LOGS_EXPORTER": "warning",
        "OTEL_RESOURCE_ATTRIBUTES": "deployment.environment=prod,service.name=bleh,"
        "service.version=1.0,testtag1=random1,testtag2=random2,testtag3=random3,testtag4=random4",
        "OTEL_SDK_DISABLED": "False",
    },
    err=b"Setting OTEL_LOGS_EXPORTER to warning is not supported by ddtrace, "
    b"this configuration will be ignored.\nFollowing style not supported by ddtrace: jaegar.\n",
)
def test_dd_otel_missing_dd_env_configuration():
    from ddtrace import config
    from tests.opentelemetry.test_config import _global_sampling_rule

    assert config.service == "Test", config.service
    assert config.version == "1.0"
    assert config._otel_enabled is True, config._otel_enabled
    assert config._debug_mode is True, config._debug_mode
    assert config._propagation_style_extract == ["tracecontext", "b3"], config._propagation_style_extract
    assert _global_sampling_rule().sample_rate == 0.9
    assert config._tracing_enabled is True, config._tracing_enabled
    assert config._runtime_metrics_enabled is False, config._runtime_metrics_enabled
    assert config.tags == {
        "env": "prod",
        "testtag1": "random1",
        "testtag2": "random2",
        "testtag3": "random3",
        "testtag4": "random4",
    }, config.tags


@pytest.mark.subprocess(env={"OTEL_SERVICE_NAME": "Test"})
def test_otel_service_configuration():
    from ddtrace import config

    assert config.service == "Test", config.service


@pytest.mark.subprocess(env={"OTEL_LOG_LEVEL": "debug"})
def test_otel_log_level_configuration_debug():
    from ddtrace import config

    assert config._debug_mode is True, config._debug_mode


@pytest.mark.subprocess(
    env={"OTEL_LOG_LEVEL": "trace"},
    err=b"Setting OTEL_LOG_LEVEL to trace is not supported by ddtrace, this configuration will be ignored.\n",
)
def test_otel_log_level_configuration_info():
    from ddtrace import config

    assert config._debug_mode is False, config._debug_mode


@pytest.mark.subprocess(
    env={"OTEL_LOG_LEVEL": "warning"},
    err=b"Setting OTEL_LOG_LEVEL to warning is not supported by ddtrace, this configuration will be ignored.\n",
)
def test_otel_log_level_configuration_unsupported():
    from ddtrace import config

    assert config._debug_mode is False, config._debug_mode


@pytest.mark.subprocess(env={"OTEL_PROPAGATORS": "b3, tracecontext"})
def test_otel_propagation_style_configuration():
    from ddtrace import config

    assert config._propagation_style_extract == ["b3", "tracecontext"], config._propagation_style_extract


@pytest.mark.subprocess(
    env={"OTEL_PROPAGATORS": "jaegar, tracecontext, b3"}, err=b"Following style not supported by ddtrace: jaegar.\n"
)
def test_otel_propagation_style_configuration_unsupportedwarning():
    from ddtrace import config

    assert config._propagation_style_extract == ["tracecontext", "b3"], config._propagation_style_extract


@pytest.mark.subprocess(
    env={"OTEL_TRACES_SAMPLER": "always_on"},
    err=b"Trace sampler set from always_on to parentbased_always_on; only parent based sampling is supported.\n",
)
def test_otel_traces_sampler_configuration_alwayson():
    from tests.opentelemetry.test_config import _global_sampling_rule

    sample_rate = _global_sampling_rule().sample_rate
    assert sample_rate == 1.0, sample_rate


@pytest.mark.subprocess(
    env={"OTEL_TRACES_SAMPLER": "always_on"},
    err=b"Trace sampler set from always_on to parentbased_always_on; only parent based sampling is supported.\n",
)
def test_otel_traces_sampler_configuration_ignore_parent():
    from tests.opentelemetry.test_config import _global_sampling_rule

    sample_rate = _global_sampling_rule().sample_rate
    assert sample_rate == 1.0, sample_rate


@pytest.mark.subprocess(
    env={"OTEL_TRACES_SAMPLER": "always_off"},
    err=b"Trace sampler set from always_off to parentbased_always_off; only parent based sampling is supported.\n",
)
def test_otel_traces_sampler_configuration_alwaysoff():
    from tests.opentelemetry.test_config import _global_sampling_rule

    sample_rate = _global_sampling_rule().sample_rate
    assert sample_rate == 0.0, sample_rate


@pytest.mark.subprocess(
    env={
        "OTEL_TRACES_SAMPLER": "traceidratio",
        "OTEL_TRACES_SAMPLER_ARG": "0.5",
    },
    err=b"Trace sampler set from traceidratio to parentbased_traceidratio; only parent based sampling is supported.\n",
)
def test_otel_traces_sampler_configuration_traceidratio():
    from tests.opentelemetry.test_config import _global_sampling_rule

    sample_rate = _global_sampling_rule().sample_rate
    assert sample_rate == 0.5, sample_rate


@pytest.mark.subprocess(env={"OTEL_TRACES_EXPORTER": "none"})
def test_otel_traces_exporter_configuration():
    from ddtrace import config

    assert config._tracing_enabled is False, config._tracing_enabled


@pytest.mark.subprocess(
    env={"OTEL_TRACES_EXPORTER": "true"},
    err=b"Setting OTEL_TRACES_EXPORTER to true is not supported by ddtrace, this configuration will be ignored.\n",
)
def test_otel_traces_exporter_configuration_unsupported_exporter():
    from ddtrace import config

    assert config._tracing_enabled is True, config._tracing_enabled


@pytest.mark.subprocess(env={"OTEL_METRICS_EXPORTER": "none"})
def test_otel_metrics_exporter_configuration():
    from ddtrace import config

    assert config._runtime_metrics_enabled is False, config._runtime_metrics_enabled


@pytest.mark.subprocess(
    env={"OTEL_METRICS_EXPORTER": "true"},
    err=b"Setting OTEL_METRICS_EXPORTER to true is not supported by ddtrace, this configuration will be ignored.\n",
)
def test_otel_metrics_exporter_configuration_unsupported_exporter():
    from ddtrace import config

    assert config._runtime_metrics_enabled is False, config._runtime_metrics_enabled


@pytest.mark.subprocess(
    env={"OTEL_LOGS_EXPORTER": "console"},
    err=b"Setting OTEL_LOGS_EXPORTER to console is not supported by ddtrace, this configuration will be ignored.\n",
)
def test_otel_logs_exporter_configuration_unsupported():
    from ddtrace import config  # noqa: F401


@pytest.mark.subprocess(env={"OTEL_LOGS_EXPORTER": "none"}, err=b"")
def test_otel_logs_exporter_configuration():
    """
    Testing that a warning is not logged when 'none' value is found.
    """
    from ddtrace import config  # noqa: F401


@pytest.mark.subprocess(
    env={"OTEL_RESOURCE_ATTRIBUTES": "deployment.environment=prod,service.name=bleh,service.version=1.0"}
)
def test_otel_resource_attributes_unified_tags():
    from ddtrace import config

    assert config.service == "bleh"
    assert config.version == "1.0"
    assert config.env == "prod"


@pytest.mark.subprocess(
    env={"OTEL_RESOURCE_ATTRIBUTES": "deployment.environment:prod,service.name:bleh,service.version:1.0"},
    err=b"Setting OTEL_RESOURCE_ATTRIBUTES to deployment.environment:prod,service.name:bleh,service.version:1.0"
    b" is not supported by ddtrace, this configuration will be ignored.\n",
)
def test_otel_resource_attributes_misconfigured_tags():
    from ddtrace import config  # noqa: F401


@pytest.mark.subprocess(
    env={
        "OTEL_RESOURCE_ATTRIBUTES": "deployment.environment=prod,service.name=bleh,"
        "service.version=1.0,testtag1=random1,testtag2=random2,testtag3=random3,testtag4=random4"
    }
)
def test_otel_resource_attributes_mixed_tags():
    from ddtrace import config

    assert config.service == "bleh"
    assert config.version == "1.0"
    assert config.env == "prod"
    assert config.tags == {
        "env": "prod",
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
    err=b"To preserve metrics cardinality, only the following first 10 tags have been processed "
    b"['version:1.0', 'service:bleh', 'env:prod', 'testtag1:random1', 'testtag2:random2', 'testtag3:random3', "
    b"'testtag4:random4', 'testtag5:random5', 'testtag6:random6', 'testtag7:random7']. "
    b"The following tags were not ingested: ['testtag8:random8']\n",
)
def test_otel_resource_attributes_tags_warning():
    from ddtrace import config

    assert config.env == "prod"
    assert config.service == "bleh", config.service
    assert config.version == "1.0"
    assert config.tags == {
        "env": "prod",
        "testtag1": "random1",
        "testtag2": "random2",
        "testtag3": "random3",
        "testtag4": "random4",
        "testtag5": "random5",
        "testtag6": "random6",
        "testtag7": "random7",
    }, config.tags


@pytest.mark.subprocess(env={"OTEL_SDK_DISABLED": "false", "DD_TRACE_OTEL_ENABLED": None})
def test_otel_sdk_disabled_configuration():
    from ddtrace import config

    assert config._otel_enabled is True


@pytest.mark.subprocess(env={"OTEL_SDK_DISABLED": "true", "DD_TRACE_OTEL_ENABLED": None})
def test_otel_sdk_disabled_configuration_true():
    from ddtrace import config

    assert config._otel_enabled is False, config._otel_enabled


@pytest.mark.subprocess(
    env={"OTEL_RESOURCE_ATTRIBUTES": "service.version=1.0"},
)
def test_otel_resource_attributes_version_tag():
    from ddtrace import config

    assert config.version == "1.0"
