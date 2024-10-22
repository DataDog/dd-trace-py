import os
from typing import Any  # noqa:F401
from typing import Callable  # noqa:F401
from typing import List  # noqa:F401
from typing import Optional  # noqa:F401
from typing import Union  # noqa:F401

from envier import Env

from ddtrace.internal.telemetry import telemetry_writer


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

            source = "unknown"
            if env_name in self.source:
                source = "env_var"
            else:
                if env_val == e.default:
                    source = "default"

            telemetry_writer.add_configuration(env_name, env_val, source)


def report_telemetry(env: Env) -> None:
    return Config._report_telemetry(env)


def get_config(
    envs: Union[str, List[str]],
    default: Any = None,
    modifier: Optional[Callable[[Any], Any]] = None,
    report_telemetry=True,
):
    if isinstance(envs, str):
        envs = [envs]
    val = default
    source = "default"
    effective_env = envs[0]
    for env in envs:
        if env in os.environ:
            val = os.environ[env]
            if modifier:
                val = modifier(val)
            source = "env_var"
            effective_env = env
            break
    if report_telemetry:
        telemetry_writer.add_configuration(effective_env, val, source)
    return val
