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

from ._otel_remapper import parse_otel_env
from ._otel_remapper import should_parse_otel_env


STABLE_CONFIGS = get_configuration_from_disk()


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
):
    if isinstance(envs, str):
        envs = [envs]
    val = None
    # Get configuration from stable config
    for env in envs:
        entry = STABLE_CONFIGS.get(env)
        if entry and (
            entry["source"] == "fleet_stable_config"
            or (entry["source"] == "local_stable_config" and entry["name"] not in list(os.environ.keys()))
        ):
            val = entry["value"]
            if modifier:
                val = modifier(val)
            if report_telemetry:
                telemetry_writer.add_configuration(env, val, entry["source"])
    # Get configuration from otel env vars
    if otel_env and should_parse_otel_env(otel_env):
        parsed_val = parse_otel_env(otel_env)
        if parsed_val is not None:
            val = parsed_val
            if modifier:
                val = modifier(val)
            if report_telemetry:
                raw_value = os.environ.get(otel_env, "").lower()
                telemetry_writer.add_configuration(otel_env, raw_value, "env_var")
    # Get configuration from datadog env vars
    if val is None:
        for env in envs:
            if env in os.environ:
                val = os.environ[env]
                if modifier:
                    val = modifier(val)
                if report_telemetry:
                    telemetry_writer.add_configuration(env, val, "env_var")
                break
    # Use default value to set configuration
    if val is None:
        val = default
        if report_telemetry:
            telemetry_writer.add_configuration(envs[0], val, "default")

    return val
