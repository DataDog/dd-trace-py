import os
from typing import Any  # noqa:F401

from ddtrace.settings._core import DDConfig as Env

from ..internal.logger import get_logger
from ..internal.telemetry import telemetry_writer
from ..internal.telemetry.constants import TELEMETRY_NAMESPACE
from ._otel_remapper import ENV_VAR_MAPPINGS


log = get_logger(__name__)


class Config(Env):
    """Env-based configuration sub-class for automatic telemetry reporting."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._report_telemetry()

    def _report_telemetry(self) -> None:
        for name, e in type(self).items(recursive=True):
            if e.private:
                continue

            env_name = e.full_name

            # Get the item value recursively
            env_val = self
            for p in name.split("."):
                env_val = getattr(env_val, p)

            if isinstance(self, Env) and env_name in self.fleet_source:
                source = "fleet_stable_config"
            elif env_name in os.environ:
                source = "env_var"
            elif isinstance(self, Env) and env_name in self.local_source:
                source = "local_stable_config"
            elif env_name in self.source:
                source = "code"
            elif env_val == e.default:
                source = "default"
            else:
                source = "unknown"

            telemetry_writer.add_configuration(env_name, env_val, source)


def report_telemetry(env: Env) -> None:
    Config._report_telemetry(env)


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


def report_config_telemetry(effective_env, val, source, otel_env):
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
        telemetry_writer.add_configuration(effective_env, val, source)
