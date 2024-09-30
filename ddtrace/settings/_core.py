from envier.env import EnvVariable
from envier.env import _normalized

from ddtrace.internal.telemetry import telemetry_writer


def report_telemetry(env):
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
            telemetry_writer.add_configuration(env_name, str(env_val), source)
