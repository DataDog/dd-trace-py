import pytest


def _global_sampling_rule():
    from ddtrace.trace import tracer

    for rule in tracer._sampler.rules:
        if (
            rule.service is None
            and rule.name is None
            and rule.resource is None
            and not rule.tags
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
    err=b"Setting OTEL_LOGS_EXPORTER to warning is not supported by ddtrace, this configuration "
    b"will be ignored.\nTrace sampler set from always_off to parentbased_always_off; only parent based "
    b"sampling is supported.\nFollowing style not supported by ddtrace: jaegar.\n",
)
def test_dd_otel_mixed_env_configuration():
    from ddtrace import config
    from tests.opentelemetry.test_config import _global_sampling_rule

    assert config.service == "DD_service_test", config.service
    assert config._debug_mode is False, config._debug_mode
    assert config._propagation_style_extract == ["b3"], config._propagation_style_extract
    assert _global_sampling_rule().sample_rate == 0.1
    assert config._tracing_enabled is True, config._tracing_enabled
    assert config._runtime_metrics_enabled is False, config._runtime_metrics_enabled
    assert config._otel_trace_enabled is True, config._otel_trace_enabled
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
    err=b"Setting OTEL_LOGS_EXPORTER to warning is not supported by ddtrace, this configuration will be ignored.\n"
    b"Trace sampler set from always_off to parentbased_always_off; only parent based sampling is supported.\n"
    b"Following style not supported by ddtrace: jaegar.\n",
)
def test_dd_otel_missing_dd_env_configuration():
    from ddtrace import config
    from tests.opentelemetry.test_config import _global_sampling_rule

    assert config.service == "Test", config.service
    assert config.version == "1.0"
    assert config._otel_trace_enabled is True, config._otel_trace_enabled
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


@pytest.mark.subprocess(env={"DD_METRICS_OTEL_ENABLED": "True", "OTEL_METRICS_EXPORTER": "none"})
def test_otel_metrics_exporter_configuration_none():
    from ddtrace import config

    assert config._runtime_metrics_enabled is False, config._runtime_metrics_enabled


@pytest.mark.subprocess(env={"DD_METRICS_OTEL_ENABLED": "True", "OTEL_METRICS_EXPORTER": "otlp"})
def test_otel_metrics_exporter_configuration_otlp():
    from ddtrace import config

    assert config._runtime_metrics_enabled is False, config._runtime_metrics_enabled


@pytest.mark.subprocess(
    env={"DD_METRICS_OTEL_ENABLED": "True", "OTEL_METRICS_EXPORTER": "true"},
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
    env={"OTEL_RESOURCE_ATTRIBUTES": "deployment.environment.name=prod,service.name=bleh,service.version=1.0"}
)
def test_otel_resource_attributes_unified_tags():
    from ddtrace import config

    assert config.service == "bleh"
    assert config.version == "1.0"
    assert config.env == "prod"


@pytest.mark.subprocess(
    env={"OTEL_RESOURCE_ATTRIBUTES": "deployment.environment=legacy,deployment.environment.name=stable"}
)
def test_otel_resource_attributes_prefer_stable_environment():
    from ddtrace import config

    assert config.env == "stable"


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

    assert config._otel_trace_enabled is True


@pytest.mark.subprocess(env={"OTEL_SDK_DISABLED": "true", "DD_TRACE_OTEL_ENABLED": None})
def test_otel_sdk_disabled_configuration_true():
    from ddtrace import config

    assert config._otel_trace_enabled is False, config._otel_trace_enabled


@pytest.mark.subprocess(
    env={"OTEL_RESOURCE_ATTRIBUTES": "service.version=1.0"},
)
def test_otel_resource_attributes_version_tag():
    from ddtrace import config

    assert config.version == "1.0"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", []),
        ("key=value", [("key", "value")]),
        ("k1=v1,k2=v2,k3=v3", [("k1", "v1"), ("k2", "v2"), ("k3", "v3")]),
        ("  k1 = v1 ,  k2 = v2 ", [("k1", "v1"), ("k2", "v2")]),
        ("invalid,key=value", [("key", "value")]),
        ("key=val=extra", [("key", "val=extra")]),
    ],
)
def test_parse_otlp_headers(raw, expected):
    from ddtrace.internal.writer.writer import NativeWriter

    assert NativeWriter._parse_otlp_headers(raw) == expected


