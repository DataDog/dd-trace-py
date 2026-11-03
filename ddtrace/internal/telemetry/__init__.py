"""
Instrumentation Telemetry API.
This starts when instrumentation or configuration requiring telemetry is initialized. It can be disabled by setting
``DD_INSTRUMENTATION_TELEMETRY_ENABLED`` variable to ``False``.
"""

import typing as t

from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.internal.settings._core import FLEET_CONFIG  # noqa: F401
from ddtrace.internal.settings._core import FLEET_CONFIG_IDS  # noqa: F401
from ddtrace.internal.settings._core import LOCAL_CONFIG  # noqa: F401
from ddtrace.internal.settings._core import DDConfig
from ddtrace.internal.settings._otel_remapper import ENV_VAR_MAPPINGS
from ddtrace.internal.settings._otel_remapper import SUPPORTED_OTEL_ENV_VARS
from ddtrace.internal.settings._read_config import resolve_config
from ddtrace.internal.settings._read_config import set_configuration_reporter
from ddtrace.internal.settings._supported_configurations import CONFIGURATION_ALIASES  # noqa: F401
from ddtrace.internal.settings._supported_configurations import SENSITIVE_CONFIGURATIONS
from ddtrace.internal.settings.appsec_telemetry import config as appsec_telemetry_config
from ddtrace.internal.settings.process_tags import process_tags_config
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.utils.formats import asbool


log = get_logger(__name__)

__all__ = ["telemetry_writer"]


def get_config(
    envs: t.Union[str, list[str]],
    default: t.Any = None,
    modifier: t.Optional[t.Callable[[t.Any], t.Any]] = None,
    otel_env: t.Optional[t.Union[str, list[str]]] = None,
    report_telemetry=True,
) -> t.Any:
    return resolve_config(
        envs,
        default,
        modifier,
        otel_env,
        report_configuration=telemetry_writer.add_configuration if report_telemetry else None,
        invalid_otel=lambda name: _invalid_otel_config(name),
        hiding_otel=lambda name, dd_name: _hiding_otel_config(name, dd_name),
    )


telemetry_enabled = get_config("DD_INSTRUMENTATION_TELEMETRY_ENABLED", True, asbool, report_telemetry=False)
if telemetry_enabled:
    from .writer import TelemetryWriter
else:
    from .noop_writer import NoOpTelemetryWriter as TelemetryWriter  # type: ignore[assignment]

telemetry_writer = TelemetryWriter()
set_configuration_reporter(lambda *configuration: telemetry_writer.add_configuration(*configuration))


def report_configuration(config: DDConfig) -> None:
    for name, e in type(config).items(recursive=True):
        if e.private:
            continue

        env_name = e.full_name

        # Configurations marked ``sensitive: true`` in the registry are excluded
        # from configuration telemetry.
        if env_name in SENSITIVE_CONFIGURATIONS:
            continue

        # Get the item value recursively
        env_val = config
        for p in name.split("."):
            env_val = getattr(env_val, p)

        telemetry_writer.add_configuration(env_name, env_val, config.value_source(env_name), config.config_id(env_name))


def _invalid_otel_config(otel_env):
    log.warning(
        "Setting %s to %s is not supported by ddtrace, this configuration will be ignored.",
        otel_env,
        env.get(otel_env, ""),
    )
    telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE.TRACERS,
        "otel.env.invalid",
        1,
        (("config_opentelemetry", otel_env.lower()),),
    )


def _unsupported_otel_config(otel_env):
    log.debug("OpenTelemetry configuration %s is not recognized by ddtrace", otel_env)
    telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE.TRACERS,
        "otel.env.unsupported",
        1,
        (("config_opentelemetry", otel_env.lower()),),
    )


def validate_otel_envs():
    user_envs = {key.upper(): value for key, value in env.items()}
    for otel_env, _ in user_envs.items():
        if (
            otel_env not in ENV_VAR_MAPPINGS
            and otel_env.startswith("OTEL_")
            and otel_env not in SUPPORTED_OTEL_ENV_VARS
        ):
            _unsupported_otel_config(otel_env)
        elif otel_env == "OTEL_METRICS_EXPORTER":
            # defer validation to validate_and_report_otel_metrics_exporter_enabled
            pass


def validate_and_report_otel_metrics_exporter_enabled():
    metrics_exporter_enabled = True
    user_envs = {key.upper(): value for key, value in env.items()}
    if "OTEL_METRICS_EXPORTER" in user_envs:
        otel_value = env.get("OTEL_METRICS_EXPORTER", "otlp").lower()
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
        env[otel_env],
    )
    telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE.TRACERS,
        "otel.env.hiding",
        1,
        (("config_opentelemetry", otel_env.lower()), ("config_datadog", dd_env.lower())),
    )


# TODO: Remove this once the telemetry feature is refactored to a better design
report_configuration(appsec_telemetry_config)
report_configuration(agent_config)
report_configuration(process_tags_config)
