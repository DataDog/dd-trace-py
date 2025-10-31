"""
Instrumentation Telemetry API.
This is normally started automatically when ``ddtrace`` is imported. It can be disabled by setting
``DD_INSTRUMENTATION_TELEMETRY_ENABLED`` variable to ``False``.
"""

import os
import typing as t

from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.utils.formats import asbool
from ddtrace.settings._agent import config as agent_config
from ddtrace.settings._core import FLEET_CONFIG
from ddtrace.settings._core import FLEET_CONFIG_IDS
from ddtrace.settings._core import LOCAL_CONFIG
from ddtrace.settings._core import DDConfig
from ddtrace.settings._otel_remapper import ENV_VAR_MAPPINGS
from ddtrace.settings._otel_remapper import SUPPORTED_OTEL_ENV_VARS
from ddtrace.settings._otel_remapper import parse_otel_env


log = get_logger(__name__)

__all__ = ["telemetry_writer"]


def get_config(
    envs: t.Union[str, t.List[str]],
    default: t.Any = None,
    modifier: t.Optional[t.Callable[[t.Any], t.Any]] = None,
    otel_env: t.Optional[str] = None,
    report_telemetry=True,
) -> t.Any:
    """Retrieve a configuration value in order of precedence:
    1. Fleet stable config (highest)
    2. Datadog env vars
    3. OpenTelemetry env vars
    4. Local stable config
    5. Default value (lowest)

    Reports telemetry for every detected configuration source.
    """
    if isinstance(envs, str):
        envs = [envs]

    effective_val = default
    telemetry_name = envs[0]
    if report_telemetry:
        telemetry_writer.add_configuration(telemetry_name, default, "default")

    for env in envs:
        if env in LOCAL_CONFIG:
            val = LOCAL_CONFIG[env]
            if modifier:
                val = modifier(val)

            if report_telemetry:
                telemetry_writer.add_configuration(telemetry_name, val, "local_stable_config")
            effective_val = val
            break

    if otel_env is not None and otel_env in os.environ:
        raw_val, parsed_val = parse_otel_env(otel_env)
        if parsed_val is not None:
            val = parsed_val
            if modifier:
                val = modifier(val)

            if report_telemetry:
                # OpenTelemetry configurations always report the raw value
                telemetry_writer.add_configuration(telemetry_name, raw_val, "otel_env_var")
            effective_val = val
        else:
            _invalid_otel_config(otel_env)

    for env in envs:
        if env in os.environ:
            val = os.environ[env]
            if modifier:
                val = modifier(val)

            if report_telemetry:
                telemetry_writer.add_configuration(telemetry_name, val, "env_var")
                if otel_env is not None and otel_env in os.environ:
                    _hiding_otel_config(otel_env, env)
            effective_val = val
            break

    for env in envs:
        if env in FLEET_CONFIG:
            val = FLEET_CONFIG[env]
            config_id = FLEET_CONFIG_IDS.get(env)
            if modifier:
                val = modifier(val)

            if report_telemetry:
                telemetry_writer.add_configuration(telemetry_name, val, "fleet_stable_config", config_id)
                if otel_env is not None and otel_env in os.environ:
                    _hiding_otel_config(otel_env, env)
            effective_val = val
            break

    return effective_val


telemetry_enabled = get_config("DD_INSTRUMENTATION_TELEMETRY_ENABLED", True, asbool, report_telemetry=False)
if telemetry_enabled:
    from .writer import TelemetryWriter
else:
    from .noop_writer import NoOpTelemetryWriter as TelemetryWriter  # type: ignore[assignment]

telemetry_writer = TelemetryWriter()


def report_configuration(config: DDConfig) -> None:
    for name, e in type(config).items(recursive=True):
        if e.private:
            continue

        env_name = e.full_name

        # Get the item value recursively
        env_val = config
        for p in name.split("."):
            env_val = getattr(env_val, p)

        telemetry_writer.add_configuration(env_name, env_val, config.value_source(env_name), config.config_id)


def _invalid_otel_config(otel_env):
    log.warning(
        "Setting %s to %s is not supported by ddtrace, this configuration will be ignored.",
        otel_env,
        os.environ.get(otel_env, ""),
    )
    telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE.TRACERS,
        "otel.env.invalid",
        1,
        (("config_opentelemetry", otel_env.lower()),),
    )


def _unsupported_otel_config(otel_env):
    log.warning("OpenTelemetry configuration %s is not supported by Datadog.", otel_env)
    telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE.TRACERS,
        "otel.env.unsupported",
        1,
        (("config_opentelemetry", otel_env.lower()),),
    )


def validate_otel_envs():
    user_envs = {key.upper(): value for key, value in os.environ.items()}
    for otel_env, _ in user_envs.items():
        if (
            otel_env not in ENV_VAR_MAPPINGS
            and otel_env.startswith("OTEL_")
            and otel_env not in SUPPORTED_OTEL_ENV_VARS
        ):
            _unsupported_otel_config(otel_env)
        elif otel_env == "OTEL_LOGS_EXPORTER":
            # check for invalid values
            otel_value = os.environ.get(otel_env, "none").lower()
            if otel_value != "none":
                _invalid_otel_config(otel_env)
            # TODO: Separate from validation
            telemetry_writer.add_configuration(otel_env, otel_value, "env_var")
        elif otel_env == "OTEL_METRICS_EXPORTER":
            # defer validation to validate_and_report_otel_metrics_exporter_enabled
            pass


def validate_and_report_otel_metrics_exporter_enabled():
    metrics_exporter_enabled = True
    user_envs = {key.upper(): value for key, value in os.environ.items()}
    if "OTEL_METRICS_EXPORTER" in user_envs:
        otel_value = os.environ.get("OTEL_METRICS_EXPORTER", "otlp").lower()
        if otel_value == "none":
            metrics_exporter_enabled = False
        elif otel_value != "otlp":
            _invalid_otel_config("OTEL_METRICS_EXPORTER")

        # Report to configuration telemetry
        telemetry_writer.add_configuration("OTEL_METRICS_EXPORTER", otel_value, "env_var")

    return metrics_exporter_enabled


def _hiding_otel_config(otel_env, dd_env):
    log.debug(
        "Datadog configuration %s is already set. OpenTelemetry configuration will be ignored: %s=%s",
        dd_env,
        otel_env,
        os.environ[otel_env],
    )
    telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE.TRACERS,
        "otel.env.hiding",
        1,
        (("config_opentelemetry", otel_env.lower()), ("config_datadog", dd_env.lower())),
    )


# TODO: Remove this once the telemetry feature is refactored to a better design
report_configuration(agent_config)