@pytest.mark.subprocess()
def test_trace_metrics_endpoint_defaults_to_http_json():
    """The native trace-metrics exporter is HTTP/JSON only, so its endpoint must resolve to an
    HTTP ``/v1/metrics`` endpoint even when the (default) metrics protocol is gRPC. Otherwise the
    auto-enable path would post JSON metrics to the gRPC endpoint (``:4317`` with no path).
    """
    from ddtrace.internal.settings._opentelemetry import otel_config

    endpoint = otel_config.exporter.TRACE_METRICS_ENDPOINT
    assert endpoint.startswith("http://")
    assert endpoint.endswith(":4318/v1/metrics"), endpoint


@pytest.mark.subprocess(env={"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"})
def test_trace_metrics_endpoint_appends_path_to_global_endpoint():
    from ddtrace.internal.settings._opentelemetry import otel_config

    assert otel_config.exporter.TRACE_METRICS_ENDPOINT == "http://collector:4318/v1/metrics"


@pytest.mark.subprocess(env={"OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "http://collector:9999/custom/path"})
def test_trace_metrics_endpoint_uses_signal_specific_endpoint_as_is():
    from ddtrace.internal.settings._opentelemetry import otel_config

    assert otel_config.exporter.TRACE_METRICS_ENDPOINT == "http://collector:9999/custom/path"


@pytest.mark.subprocess(env={"DD_AGENTLESS_ENABLED": "true", "DD_API_KEY": "foobarkey"})
def test_otlp_metrics_and_logs_target_the_intake_when_agentless():
    """Agentless has no Agent OTLP receiver to export to, so the intake takes its place."""
    from ddtrace.internal.settings._opentelemetry import otel_config

    exporter = otel_config.exporter
    assert exporter.METRICS_ENDPOINT == "https://otlp.datadoghq.com/v1/metrics"
    assert exporter.LOGS_ENDPOINT == "https://otlp.datadoghq.com/v1/logs"
    # The intake is HTTPS-only, so the gRPC default cannot stand.
    assert exporter.METRICS_PROTOCOL == "http/protobuf"
    assert exporter.LOGS_PROTOCOL == "http/protobuf"


@pytest.mark.subprocess(env={"DD_AGENTLESS_ENABLED": "true", "DD_API_KEY": "foobarkey", "DD_SITE": "datadoghq.eu"})
def test_otlp_intake_follows_dd_site():
    from ddtrace.internal.settings._opentelemetry import otel_config

    assert otel_config.exporter.METRICS_ENDPOINT == "https://otlp.datadoghq.eu/v1/metrics"


@pytest.mark.subprocess(env={"DD_API_KEY": "foobarkey"})
def test_otlp_metrics_and_logs_target_the_agent_without_agentless():
    """An API key alone must not divert OTLP export away from the Agent."""
    from ddtrace.internal.settings._opentelemetry import otel_config

    exporter = otel_config.exporter
    assert "otlp.datadoghq.com" not in exporter.METRICS_ENDPOINT
    assert "otlp.datadoghq.com" not in exporter.LOGS_ENDPOINT
    assert exporter.METRICS_PROTOCOL == "grpc"


@pytest.mark.subprocess(
    env={
        "DD_AGENTLESS_ENABLED": "true",
        "DD_API_KEY": "foobarkey",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
    }
)
def test_explicit_otlp_settings_win_over_agentless():
    """A user pointing OTLP at their own collector must keep it, agentless or not."""
    from ddtrace.internal.opentelemetry.metrics import _prepare_agentless_export
    from ddtrace.internal.settings import env
    from ddtrace.internal.settings._opentelemetry import otel_config

    assert otel_config.exporter.METRICS_PROTOCOL == "http/json"
    # ddtrace must not override the global endpoint the user set...
    _prepare_agentless_export(
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "OTEL_EXPORTER_OTLP_METRICS_HEADERS", "http/json", "metrics"
    )
    # ...nor attach Datadog credentials to a third-party collector.
    assert env.get("OTEL_EXPORTER_OTLP_METRICS_HEADERS") is None


@pytest.mark.subprocess(env={"DD_AGENTLESS_ENABLED": "true", "DD_API_KEY": "foobarkey"})
def test_agentless_otlp_export_carries_the_api_key():
    from ddtrace.internal.opentelemetry.metrics import _prepare_agentless_export
    from ddtrace.internal.settings import env

    _prepare_agentless_export(
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "OTEL_EXPORTER_OTLP_METRICS_HEADERS", "http/protobuf", "metrics"
    )
    assert env.get("OTEL_EXPORTER_OTLP_METRICS_HEADERS") == "dd-api-key=foobarkey"


@pytest.mark.subprocess(
    env={
        "DD_AGENTLESS_ENABLED": "true",
        "DD_API_KEY": "foobarkey",
        "OTEL_TRACES_SPAN_METRICS_ENABLED": "true",
        # Datadog stats are on by default under agentless and would take precedence.
        "DD_TRACE_STATS_COMPUTATION_ENABLED": "0",
    }
)
def test_otlp_trace_metrics_target_the_intake_when_agentless():
    """Client-computed span stats have no agent OTLP receiver to fall back on in agentless mode."""
    from ddtrace.internal.settings._opentelemetry import otel_config

    exporter = otel_config.exporter
    assert exporter.TRACE_METRICS_ENDPOINT == "https://otlp.datadoghq.com/v1/metrics"
    # The intake authenticates with the API key; the agent's receiver does not.
    assert "dd-api-key=foobarkey" in exporter.METRICS_HEADERS


@pytest.mark.subprocess(
    env={
        "DD_AGENTLESS_ENABLED": "true",
        "DD_API_KEY": "foobarkey",
        "OTEL_TRACES_SPAN_METRICS_ENABLED": "true",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318",
        # Datadog stats are on by default under agentless and would take precedence.
        "DD_TRACE_STATS_COMPUTATION_ENABLED": "0",
    }
)
def test_own_collector_keeps_trace_metrics_and_gets_no_credentials():
    from ddtrace.internal.settings._opentelemetry import otel_config

    exporter = otel_config.exporter
    assert exporter.TRACE_METRICS_ENDPOINT == "http://collector:4318/v1/metrics"
    assert "dd-api-key" not in exporter.METRICS_HEADERS


@pytest.mark.subprocess(env={"DD_API_KEY": "foobarkey", "OTEL_TRACES_SPAN_METRICS_ENABLED": "true"})
def test_otlp_trace_metrics_target_the_agent_without_agentless():
    from ddtrace.internal.settings._opentelemetry import otel_config

    exporter = otel_config.exporter
    assert "otlp.datadoghq.com" not in exporter.TRACE_METRICS_ENDPOINT
    assert "dd-api-key" not in exporter.METRICS_HEADERS


@pytest.mark.subprocess(
    env={
        "DD_AGENTLESS_ENABLED": "true",
        "DD_API_KEY": "foobarkey",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4317",
    }
)
def test_custom_collector_keeps_the_grpc_protocol_default():
    """Agentless switches the default to HTTP for the intake, which speaks nothing else.

    A collector the user pointed us at is commonly gRPC on 4317, so it keeps the standard default.
    """
    from ddtrace.internal.settings._opentelemetry import otel_config

    assert otel_config.exporter.METRICS_PROTOCOL == "grpc"
    assert otel_config.exporter.LOGS_PROTOCOL == "grpc"


@pytest.mark.subprocess(
    env={
        "DD_AGENTLESS_ENABLED": "true",
        "DD_API_KEY": "foobarkey",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "http://collector:4317",
    }
)
def test_signal_specific_collector_keeps_the_grpc_protocol_default():
    """Only the signal pointed elsewhere opts out; the others still target the intake."""
    from ddtrace.internal.settings._opentelemetry import otel_config

    assert otel_config.exporter.METRICS_PROTOCOL == "grpc"
    assert otel_config.exporter.LOGS_PROTOCOL == "http/protobuf"


@pytest.mark.subprocess(
    env={"DD_AGENTLESS_ENABLED": "true", "DD_API_KEY": "foobarkey", "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc"}
)
def test_explicit_protocol_survives_agentless():
    from ddtrace.internal.settings._opentelemetry import otel_config

    assert otel_config.exporter.METRICS_PROTOCOL == "grpc"


@pytest.mark.subprocess(
    env={
        "DD_AGENTLESS_ENABLED": "true",
        "DD_API_KEY": "foobarkey",
        "OTEL_EXPORTER_OTLP_METRICS_HEADERS": "x-team=apm,x-env=prod",
    }
)
def test_api_key_is_appended_to_custom_headers_for_the_intake():
    """The intake still needs authenticating; dropping the key over a custom header loses the data."""
    from ddtrace.internal.opentelemetry.metrics import _prepare_agentless_export
    from ddtrace.internal.settings import env

    _prepare_agentless_export(
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "OTEL_EXPORTER_OTLP_METRICS_HEADERS", "http/protobuf", "metrics"
    )
    assert env.get("OTEL_EXPORTER_OTLP_METRICS_HEADERS") == "x-team=apm,x-env=prod,dd-api-key=foobarkey"


@pytest.mark.subprocess(
    env={"DD_AGENTLESS_ENABLED": "true", "DD_API_KEY": "foobarkey", "OTEL_EXPORTER_OTLP_HEADERS": "x-team=apm"}
)
def test_global_custom_headers_are_carried_over_with_the_api_key():
    """Signal-specific headers replace the global ones, so they have to be copied across."""
    from ddtrace.internal.opentelemetry.metrics import _prepare_agentless_export
    from ddtrace.internal.settings import env

    _prepare_agentless_export(
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "OTEL_EXPORTER_OTLP_METRICS_HEADERS", "http/protobuf", "metrics"
    )
    assert env.get("OTEL_EXPORTER_OTLP_METRICS_HEADERS") == "x-team=apm,dd-api-key=foobarkey"


@pytest.mark.subprocess(
    env={
        "DD_AGENTLESS_ENABLED": "true",
        "DD_API_KEY": "foobarkey",
        "OTEL_EXPORTER_OTLP_METRICS_HEADERS": "dd-api-key=set-by-the-user",
    }
)
def test_a_user_supplied_api_key_header_is_left_alone():
    from ddtrace.internal.opentelemetry.metrics import _prepare_agentless_export
    from ddtrace.internal.settings import env

    _prepare_agentless_export(
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "OTEL_EXPORTER_OTLP_METRICS_HEADERS", "http/protobuf", "metrics"
    )
    assert env.get("OTEL_EXPORTER_OTLP_METRICS_HEADERS") == "dd-api-key=set-by-the-user"


@pytest.mark.subprocess(
    env={
        "DD_AGENTLESS_ENABLED": "true",
        "DD_API_KEY": "foobarkey",
        "OTEL_EXPORTER_OTLP_METRICS_HEADERS": "x-team=apm,x-env=prod",
    }
)
def test_signal_headers_keep_custom_entries_and_gain_the_api_key():
    """The native trace-metrics exporter reads these values directly, never the SDK helper.

    Without the key merged in here the intake rejects the metrics as unauthenticated.
    """
    from ddtrace.internal.settings._opentelemetry import otel_config

    assert otel_config.exporter.METRICS_HEADERS == "x-team=apm,x-env=prod,dd-api-key=foobarkey"


@pytest.mark.subprocess(
    env={
        "DD_AGENTLESS_ENABLED": "true",
        "DD_API_KEY": "foobarkey",
        "OTEL_EXPORTER_OTLP_HEADERS": "x-team=apm",
    }
)
def test_global_headers_are_kept_rather_than_replaced_by_the_api_key():
    from ddtrace.internal.settings._opentelemetry import otel_config

    assert otel_config.exporter.METRICS_HEADERS == "x-team=apm,dd-api-key=foobarkey"
    assert otel_config.exporter.LOGS_HEADERS == "x-team=apm,dd-api-key=foobarkey"


@pytest.mark.subprocess(
    env={
        "DD_AGENTLESS_ENABLED": "true",
        "DD_API_KEY": "foobarkey",
        "OTEL_EXPORTER_OTLP_METRICS_HEADERS": "dd-api-key=set-by-the-user",
    }
)
def test_an_api_key_the_user_set_is_not_duplicated():
    from ddtrace.internal.settings._opentelemetry import otel_config

    assert otel_config.exporter.METRICS_HEADERS == "dd-api-key=set-by-the-user"


@pytest.mark.subprocess(
    env={
        "DD_AGENTLESS_ENABLED": "true",
        "DD_API_KEY": "foobarkey",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318",
        "OTEL_EXPORTER_OTLP_HEADERS": "x-team=apm",
    }
)
def test_a_collector_of_your_own_gets_your_headers_and_no_api_key():
    from ddtrace.internal.settings._opentelemetry import otel_config

    assert otel_config.exporter.METRICS_HEADERS == "x-team=apm"
    assert "dd-api-key" not in otel_config.exporter.LOGS_HEADERS


@pytest.mark.subprocess(
    env={
        "DD_AGENTLESS_ENABLED": "true",
        "DD_API_KEY": "foobarkey",
        "OTEL_TRACES_SPAN_METRICS_ENABLED": "true",
        "DD_TRACE_STATS_COMPUTATION_ENABLED": "0",
        "OTEL_EXPORTER_OTLP_METRICS_HEADERS": "x-team=apm",
    }
)
def test_native_trace_metrics_exporter_sends_custom_headers_and_the_api_key():
    """End of the chain: what the exporter is handed, not just what the config derives."""
    from ddtrace.internal.settings._opentelemetry import otel_config
    from ddtrace.internal.writer.writer import NativeWriter
    from ddtrace.trace import tracer

    writer = tracer._span_aggregator.writer
    assert writer._otlp_metrics_endpoint == "https://otlp.datadoghq.com/v1/metrics"

    # This is the value, and the parse of it, that _create_exporter hands to libdatadog.
    headers = dict(NativeWriter._parse_otlp_headers(otel_config.exporter.METRICS_HEADERS))
    assert headers["x-team"] == "apm"
    assert headers["dd-api-key"] == "foobarkey"
