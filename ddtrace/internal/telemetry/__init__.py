"""
Instrumentation Telemetry API.
This is normally started automatically when ``ddtrace`` is imported. It can be disabled by setting
``DD_INSTRUMENTATION_TELEMETRY_ENABLED`` variable to ``False``.
"""

from ddtrace.internal import core
from ddtrace.internal.getconfig import _invalid_otel_config
from ddtrace.internal.getconfig import get_config
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.internal.settings._core import DDConfig
from ddtrace.internal.settings._otel_remapper import ENV_VAR_MAPPINGS
from ddtrace.internal.settings._otel_remapper import SUPPORTED_OTEL_ENV_VARS
from ddtrace.internal.settings._supported_configurations import SENSITIVE_CONFIGURATIONS
from ddtrace.internal.settings.process_tags import process_tags_config
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.utils.formats import asbool


log = get_logger(__name__)

__all__ = ["telemetry_writer"]


def _add_conf(*args):
    telemetry_writer.add_configuration(*args)


def _add_count(*args):
    telemetry_writer.add_count_metric(*args)


core.on("telemetry.add_configuration", _add_conf)
core.on("telemetry.add_count_metric", _add_count)


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

        # Configurations marked ``sensitive: true`` in the registry are excluded
        # from configuration telemetry.
        if env_name in SENSITIVE_CONFIGURATIONS:
            continue

        # Get the item value recursively
        env_val = config
        for p in name.split("."):
            env_val = getattr(env_val, p)

        core.dispatch(
            "telemetry.add_configuration", (env_name, env_val, config.value_source(env_name), config.config_id)
        )


def _unsupported_otel_config(otel_env):
    log.warning("OpenTelemetry configuration %s is not supported by Datadog.", otel_env)
    core.dispatch(
        "telemetry.add_count_metric",
        (
            TELEMETRY_NAMESPACE.TRACERS,
            "otel.env.unsupported",
            1,
            (("config_opentelemetry", otel_env.lower()),),
        ),
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
        elif otel_env == "OTEL_LOGS_EXPORTER":
            # check for invalid values
            otel_value = env.get(otel_env, "none").lower()
            if otel_value != "none":
                _invalid_otel_config(otel_env)
            core.dispatch("telemetry.add_configuration", (otel_env, otel_value, "env_var"))
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

        core.dispatch("telemetry.add_configuration", ("OTEL_METRICS_EXPORTER", otel_value, "env_var"))

    return metrics_exporter_enabled


# TODO: Remove this once the telemetry feature is refactored to a better design
report_configuration(agent_config)
report_configuration(process_tags_config)
