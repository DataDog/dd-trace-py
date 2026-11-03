import typing as t

from ddtrace.internal.forksafe import RLock
from ddtrace.internal.settings import env
from ddtrace.internal.settings._otel_remapper import parse_otel_env
from ddtrace.internal.settings._supported_configurations import CONFIGURATION_ALIASES
from ddtrace.internal.settings._supported_configurations import SENSITIVE_CONFIGURATIONS


_configuration_lock = RLock()
_pending_configurations: list[tuple[t.Any, ...]] = []
_configuration_reporter: t.Optional[t.Callable[..., None]] = None


def record_configuration(*configuration: t.Any) -> None:
    """Retain startup configuration until telemetry attaches its reporter."""
    with _configuration_lock:
        if _configuration_reporter is None:
            _pending_configurations.append(configuration)
        else:
            _configuration_reporter(*configuration)


def set_configuration_reporter(reporter: t.Callable[..., None]) -> None:
    global _configuration_reporter
    with _configuration_lock:
        _configuration_reporter = reporter
        for configuration in _pending_configurations:
            reporter(*configuration)
        _pending_configurations.clear()


def resolve_config(
    envs: t.Union[str, list[str]],
    default: t.Any = None,
    modifier: t.Optional[t.Callable[[t.Any], t.Any]] = None,
    otel_env: t.Optional[t.Union[str, list[str]]] = None,
    report_configuration: t.Optional[t.Callable[..., None]] = None,
    invalid_otel: t.Optional[t.Callable[[str], None]] = None,
    hiding_otel: t.Optional[t.Callable[[str, str], None]] = None,
) -> t.Any:
    """Resolve fleet > Datadog env > OpenTelemetry env > local > default configuration.

    Report detected sources through callbacks without starting telemetry.
    """
    from ddtrace.internal.settings._core import FLEET_CONFIG
    from ddtrace.internal.settings._core import FLEET_CONFIG_IDS
    from ddtrace.internal.settings._core import LOCAL_CONFIG

    if isinstance(envs, str):
        envs = [envs]
    otel_envs = [otel_env] if isinstance(otel_env, str) else otel_env or []

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
        report_configuration = None
    if report_configuration is not None:
        report_configuration(telemetry_name, default, "default")

    for env_name in envs:
        if env_name in LOCAL_CONFIG:
            val = LOCAL_CONFIG[env_name]
            if modifier:
                val = modifier(val)

            if report_configuration is not None:
                report_configuration(telemetry_name, val, "local_stable_config")
            effective_val = val
            break

    for otel_env_name in otel_envs:
        if otel_env_name in env:
            raw_val, parsed_val = parse_otel_env(otel_env_name)
            if parsed_val is not None:
                val = parsed_val
                if modifier:
                    val = modifier(val)

                if report_configuration is not None:
                    # OpenTelemetry configurations always report the raw value
                    report_configuration(telemetry_name, raw_val, "otel_env_var")
                effective_val = val
            elif invalid_otel is not None:
                invalid_otel(otel_env_name)

    for env_name in envs:
        if env_name in env:
            val = env[env_name]
            if modifier:
                val = modifier(val)

            if report_configuration is not None:
                report_configuration(telemetry_name, val, "env_var")
                for otel_env_name in otel_envs:
                    if otel_env_name in env and hiding_otel is not None:
                        hiding_otel(otel_env_name, env_name)
            effective_val = val
            break

    for env_name in envs:
        if env_name in FLEET_CONFIG:
            val = FLEET_CONFIG[env_name]
            config_id = FLEET_CONFIG_IDS.get(env_name)
            if modifier:
                val = modifier(val)

            if report_configuration is not None:
                report_configuration(telemetry_name, val, "fleet_stable_config", config_id)
                for otel_env_name in otel_envs:
                    if otel_env_name in env and hiding_otel is not None:
                        hiding_otel(otel_env_name, env_name)
            effective_val = val
            break

    return effective_val
