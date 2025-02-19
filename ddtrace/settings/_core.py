import os
from typing import Any  # noqa:F401
from typing import Callable  # noqa:F401
from typing import List  # noqa:F401
from typing import Optional  # noqa:F401
from typing import Union  # noqa:F401

from envier.env import EnvVariable
from envier.env import _normalized

from ddtrace.internal.native import get_configuration_from_disk
from ddtrace.internal.telemetry import telemetry_writer

from ._otel_remapper import hiding_otel_config
from ._otel_remapper import parse_otel_env


FLEET_CONFIG, LOCAL_CONFIG = get_configuration_from_disk()


def report_telemetry(env: Any) -> None:
    for name, e in list(env.__class__.__dict__.items()):
        if isinstance(e, EnvVariable) and not e.private:
            env_name = env._full_prefix + _normalized(e.name)
            env_val = e(env, env._full_prefix)
            raw_val = env.source.get(env_name)
            if env_name in env.source and env_val == e._cast(e.type, raw_val, env):
                source = "env_var"
            elif env_val == e.default:
                source = "default"
            else:
                source = "unknown"
            telemetry_writer.add_configuration(env_name, env_val, source)


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
    elif effective_env is not None and otel_env is not None and otel_env in os.environ:
        hiding_otel_config(otel_env, effective_env)
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
        if effective_env == otel_env:
            # We only report the raw value for OpenTelemetry configurations, we should make this consistent
            raw_val = os.environ.get(effective_env, "").lower()
            telemetry_writer.add_configuration(effective_env, raw_val, source)
        else:
            telemetry_writer.add_configuration(effective_env, val, source)
    return val
