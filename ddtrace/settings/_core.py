import os
from typing import Any  # noqa:F401
from typing import Callable  # noqa:F401
from typing import List  # noqa:F401
from typing import Optional  # noqa:F401
from typing import Union  # noqa:F401

from ddtrace.internal.native import get_configuration_from_disk

from ._otel_remapper import parse_otel_env


FLEET_CONFIG, LOCAL_CONFIG = get_configuration_from_disk()


def get_config(
    envs: Union[str, List[str]],
    default: Any = None,
    modifier: Optional[Callable[[Any], Any]] = None,
    otel_env: Optional[str] = None,
    report_telemetry=True,
) -> Any:
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
    # Get configurations from fleet stable config
    for env in envs:
        if env in FLEET_CONFIG:
            source = "fleet_stable_config"
            effective_env = env
            val = FLEET_CONFIG[env]["value"]
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
                val = LOCAL_CONFIG[env]["value"]
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
        from ddtrace.settings._telemetry import report_config_telemetry

        report_config_telemetry(effective_env, val, source, otel_env)

    return val
