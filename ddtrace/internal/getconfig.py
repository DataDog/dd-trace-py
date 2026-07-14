import typing as t

from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
from ddtrace.internal.settings._core import FLEET_CONFIG
from ddtrace.internal.settings._core import FLEET_CONFIG_IDS
from ddtrace.internal.settings._core import LOCAL_CONFIG
from ddtrace.internal.settings._otel_remapper import parse_otel_env
from ddtrace.internal.settings._supported_configurations import CONFIGURATION_ALIASES
from ddtrace.internal.settings._supported_configurations import SENSITIVE_CONFIGURATIONS
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


log = get_logger(__name__)


def get_config(
    envs: t.Union[str, list[str]],
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

    # Expand with registered aliases of the canonical name (envs[0]) so all
    # config sources (LOCAL_CONFIG, env, FLEET_CONFIG) honor legacy renames.
    canonical = envs[0]
    aliases = CONFIGURATION_ALIASES.get(canonical, [])
    if aliases:
        envs += [a for a in aliases if a not in envs]

    effective_val = default
    telemetry_name = envs[0]
    # Configurations marked ``sensitive: true`` in the registry are excluded from
    # configuration telemetry, regardless of which source supplies the value.
    if telemetry_name in SENSITIVE_CONFIGURATIONS:
        report_telemetry = False
    if report_telemetry:
        core.dispatch("telemetry.add_configuration", (telemetry_name, default, "default"))

    for env_name in envs:
        if env_name in LOCAL_CONFIG:
            val = LOCAL_CONFIG[env_name]
            if modifier:
                val = modifier(val)

            if report_telemetry:
                core.dispatch("telemetry.add_configuration", (telemetry_name, val, "local_stable_config"))
            effective_val = val
            break

    if otel_env is not None and otel_env in env:
        raw_val, parsed_val = parse_otel_env(otel_env)
        if parsed_val is not None:
            val = parsed_val
            if modifier:
                val = modifier(val)

            if report_telemetry:
                # OpenTelemetry configurations always report the raw value
                core.dispatch("telemetry.add_configuration", (telemetry_name, raw_val, "otel_env_var"))
            effective_val = val
        else:
            _invalid_otel_config(otel_env)

    for env_name in envs:
        if env_name in env:
            val = env[env_name]
            if modifier:
                val = modifier(val)

            if report_telemetry:
                core.dispatch("telemetry.add_configuration", (telemetry_name, val, "env_var"))
                if otel_env is not None and otel_env in env:
                    _hiding_otel_config(otel_env, env_name)
            effective_val = val
            break

    for env_name in envs:
        if env_name in FLEET_CONFIG:
            val = FLEET_CONFIG[env_name]
            config_id = FLEET_CONFIG_IDS.get(env_name)
            if modifier:
                val = modifier(val)

            if report_telemetry:
                core.dispatch("telemetry.add_configuration", (telemetry_name, val, "fleet_stable_config", config_id))
                if otel_env is not None and otel_env in env:
                    _hiding_otel_config(otel_env, env_name)
            effective_val = val
            break

    return effective_val


def _invalid_otel_config(otel_env):
    log.warning(
        "Setting %s to %s is not supported by ddtrace, this configuration will be ignored.",
        otel_env,
        env.get(otel_env, ""),
    )
    core.dispatch(
        "telemetry.add_count_metric",
        (
            TELEMETRY_NAMESPACE.TRACERS,
            "otel.env.invalid",
            1,
            (("config_opentelemetry", otel_env.lower()),),
        ),
    )


def _hiding_otel_config(otel_env, dd_env):
    log.debug(
        "Datadog configuration %s is already set. OpenTelemetry configuration will be ignored: %s=%s",
        dd_env,
        otel_env,
        env[otel_env],
    )
    core.dispatch(
        "telemetry.add_count_metric",
        (
            TELEMETRY_NAMESPACE.TRACERS,
            "otel.env.hiding",
            1,
            (("config_opentelemetry", otel_env.lower()), ("config_datadog", dd_env.lower())),
        ),
    )
