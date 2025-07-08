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
from ddtrace.settings._otel_remapper import parse_otel_env


log = get_logger(__name__)

__all__ = ["telemetry_writer"]


def report_config_telemetry(effective_env, val, source, otel_env, config_id):
    if effective_env == otel_env:
        # We only report the raw value for OpenTelemetry configurations, we should make this consistent
        raw_val = os.environ.get(effective_env, "").lower()
        telemetry_writer.add_configuration(effective_env, raw_val, source)
    else:
        if otel_env is not None and otel_env in os.environ:
            if source in ("fleet_stable_config", "env_var"):
                _hiding_otel_config(otel_env, effective_env)
            else:
                _invalid_otel_config(otel_env)
        telemetry_writer.add_configuration(effective_env, val, source, config_id)


def get_config(
    envs: t.Union[str, t.List[str]],
    default: t.Any = None,
    modifier: t.Optional[t.Callable[[t.Any], t.Any]] = None,
    otel_env: t.Optional[str] = None,
    report_telemetry=True,
) -> t.Any:
    """Retrieve a configuration value in order of precedence:
    1. Fleet stable config
    2. Datadog env vars
    3. OpenTelemetry env vars
    4. Local stable config
    5. Default value
    """
    if isinstance(envs, str):
        envs = [envs]
    source = ""
    effective_env = ""
    val = None
    config_id = None
    # Get configurations from fleet stable config
    for env in envs:
        if env in FLEET_CONFIG:
            source = "fleet_stable_config"
            effective_env = env
            val = FLEET_CONFIG[env]
            config_id = FLEET_CONFIG_IDS.get(env)
            break
    # Get configurations from datadog env vars
    if val is None:
        for env in envs:
            if env in os.environ:
                source = "env_var"
                effective_env = env
                val = os.environ[env]
                break
    # Get configurations from otel env vars
    if val is None:
        if otel_env is not None and otel_env in os.environ:
            parsed_val = parse_otel_env(otel_env)
            if parsed_val is not None:
                source = "env_var"
                effective_env = otel_env
                val = parsed_val
    # Get configurations from local stable config
    if val is None:
        for env in envs:
            if env in LOCAL_CONFIG:
                source = "local_stable_config"
                effective_env = env
                val = LOCAL_CONFIG[env]
                break
    # Convert the raw value to expected format, if a modifier is provided
    if val is not None and modifier:
        val = modifier(val)
    # If no value is found, use the default
    if val is None:
        effective_env = envs[0]
        val = default
        source = "default"
    # Report telemetry
    if report_telemetry:
        report_config_telemetry(effective_env, val, source, otel_env, config_id)

    return val


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
            and otel_env not in ("OTEL_PYTHON_CONTEXT", "OTEL_TRACES_SAMPLER_ARG", "OTEL_LOGS_EXPORTER")
        ):
            _unsupported_otel_config(otel_env)
        elif otel_env == "OTEL_LOGS_EXPORTER":
            # check for invalid values
            otel_value = os.environ.get(otel_env, "none").lower()
            if otel_value != "none":
                _invalid_otel_config(otel_env)
            # TODO: Separate from validation
            telemetry_writer.add_configuration(otel_env, otel_value, "env_var")


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
